#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
远程服务器部署服务 —— 生产/测试环境定时部署执行

流程概览：
1. 查询所有 prod/test 环境 pending/publishing 状态的发布记录
2. 按 client_id（prod）或 chat_id（test）分组：publishing 跳过，pending 取消旧记录、部署最新
3. 部署步骤：commit 补充 → SSH 检查 → 目录检查 → Docker 容器部署 → Nginx 路由

日志约定：所有 logger 调用第一个位置参数填充 trace_id，输出形如
`[trace_id=xxx] ...`，便于按链路聚合。
"""

import logging
import os
import re
import uuid
from urllib.parse import urlparse

from dao.deploy_dao import get_pending_deploy_records, update_deploy_record_status, batch_cancel_deploy_records
from dao.client_dao import get_client_repos, get_client_deploys, get_client_servers, get_client_domains
from dao.models import DeployRecord, ClientDeploy
from service.deploy_service import generate_deploy_toml
from service.deploy_route_prefix import pairs_from_deploys
from dao.chat_dao import batch_get_msg_by_msgids
from utils.git_utils import parse_github_url, get_branch_latest_commit
from utils.ssh_utils import SshClient
from utils.git_remote_utils import (
    GitRemoteError,
    build_auth_url,
    is_git_auth_error,
    clone_repo_with_retry,
    fetch_or_reclone,
    checkout_commit_with_auth_refresh,
)

logger = logging.getLogger(__name__)

_ENV_INIT_SCRIPT_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'utils', 'server_env_init.sh'))


class RemoteDeployError(Exception):
    """远程部署执行失败"""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ============================================================
# 主入口
# ============================================================

def process_pending_deploys_prod(client_id: int):
    """
    处理指定应用 prod 环境的待发布记录（供调度器调用）。

    - 存在 publishing 记录 → 跳过
    - 仅 pending → 取消旧记录，部署最新
    """
    trace_id = str(uuid.uuid4())
    records = get_pending_deploy_records(client_id=client_id, env='prod')
    if not records:
        return

    if any(r.status == DeployRecord.STATUS_PUBLISHING for r in records):
        logger.info("[trace_id=%s] client_id=%s has publishing record, skip", trace_id, client_id)
        return

    publish_record, merge_request = _pick_latest_and_cancel_older(
        records=records, trace_id=trace_id, scope_desc=f'client_id={client_id}',
    )
    if not publish_record:
        return

    _run_deploy_with_failure_handler(
        record=publish_record, trace_id=trace_id, host_key='', merge_request=merge_request,
        scope_desc=f'client_id={client_id}',
    )


def process_pending_deploys_test(client_id: int):
    """处理指定应用 test 环境的待发布记录（按 chat_id 分组）。"""
    records = get_pending_deploy_records(client_id=client_id, env='test')
    if not records:
        return

    chat_records: dict[int, list] = {}
    for record in records:
        chat_records.setdefault(record.chat_id, []).append(record)

    for chat_id, chat_deploy_records in chat_records.items():
        trace_id = str(uuid.uuid4())
        scope_desc = f'client_id={client_id}, chat_id={chat_id}'

        if any(r.status == DeployRecord.STATUS_PUBLISHING for r in chat_deploy_records):
            logger.info("[trace_id=%s] %s has publishing record, skip", trace_id, scope_desc)
            continue

        publish_record, merge_request = _pick_latest_and_cancel_older(
            records=chat_deploy_records, trace_id=trace_id, scope_desc=scope_desc,
        )
        if not publish_record:
            continue

        host_key = (
            f'task{publish_record.task_id}'
            f'chat{publish_record.chat_id}'
            f'msg{publish_record.msg_id}'
        )
        _run_deploy_with_failure_handler(
            record=publish_record, trace_id=trace_id, host_key=host_key,
            merge_request=merge_request, scope_desc=scope_desc,
        )


def _pick_latest_and_cancel_older(records: list, trace_id: str, scope_desc: str) -> tuple:
    """从 pending 记录中选出最新待部署记录，其余标记为 cancel。

    Returns:
        (publish_record, merge_request)，若无可部署记录则 (None, None)
    """
    pending = [r for r in records if r.status == DeployRecord.STATUS_PENDING]
    if not pending:
        return None, None

    # 创建时间降序，最新在前
    pending.sort(key=lambda r: r.created_at, reverse=True)
    msg_ids = [r.msg_id for r in pending]
    msgs = batch_get_msg_by_msgids(user_id=pending[0].user_id, msg_ids=msg_ids)
    msg_dict = {m.id: m for m in msgs}

    publish_record = None
    merge_request = None
    cancel_ids = []
    for deploy_record in pending:
        msg = msg_dict.get(deploy_record.msg_id)
        if not msg:
            continue
        mr = (msg.extra or {}).get('merge_request')
        if not mr:
            continue
        if not publish_record:
            publish_record = deploy_record
            merge_request = mr
        else:
            cancel_ids.append(deploy_record.id)

    if cancel_ids:
        cancel_ids_str = ','.join(str(rid) for rid in cancel_ids)
        logger.info(
            "[trace_id=%s] %s: cancelled older pending deploy record_ids=[%s] (count=%d)",
            trace_id, scope_desc, cancel_ids_str, len(cancel_ids),
        )
        batch_cancel_deploy_records(record_ids=cancel_ids)

    return publish_record, merge_request


def _run_deploy_with_failure_handler(
    record: DeployRecord, trace_id: str, host_key: str,
    merge_request: list[dict], scope_desc: str,
):
    """统一的 _execute_deploy 调用包装：失败时写入 FAILED 并保留已有 detail 字段。"""
    try:
        _execute_deploy(record=record, trace_id=trace_id, host_key=host_key, merge_request=merge_request)
    except Exception as e:
        logger.exception("[trace_id=%s] %s: failed to execute deploy", trace_id, scope_desc)
        # 用 detail_patch 仅更新 deploy_log，避免覆盖 _execute_deploy 内部已写入的
        # trace_id / host_key / commits 等字段。
        update_deploy_record_status(
            record_id=record.id,
            status=DeployRecord.STATUS_FAILED,
            detail_patch={'deploy_log': f'部署失败：{str(e)}'},
        )


# ============================================================
# 环境初始化
# ============================================================

def _init_server_env(ssh: SshClient, trace_id: str):
    """传输并执行服务器环境初始化脚本（安装 git、docker、nginx、certbot）"""
    try:
        with open(_ENV_INIT_SCRIPT_PATH, 'r', encoding='utf-8') as f:
            script_content = f.read()
    except FileNotFoundError:
        raise RemoteDeployError(f"环境初始化脚本不存在: {_ENV_INIT_SCRIPT_PATH}")

    remote_path = '/tmp/server_env_init.sh'
    ssh.write_file(remote_dir='/tmp', remote_path=remote_path, content=script_content)
    ssh.execute(command=f'chmod +x {remote_path}')
    logger.info("[trace_id=%s] Executing server env init script on remote server", trace_id)
    ssh.execute(command=f'bash {remote_path}')
    logger.info("[trace_id=%s] Server env init completed successfully", trace_id)


# ============================================================
# 部署执行主流程
# ============================================================

def _execute_deploy(record: DeployRecord, trace_id: str, host_key: str, merge_request: list[dict]):
    """
    执行单条指定环境部署记录的完整流程。

    步骤：
    3.1 补充 commit 信息（GitHub API 查询默认分支最新 commit）
    3.2 SSH 连通性检查（连接云服务器）
    3.3 目录文件检查（创建目录、下载/更新仓库）
    3.4 遍历部署命令（Docker 镜像打包、容器启动、Nginx 路由）
    """
    # 标记为 publishing，记录 host_key / trace_id
    detail = dict(record.detail or {})
    detail['host_key'] = host_key
    detail['trace_id'] = trace_id
    update_deploy_record_status(record_id=record.id, status=DeployRecord.STATUS_PUBLISHING, detail=detail)

    repos = get_client_repos(client_id=record.client_id, user_id=record.user_id)
    if not repos:
        raise RemoteDeployError("未配置代码仓库")

    commits, repo_auth = _fill_commit_info(repos=repos, trace_id=trace_id, merge_request=merge_request)

    detail['commits'] = commits or {}
    update_deploy_record_status(record_id=record.id, status=DeployRecord.STATUS_PUBLISHING, detail=detail)
    logger.info(
        "[trace_id=%s] Start deploy: record_id=%s, client_id=%s, env=%s, host_key=%s",
        trace_id, record.id, record.client_id, record.env, host_key,
    )

    # 3.2 SSH 检查
    servers = get_client_servers(client_id=record.client_id, user_id=record.user_id, env=record.env)
    if not servers:
        raise RemoteDeployError(f"未配置{record.env}环境云服务器")

    for server in servers:
        ip = (server.ip or '').strip()
        username = (server.name or '').strip()
        password = (server.password or '').strip()
        if not ip or not username:
            raise RemoteDeployError(f"{record.env}环境服务器 IP 或用户名为空")

        with SshClient(ip=ip, username=username, password=password, trace_id=trace_id) as ssh:
            logger.info("[trace_id=%s] SSH connected: record_id=%s, ip=%s", trace_id, record.id, ip)

            # 环境初始化：传输并执行 server_env_init.sh
            _init_server_env(ssh=ssh, trace_id=trace_id)

            # 3.3 目录文件检查
            _setup_directories(
                ssh=ssh, username=username, client_id=record.client_id,
                repos=repos, repo_auth=repo_auth, trace_id=trace_id,
            )

            # 3.4 遍历部署命令
            deploys = get_client_deploys(client_id=record.client_id, user_id=record.user_id)
            if not deploys:
                raise RemoteDeployError("未配置部署命令")

            domains = [d.domain for d in get_client_domains(client_id=record.client_id, user_id=record.user_id, env=record.env)]

            container_names = []
            for deploy in deploys:
                cname = _execute_single_deploy(
                    ssh=ssh, username=username, client_id=record.client_id, record_id=record.id,
                    deploy=deploy, commits=commits, repo_auth=repo_auth, user_id=record.user_id,
                    host_key=host_key, trace_id=trace_id, env=record.env,
                )
                container_names.append(cname)

            # 创建 nginx 容器（按 route_prefix 分流到各容器）
            if domains and container_names:
                try:
                    route_specs = pairs_from_deploys(deploys, container_names)
                except ValueError as e:
                    raise RemoteDeployError(str(e)) from e
                _setup_nginx_container(
                    ssh=ssh, username=username, client_id=record.client_id,
                    route_specs=route_specs, host_key=host_key, domains=domains, trace_id=trace_id,
                )

    # 所有服务器均部署成功后才置 SUCCESS
    update_deploy_record_status(
        record_id=record.id, status=DeployRecord.STATUS_SUCCESS,
        detail_patch={'deploy_log': '部署成功'},
    )
    logger.info("[trace_id=%s] Deploy success: record_id=%s, client_id=%s", trace_id, record.id, record.client_id)


# ============================================================
# 步骤 3.1：Commit 信息补充
# ============================================================

def _fill_commit_info(repos, trace_id: str, merge_request: list[dict]) -> tuple:
    """
    查询所有仓库默认分支的最新 commitId。

    Returns:
        (commits, repo_auth):
        - commits: {repo_id_str: {url, branch, commit_id}} — 写入数据库 detail
        - repo_auth: {repo_id_str: {token, org, repo_name}} — 仅运行时使用，不落库
    """
    from service.git_service import refresh_repo_token_by_url

    commits = {}
    repo_auth = {}
    repo_info = {}
    if merge_request:
        for mr in merge_request:
            repo_info[mr['repo_name']] = {
                'branch_name': mr['branch_name'],
                'latest_commitId': mr['latest_commitId'],
            }

    for repo in repos:
        url = repo.url
        org, repo_name = parse_github_url(url=url)
        if not org or not repo_name:
            logger.warning("[trace_id=%s] Cannot parse repo URL: %s, skip", trace_id, url)
            continue

        # 刷新 GitHub Installation Token
        try:
            token = refresh_repo_token_by_url(repo_url=url, trace_id=trace_id)
        except Exception as e:
            raise RemoteDeployError(f"刷新仓库 {repo_name} token 失败：{e}")

        # 获取默认分支最新 commit
        if repo_name in repo_info:
            branch_name = repo_info[repo_name]['branch_name']
            latest_commitId = repo_info[repo_name]['latest_commitId']
        else:
            branch_name = repo.default_branch or 'main'
            latest_commitId = get_branch_latest_commit(
                token=token, organization=org, repo_name=repo_name,
                branch=branch_name, trace_id=trace_id,
            )

        if not latest_commitId:
            raise RemoteDeployError(f"仓库 {repo_name} 分支 {branch_name} 未返回有效 commit")

        repo_id_str = str(repo.id)
        commits[repo_id_str] = {'url': url, 'branch': branch_name, 'commit_id': latest_commitId}
        repo_auth[repo_id_str] = {'token': token, 'org': org, 'repo_name': repo_name}
        logger.info(
            "[trace_id=%s] Got commit: repo=%s, branch=%s, commit=%s",
            trace_id, repo_name, branch_name, latest_commitId[:8],
        )

    return commits, repo_auth


# ============================================================
# 步骤 3.3：目录文件检查
# ============================================================

def _refresh_repo_token(repo_url: str, repo_name: str, trace_id: str) -> str:
    """刷新仓库 token 的薄包装：失败转换为 RemoteDeployError。"""
    from service.git_service import refresh_repo_token_by_url

    logger.info("[trace_id=%s] Refreshing repo token: repo=%s", trace_id, repo_name)
    try:
        return refresh_repo_token_by_url(repo_url=repo_url, trace_id=trace_id)
    except Exception as e:
        raise RemoteDeployError(f"刷新仓库 {repo_name} token 失败：{e}")


def _token_provider(url: str, repo_name: str, trace_id: str) -> str:
    """供 utils.git_remote_utils 注入的 token 刷新回调。"""
    return _refresh_repo_token(repo_url=url, repo_name=repo_name, trace_id=trace_id)


def _setup_directories(ssh, username: str, client_id: int, repos, repo_auth: dict, trace_id: str):
    """
    检查远程服务器目录结构并下载缺失的仓库。

    目录结构：
    /home/{username}/app{client_id}/
    ├── repo/          # 持久化 git 仓库
    │   ├── {repo1}/
    │   └── {repo2}/
    └── repo_tmp/      # 部署临时文件
    └── nginx/         # nginx 配置文件
    """
    base_dir = f'/home/{username}/app{client_id}'
    repo_dir = f'{base_dir}/repo'
    repo_tmp_dir = f'{base_dir}/repo_tmp'
    nginx_dir = f'{base_dir}/nginx'

    ssh.execute(command=f'mkdir -p {repo_dir}')
    ssh.execute(command=f'mkdir -p {repo_tmp_dir}')
    ssh.execute(command=f'mkdir -p {nginx_dir}')

    # 清理历史部署遗留的低速中断配置（该配置是持久化到 ~/.gitconfig 的）
    ssh.execute_ignore_error(command='git config --global --unset-all http.lowSpeedLimit')
    ssh.execute_ignore_error(command='git config --global --unset-all http.lowSpeedTime')
    ssh.execute_ignore_error(command='git config --global http.postBuffer 524288000')
    git_cfg = ssh.execute_ignore_error(
        command='git config --global --get-regexp "^http\\.(postBuffer|lowSpeedLimit|lowSpeedTime)$" || true',
    )
    logger.info("[trace_id=%s] Remote git http config after sanitize, config=%s", trace_id, git_cfg or "<empty>")

    for repo in repos:
        repo_id_str = str(repo.id)
        if repo_id_str not in repo_auth:
            continue

        auth = repo_auth[repo_id_str]
        repo_name = auth['repo_name']
        token = auth['token']
        url = repo.url
        branch = repo.default_branch or 'main'
        auth_url = build_auth_url(url=url, token=token)

        target_path = f'{repo_dir}/{repo_name}'

        is_valid_repo = ssh.execute_ignore_error(
            command=f'git -C {target_path} rev-parse --is-inside-work-tree 2>/dev/null || echo "invalid"',
        )

        try:
            if 'invalid' in is_valid_repo:
                ssh.execute_ignore_error(command=f'rm -rf {target_path}')
                clone_repo_with_retry(
                    ssh=ssh, auth_url=auth_url, branch=branch,
                    repo_dir=repo_dir, repo_name=repo_name, trace_id=trace_id,
                )
            else:
                fetch_or_reclone(
                    ssh=ssh, target_path=target_path, repo_dir=repo_dir,
                    repo_name=repo_name, branch=branch, auth_url=auth_url, trace_id=trace_id,
                )
        except GitRemoteError as e:
            if not is_git_auth_error(e.message):
                raise RemoteDeployError(e.message) from e
            # token 可能在部署过程中失效，认证失败时刷新一次并重建本地仓库（re-clone 最可靠）
            new_token = _refresh_repo_token(repo_url=url, repo_name=repo_name, trace_id=trace_id)
            repo_auth[repo_id_str]['token'] = new_token
            new_auth_url = build_auth_url(url=url, token=new_token)
            logger.warning(
                "[trace_id=%s] Git auth failed, retry with refreshed token: repo=%s",
                trace_id, repo_name,
            )
            ssh.execute_ignore_error(command=f'rm -rf {target_path}')
            try:
                clone_repo_with_retry(
                    ssh=ssh, auth_url=new_auth_url, branch=branch,
                    repo_dir=repo_dir, repo_name=repo_name, trace_id=trace_id,
                )
            except GitRemoteError as retry_err:
                raise RemoteDeployError(retry_err.message) from retry_err


# ============================================================
# 步骤 3.4：单条部署命令执行
# ============================================================

def _execute_single_deploy(
    ssh: SshClient, username: str, client_id: int, record_id: int, deploy: ClientDeploy,
    commits: dict, repo_auth: dict, user_id: int, host_key: str, trace_id: str, env: str,
) -> str:
    """
    执行单条部署命令（ClientDeploy）：拷贝仓库、打包镜像、启动容器。

    Returns:
        启动的容器名称
    """
    deploy_uuid = deploy.uuid
    repo_id = deploy.repo_id
    work_dir = (deploy.work_dir or '').strip().strip('/')
    startup_command = (deploy.startup_command or '').strip()

    if not repo_id:
        raise RemoteDeployError(f"部署配置 {deploy_uuid} 未关联代码仓库")

    repo_id_str = str(repo_id)
    if repo_id_str not in commits:
        raise RemoteDeployError(f"部署配置 {deploy_uuid} 关联的仓库 ID={repo_id} 无 commit 信息")

    commit_info = commits[repo_id_str]
    commit_id = commit_info['commit_id']
    commit_short = commit_id[:8]
    auth = repo_auth[repo_id_str]
    repo_name = auth['repo_name']
    url = commit_info['url']

    base_dir = f'/home/{username}/app{client_id}'
    repo_dir = f'{base_dir}/repo'
    tmp_dir = f'{base_dir}/repo_tmp/tmp_{record_id}'
    tmp_repo_dir = f'{tmp_dir}/{repo_name}'
    full_work_dir = f'{tmp_repo_dir}/{work_dir}' if work_dir else tmp_repo_dir

    try:
        # 创建临时目录并清理旧副本。
        # 历史部署可能遗留 root 拥有文件，先用 sudo 清理并修复权限，避免 cp 权限错误。
        ssh.execute(command=f'mkdir -p {tmp_dir}')
        ssh.execute_ignore_error(command=f'sudo rm -rf {tmp_repo_dir} || rm -rf {tmp_repo_dir}')
        ssh.execute_ignore_error(command=f'sudo chown -R {username}:{username} {tmp_dir} || true')
        ssh.execute(command=f'cp -r {repo_dir}/{repo_name} {tmp_repo_dir}')

        try:
            final_token = checkout_commit_with_auth_refresh(
                ssh=ssh, tmp_repo_dir=tmp_repo_dir, commit_id=commit_id,
                url=url, token=auth['token'], repo_name=repo_name,
                token_provider=_token_provider, trace_id=trace_id,
            )
            repo_auth[repo_id_str]['token'] = final_token
        except GitRemoteError as e:
            raise RemoteDeployError(e.message) from e

        # Docker 镜像：检查 Dockerfile 是否存在
        image_name = f'app{client_id}_{deploy_uuid}'
        image_tag = commit_short
        image_full = f'{image_name}:{image_tag}'

        df_check = ssh.execute_ignore_error(
            command=f'test -f {full_work_dir}/Dockerfile && echo "found" || echo "not_found"',
        )
        if 'not_found' in df_check:
            raise RemoteDeployError(f"工作目录 {work_dir or '/'} 下未找到 Dockerfile")

        # 镜像打包（已存在则跳过）
        # docker 命令统一用 sudo：ubuntu 用户首次部署尚未加入 docker 组时兜底，
        # 避免 /var/run/docker.sock 无权限。
        img_check = ssh.execute_ignore_error(
            command=f'sudo docker image inspect {image_full} > /dev/null 2>&1 && echo "exists" || echo "not_exists"',
        )
        if 'not_exists' in img_check:
            # BuildKit 在默认 progress=auto 下会以步骤为单位缓冲输出，失败时容易只剩 header 一行日志，
            # 真实错误丢失。强制 --progress=plain + 2>&1 合流，确保失败原因在 stdout tail 中完整可见；
            # sudo -E 保留 BUILDKIT 相关环境变量。
            build_cmd = (
                f'cd {full_work_dir} && '
                f'BUILDKIT_PROGRESS=plain DOCKER_BUILDKIT=1 DOCKER_CLI_HINTS=false '
                f'sudo -E docker build --progress=plain -t {image_full} . 2>&1'
            )
            logger.info(
                "[trace_id=%s] Building image: %s from %s, build_cmd=%s",
                trace_id, image_full, full_work_dir, build_cmd,
            )
            ssh.execute(command=build_cmd, timeout=1200)
        else:
            logger.info("[trace_id=%s] Image %s already exists, skip build", trace_id, image_full)

        # 生成 TOML 配置并写入远程服务器
        toml_content = generate_deploy_toml(
            client_id=client_id, user_id=user_id,
            official_configs=deploy.official_configs or [],
            custom_config=deploy.custom_config or '',
            env=env,
        )
        config_dir = f'{base_dir}/config{deploy_uuid}'
        config_path = f'{config_dir}/config.toml'
        if toml_content:
            ssh.write_file(remote_dir=config_dir, remote_path=config_path, content=toml_content)

        # 创建 Docker 网络
        network_name = f'network_{client_id}_{host_key}' if host_key else f'network_{client_id}'
        ssh.execute_ignore_error(command=f'sudo docker network create {network_name} 2>/dev/null')

        # 停止并删除旧容器
        container_name = (
            f'app_{client_id}_{env}_{deploy_uuid}_{host_key}' if host_key
            else f'app_{client_id}_{env}_{deploy_uuid}'
        )
        ssh.execute_ignore_error(command=f'sudo docker rm -f {container_name} 2>/dev/null')

        # 启动容器
        # 应用容器不对外暴露端口：生产流量统一走「宿主 nginx → nginx 容器 → 容器网络内 {container_name}:8080」，
        # 不需要在宿主机上再占用随机端口；排障请用 `docker exec` 或临时 `docker run --network` 接入同一网络访问。
        mount_opt = f'-v {config_path}:/config/config.toml:ro' if toml_content else ''
        # 与模板仓库约定：APP_CONFIG_PATH 指向挂载的 /config/config.toml
        env_opt = '-e APP_CONFIG_PATH=/config/config.toml' if toml_content else ''
        run_cmd = (
            f'sudo docker run -d --name {container_name} --network {network_name} '
            f'{env_opt} {mount_opt} {image_full}'
        )
        if startup_command:
            escaped_cmd = startup_command.replace("'", "'\\''")
            run_cmd += f" sh -c '{escaped_cmd}'"

        logger.info("[trace_id=%s] Container starting: image=%s, run_cmd=%s", trace_id, image_full, run_cmd)
        ssh.execute(command=run_cmd)
        logger.info("[trace_id=%s] Container started: name=%s, image=%s", trace_id, container_name, image_full)

        return container_name
    finally:
        # 清理本次部署的临时仓库拷贝（保留镜像），避免累积磁盘占用
        ssh.execute_ignore_error(command=f'sudo rm -rf {tmp_dir} || rm -rf {tmp_dir}')


# ============================================================
# Nginx 容器路由
# ============================================================

def _normalize_client_domain(domain: str) -> str:
    """从配置值中提取纯 hostname（去掉协议、路径、端口）"""
    d = (domain or '').strip()
    if not d:
        return ''
    d = d.split()[0]
    if '://' in d or d.lower().startswith('//'):
        parsed = urlparse(d if '://' in d else f'https:{d}')
        return (parsed.hostname or '').strip()
    return d.split('/')[0].split(':')[0].strip()


def _normalize_domains(domains: list) -> list:
    out = []
    for raw in domains or []:
        h = _normalize_client_domain(raw)
        if h and h not in out:
            out.append(h)
    return out


def _sanitize_key_for_nginx_filename(key: str) -> str:
    """用于文件名与容器侧目录名，避免路径注入"""
    k = (key or '').strip()
    if not k:
        return ''
    k2 = re.sub(r'[^a-zA-Z0-9_.-]+', '_', k).strip('_.')
    if not k2:
        raise RemoteDeployError(f'发布 key 无法映射为安全文件名: {key!r}')
    return k2[:80]


def _ensure_host_nginx_includes_app_vhosts(ssh, username: str, client_id: int, trace_id: str) -> None:
    """在宿主机 /etc/nginx/conf.d 下增加 include，加载 /home/{user}/app{id}/nginx/*.conf"""
    base_nginx = f'/home/{username}/app{client_id}/nginx'
    inc_path = f'/etc/nginx/conf.d/ai_task_app_{client_id}.conf'
    content = (
        f'# Managed by ai-task remote deploy (client_id={client_id}). Do not hand-edit.\n'
        f'include {base_nginx}/*.conf;\n'
    )
    ssh.write_root_owned_file(remote_path=inc_path, content=content)
    ssh.execute(command='sudo mkdir -p /var/www/certbot')
    logger.info("[trace_id=%s] Ensured host nginx include: path=%s", trace_id, inc_path)


_INNER_PROXY_COMMON = (
    '        proxy_http_version 1.1;\n'
    '        proxy_set_header Host $host;\n'
    '        proxy_set_header X-Real-IP $remote_addr;\n'
    '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
    '        proxy_set_header X-Forwarded-Proto $scheme;\n'
    '        proxy_read_timeout 3600s;\n'
    '        proxy_send_timeout 3600s;\n'
)


def _render_inner_nginx_conf(route_specs: list) -> str:
    """
    Docker 内 nginx：仅 HTTP，按路径前缀转发到对应应用容器。

    route_specs: (规范前缀, 容器名)。前缀「/」表示整站；如「/api」对外匹配 /api/…，
    使用 proxy_pass 无 URI 后缀，将浏览器请求的完整路径原样转发到容器 :8080，
    与 ai_task apiserver（蓝图注册在 /api/app/...）一致。若应用自行期望剥前缀，需在应用内配置 url_prefix 或改路由。
    """
    if not route_specs:
        raise RemoteDeployError('nginx 路由配置为空')
    non_root = [(p, c) for p, c in route_specs if p != '/']
    root = [(p, c) for p, c in route_specs if p == '/']
    if len(root) > 1:
        raise RemoteDeployError('多个部署使用了根路由前缀 /')
    non_root.sort(key=lambda x: (-len(x[0]), x[0]))
    ordered = non_root + root

    parts = [
        'server {\n',
        '    listen 80;\n',
        '    listen [::]:80;\n',
        '    server_name _;\n',
        '\n',
    ]
    for prefix, container in ordered:
        if prefix == '/':
            parts.append('    location / {\n')
            parts.append(f'        proxy_pass http://{container}:8080;\n')
            parts.append(_INNER_PROXY_COMMON)
            parts.append('    }\n')
        else:
            parts.append(f'    location = {prefix} {{\n')
            # 相对路径 308，避免内层 nginx 上 $scheme 为 http 时把 HTTPS 站点重定向成 http
            parts.append('        return 308 ${uri}/$is_args$args;\n')
            parts.append('    }\n')
            parts.append(f'    location {prefix}/ {{\n')
            parts.append(f'        proxy_pass http://{container}:8080;\n')
            parts.append(_INNER_PROXY_COMMON)
            parts.append('    }\n')
    parts.append('}\n')
    return ''.join(parts)


def _ensure_certbot_ready(ssh, trace_id: str) -> None:
    """确保 certbot 与 nginx 插件可用，并初始化 letsencrypt 附加文件。"""
    ssh.execute(
        command=(
            'if ! command -v certbot >/dev/null 2>&1; then '
            'if command -v apt-get >/dev/null 2>&1; then '
            'sudo apt-get update -y && sudo apt-get install -y certbot python3-certbot-nginx; '
            'elif command -v dnf >/dev/null 2>&1; then '
            'sudo dnf install -y certbot python3-certbot-nginx; '
            'elif command -v yum >/dev/null 2>&1; then '
            'sudo yum install -y epel-release && sudo yum install -y certbot python3-certbot-nginx; '
            'else '
            'echo "unsupported package manager" >&2; exit 1; '
            'fi; '
            'fi'
        ),
    )
    ssh.execute(command='sudo mkdir -p /var/www/certbot')
    ssh.execute(
        command=(
            'if [ ! -f /etc/letsencrypt/options-ssl-nginx.conf ]; then '
            'sudo mkdir -p /etc/letsencrypt && '
            'printf "%s\n" '
            '"ssl_session_cache shared:le_nginx_SSL:10m;" '
            '"ssl_session_timeout 1d;" '
            '"ssl_session_tickets off;" '
            '"ssl_protocols TLSv1.2 TLSv1.3;" '
            '"ssl_prefer_server_ciphers off;" '
            '"ssl_ciphers HIGH:!aNULL:!MD5;" '
            '"add_header Strict-Transport-Security \\"max-age=31536000\\" always;" '
            ' | sudo tee /etc/letsencrypt/options-ssl-nginx.conf >/dev/null; '
            'fi'
        ),
    )
    ssh.execute(
        command=(
            'if [ ! -f /etc/letsencrypt/ssl-dhparams.pem ]; then '
            'sudo openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048; '
            'fi'
        ),
        timeout=600,
    )
    logger.info("[trace_id=%s] Certbot runtime ready on host", trace_id)


def _resolve_cert_lineage_name(ssh, primary_domain: str) -> str:
    """解析 certbot 实际 lineage 名称（可能是 domain 或 domain-0001）。

    /etc/letsencrypt/live 通常为 root:750，必须用 sudo 探测，否则永远判为不存在。
    """
    pd = (primary_domain or '').strip()
    if not pd or not re.match(r'^[a-zA-Z0-9.-]+$', pd):
        return ''

    exact = ssh.execute_ignore_error(
        command=(
            f"sudo test -f '/etc/letsencrypt/live/{pd}/fullchain.pem' "
            f"&& sudo test -f '/etc/letsencrypt/live/{pd}/privkey.pem' "
            f"&& echo '{pd}' || true"
        ),
    ).strip()
    if exact:
        return exact

    wildcard = ssh.execute_ignore_error(
        command=(
            "sudo bash -c '"
            f"for d in /etc/letsencrypt/live/{pd}*; do "
            '[ -d "$d" ] || continue; '
            '[ -f "$d/fullchain.pem" ] || continue; '
            '[ -f "$d/privkey.pem" ] || continue; '
            'basename "$d"; break; '
            "done'"
        ),
    ).strip()
    return wildcard


def _ensure_domain_certificate(ssh, server_names: str, trace_id: str) -> str:
    """确保 server_names 对应证书可用，返回证书 lineage 名称。"""
    primary = server_names.split()[0]
    cert_lineage = _resolve_cert_lineage_name(ssh=ssh, primary_domain=primary)
    if cert_lineage:
        return cert_lineage

    cert_base = f'/etc/letsencrypt/live/{primary}'
    crt = f'{cert_base}/fullchain.pem'

    domain_flags = ' '.join(f'-d {d}' for d in server_names.split() if d)
    if not domain_flags:
        raise RemoteDeployError('证书签发失败：server_name 为空')

    # 使用 webroot 模式，依赖 80 端口可达与 DNS 正确。
    ssh.execute(
        command=(
            f'sudo certbot certonly --webroot -w /var/www/certbot '
            f'--register-unsafely-without-email --agree-tos -n '
            f'--cert-name {primary} {domain_flags}'
        ),
        timeout=180,
    )

    cert_lineage_after = _resolve_cert_lineage_name(ssh=ssh, primary_domain=primary)
    if not cert_lineage_after:
        raise RemoteDeployError(
            f'证书签发后仍未找到证书 lineage（期望路径之一含 {crt}）。'
            f'请检查 DNS、80 端口，并在服务器执行: sudo certbot certificates; sudo ls -la /etc/letsencrypt/live/'
        )
    logger.info("[trace_id=%s] Certificate ensured for domains=%s", trace_id, server_names)
    return cert_lineage_after


def _render_acme_http_only_vhost(server_names: str) -> str:
    """仅用于 certbot HTTP-01 的临时 vhost。"""
    return (
        f'# Managed by ai-task remote deploy (acme bootstrap)\n'
        f'server {{\n'
        f'    listen 80;\n'
        f'    listen [::]:80;\n'
        f'    server_name {server_names};\n'
        f'\n'
        f'    location /.well-known/acme-challenge/ {{\n'
        f'        root /var/www/certbot;\n'
        f'    }}\n'
        f'\n'
        f'    location / {{\n'
        f'        return 200 "acme bootstrap";\n'
        f'    }}\n'
        f'}}\n'
    )


def _render_host_nginx_vhost(server_names: str, upstream_port: str, primary_for_cert: str) -> str:
    """宿主机 nginx：HTTP 跳转 HTTPS + ACME；HTTPS 反代到本机 Docker 映射端口"""
    cert_base = f'/etc/letsencrypt/live/{primary_for_cert}'
    return (
        f'# Managed by ai-task remote deploy — do not hand-edit\n'
        f'# TLS 证书路径假定 certbot 已为「{primary_for_cert}」签发（与 server_name 首项一致）\n'
        f'server {{\n'
        f'    listen 80;\n'
        f'    listen [::]:80;\n'
        f'    server_name {server_names};\n'
        f'\n'
        f'    location /.well-known/acme-challenge/ {{\n'
        f'        root /var/www/certbot;\n'
        f'    }}\n'
        f'\n'
        f'    location / {{\n'
        f'        return 301 https://$host$request_uri;\n'
        f'    }}\n'
        f'}}\n'
        f'\n'
        f'server {{\n'
        f'    listen 443 ssl;\n'
        f'    listen [::]:443 ssl;\n'
        f'    server_name {server_names};\n'
        f'\n'
        f'    ssl_certificate {cert_base}/fullchain.pem;\n'
        f'    ssl_certificate_key {cert_base}/privkey.pem;\n'
        f'    include /etc/letsencrypt/options-ssl-nginx.conf;\n'
        f'    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;\n'
        f'\n'
        f'    client_max_body_size 50m;\n'
        f'\n'
        f'    gzip on;\n'
        f'    gzip_min_length 256;\n'
        f'    gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript;\n'
        f'    gzip_vary on;\n'
        f'    gzip_proxied any;\n'
        f'    gzip_comp_level 6;\n'
        f'\n'
        f'    location / {{\n'
        f'        proxy_pass http://127.0.0.1:{upstream_port};\n'
        f'        proxy_http_version 1.1;\n'
        f'\n'
        f'        proxy_set_header Host $host;\n'
        f'        proxy_set_header X-Real-IP $remote_addr;\n'
        f'        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
        f'        proxy_set_header X-Forwarded-Proto $scheme;\n'
        f'\n'
        f'        proxy_read_timeout 3600;\n'
        f'        proxy_send_timeout 3600;\n'
        f'    }}\n'
        f'}}\n'
    )


def _reload_host_nginx(ssh, trace_id: str) -> None:
    ssh.execute(command='sudo nginx -t && sudo systemctl reload nginx')
    logger.info("[trace_id=%s] Host nginx reloaded", trace_id)


def _setup_nginx_container(ssh, username: str, client_id: int, route_specs: list, host_key: str, domains: list, trace_id: str):
    """
    创建 Nginx 容器用于 Docker 网络内路由，并在宿主机写入 HTTPS vhost + reload。

    目录约定（均在 /home/{user}/app{client_id}/ 下）：
    - nginx/{key}.conf 或 nginx/default.conf — 宿主机 nginx 加载（仅 *.conf，不含子目录）
    - nginx/container/{inner}/default.conf — 挂载进容器，反代到应用容器:8080

    宿主机通过 /etc/nginx/conf.d/ai_task_app_{client_id}.conf 包含 nginx/*.conf。

    route_specs: [(规范路径前缀, 容器名), ...]，与 ClientDeploy.route_prefix 一致。
    """
    if not domains or not route_specs:
        return

    norm_domains = _normalize_domains(domains)
    if not norm_domains:
        raise RemoteDeployError('生产环境域名配置无效（无法解析为 hostname）')

    base_dir = f'/home/{username}/app{client_id}'

    # 与 _execute_single_deploy 中 Docker 网络/容器命名一致（沿用原始 key 的真值判断与插值）
    if host_key:
        network_name = f'network_{client_id}_{host_key}'
        nginx_name = f'nginx_{client_id}_{host_key}'
        host_key_stripped = (host_key or '').strip()
        if not host_key_stripped:
            raise RemoteDeployError('发布 key 无效（仅空白字符）')
        host_key_fs = _sanitize_key_for_nginx_filename(host_key_stripped)
        server_names = ' '.join(f'{host_key_stripped}.{d}' for d in norm_domains)
        host_conf_basename = f'{host_key_fs}.conf'
        inner_segment = host_key_fs
    else:
        network_name = f'network_{client_id}'
        nginx_name = f'nginx_{client_id}'
        server_names = ' '.join(norm_domains)
        host_conf_basename = 'default.conf'
        inner_segment = 'default'

    _ensure_certbot_ready(ssh=ssh, trace_id=trace_id)

    host_conf_path = f'{base_dir}/nginx/{host_conf_basename}'

    # 在证书签发前，先放置 HTTP challenge vhost 并 reload，避免 webroot 校验 404。
    acme_conf = _render_acme_http_only_vhost(server_names=server_names)
    ssh.write_file(remote_dir=f'{base_dir}/nginx', remote_path=host_conf_path, content=acme_conf)
    _ensure_host_nginx_includes_app_vhosts(
        ssh=ssh, username=username, client_id=client_id, trace_id=trace_id,
    )
    _reload_host_nginx(ssh=ssh, trace_id=trace_id)

    primary_for_cert = _ensure_domain_certificate(ssh=ssh, server_names=server_names, trace_id=trace_id)

    inner_conf_dir = f'{base_dir}/nginx/container/{inner_segment}'
    inner_conf_path = f'{inner_conf_dir}/default.conf'
    inner_conf = _render_inner_nginx_conf(route_specs=route_specs)
    ssh.write_file(remote_dir=inner_conf_dir, remote_path=inner_conf_path, content=inner_conf)

    nginx_port = ssh.execute(command='shuf -i 10000-60000 -n 1').strip()

    host_conf = _render_host_nginx_vhost(
        server_names=server_names,
        upstream_port=nginx_port,
        primary_for_cert=primary_for_cert,
    )
    ssh.write_file(remote_dir=f'{base_dir}/nginx', remote_path=host_conf_path, content=host_conf)

    ssh.execute_ignore_error(command=f'sudo docker rm -f {nginx_name} 2>/dev/null')

    ssh.execute(command=(
        f'sudo docker run -d --name {nginx_name} --network {network_name} '
        f'-p {nginx_port}:80 '
        f'-v {inner_conf_path}:/etc/nginx/conf.d/default.conf:ro '
        f'nginx:alpine'
    ))
    logger.info(
        "[trace_id=%s] Nginx container started: name=%s, port=%s, server_name=%s, host_vhost=%s, routes=%s",
        trace_id, nginx_name, nginx_port, server_names, host_conf_path, route_specs,
    )

    _reload_host_nginx(ssh=ssh, trace_id=trace_id)
