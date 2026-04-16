#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
远程服务器部署服务 —— 生产环境定时部署执行

流程概览：
1. 查询所有 prod 环境 pending/publishing 状态的发布记录
2. 按 client_id 分组：publishing 跳过，pending 取消旧记录、部署最新
3. 部署步骤：commit 补充 → SSH 检查 → 目录检查 → Docker 容器部署 → Nginx 路由
"""

import logging
import os
import re
import traceback
import uuid
import time
from collections import defaultdict

from dao.deploy_dao import get_pending_prod_deploy_records, update_deploy_record_status, batch_cancel_deploy_records
from dao.client_dao import get_client_repos, get_client_deploys, get_client_servers, get_client_domains
from dao.models import DeployRecord
from service.deploy_service import generate_deploy_toml
from utils.git_utils import parse_github_url, get_branch_latest_commit

logger = logging.getLogger(__name__)

SSH_CONNECT_TIMEOUT = 10
_ENV_INIT_SCRIPT_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'utils', 'server_env_init.sh'))


class RemoteDeployError(Exception):
    """远程部署执行失败"""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ============================================================
# SSH 工具函数
# ============================================================

def _create_ssh_client(ip: str, username: str, password: str):
    """创建 SSH 客户端并连接到远程服务器"""
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=username, password=password, timeout=SSH_CONNECT_TIMEOUT, allow_agent=False, look_for_keys=False)
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(30)
    return client


def _sanitize_command(command: str) -> str:
    """移除命令中的 token/密码等敏感信息，用于日志输出"""
    return re.sub(r'x-access-token:[^@]+@', 'x-access-token:***@', command)


def _ssh_exec(ssh, command: str, timeout: int | None = None) -> str:
    """执行 SSH 命令并返回 stdout，非零退出码或超时抛出异常

    Args:
        ssh: paramiko SSHClient
        command: 要执行的命令
        timeout: 命令超时秒数，None 表示不限制
    """
    safe_cmd = _sanitize_command(command=command)
    start = time.time()
    logger.info("SSH exec start: timeout=%s, cmd=%s", timeout, safe_cmd[:600])
    stdin, stdout, stderr = ssh.exec_command(command)
    if timeout is not None:
        stdout.channel.settimeout(timeout)
    try:
        exit_status = stdout.channel.recv_exit_status()
    except Exception:
        raise RemoteDeployError(f"命令超时({timeout}s): {safe_cmd[:200]}")
    elapsed_ms = int((time.time() - start) * 1000)
    out = stdout.read().decode('utf-8', errors='replace').strip()
    if exit_status != 0:
        err = stderr.read().decode('utf-8', errors='replace').strip()
        logger.error(
            "SSH exec failed: exit=%s elapsed_ms=%s cmd=%s stdout_tail=%s stderr_tail=%s",
            exit_status, elapsed_ms, safe_cmd[:600], out[-1000:], err[-2000:],
        )
        raise RemoteDeployError(f"命令失败(exit={exit_status}, elapsed_ms={elapsed_ms}): {safe_cmd[:400]}  stdout: {out[-1000:]}  stderr: {err[-2000:]}")
    logger.info("SSH exec done: exit=0 elapsed_ms=%s cmd=%s stdout_tail=%s", elapsed_ms, safe_cmd[:600], out[-500:])
    return out


def _ssh_exec_ignore_error(ssh, command: str) -> str:
    """执行 SSH 命令，忽略错误返回 stdout"""
    stdin, stdout, stderr = ssh.exec_command(command)
    stdout.channel.recv_exit_status()
    return stdout.read().decode('utf-8', errors='replace').strip()


def _ssh_write_file(ssh, remote_dir: str, remote_path: str, content: str) -> None:
    """通过 SSH 创建远程目录并写入文件"""
    _ssh_exec(ssh=ssh, command=f'mkdir -p {remote_dir}')
    sftp = ssh.open_sftp()
    try:
        with sftp.file(remote_path, 'w') as f:
            f.write(content)
    finally:
        sftp.close()


# ============================================================
# 环境初始化
# ============================================================

def _init_server_env(ssh, trace_id: str):
    """传输并执行服务器环境初始化脚本（安装 git、docker、nginx、certbot）"""
    try:
        with open(_ENV_INIT_SCRIPT_PATH, 'r', encoding='utf-8') as f:
            script_content = f.read()
    except FileNotFoundError:
        raise RemoteDeployError(f"环境初始化脚本不存在: {_ENV_INIT_SCRIPT_PATH}")

    remote_path = '/tmp/server_env_init.sh'
    _ssh_write_file(ssh=ssh, remote_dir='/tmp', remote_path=remote_path, content=script_content)
    _ssh_exec(ssh=ssh, command=f'chmod +x {remote_path}')
    logger.info("Executing server env init script on remote server, trace_id=%s", trace_id)
    _ssh_exec(ssh=ssh, command=f'bash {remote_path}')
    logger.info("Server env init completed successfully, trace_id=%s", trace_id)


# ============================================================
# 主入口
# ============================================================

def process_pending_prod_deploys():
    """
    处理所有生产环境待发布的部署记录（供调度器调用）。

    按 client_id 分组，每个应用独立处理：
    - 存在 publishing 记录 → 跳过
    - 仅 pending → 取消旧记录，部署最新
    """
    records = get_pending_prod_deploy_records()
    if not records:
        return

    grouped = defaultdict(list)
    for record in records:
        grouped[record.client_id].append(record)

    for client_id, client_records in grouped.items():
        try:
            _process_client_records(client_id=client_id, records=client_records)
        except Exception:
            logger.exception("process_pending_prod_deploys: client_id=%s error", client_id)


def _process_client_records(client_id: int, records: list):
    """处理单个应用（client）的部署记录"""
    # 存在 publishing 记录则跳过
    if any(r.status == DeployRecord.STATUS_PUBLISHING for r in records):
        logger.debug("client_id=%s has publishing record, skip", client_id)
        return

    pending = [r for r in records if r.status == DeployRecord.STATUS_PENDING]
    if not pending:
        return

    # 按创建时间降序排列，最新在前
    pending.sort(key=lambda r: r.created_at, reverse=True)
    latest = pending[0]

    # 取消所有非最新的 pending 记录
    cancel_ids = [r.id for r in pending[1:]]
    if cancel_ids:
        batch_cancel_deploy_records(record_ids=cancel_ids)
        logger.info("Cancelled %d older pending records for client_id=%s", len(cancel_ids), client_id)

    # 执行最新记录的部署
    _execute_prod_deploy(record=latest)


# ============================================================
# 部署执行主流程
# ============================================================

def _execute_prod_deploy(record):
    """
    执行单条生产环境部署记录的完整流程。

    步骤：
    3.1 补充 commit 信息（GitHub API 查询默认分支最新 commit）
    3.2 SSH 连通性检查（连接生产服务器）
    3.3 目录文件检查（创建目录、下载/更新仓库）
    3.4 遍历部署命令（Docker 镜像打包、容器启动、Nginx 路由）
    """
    record_id = record.id
    client_id = record.client_id
    user_id = record.user_id
    detail = dict(record.detail or {})

    # 生成 traceId 用于追踪本次部署全链路
    trace_id = str(uuid.uuid4())
    detail['trace_id'] = trace_id

    try:
        # 标记为 publishing
        update_deploy_record_status(record_id=record_id, status=DeployRecord.STATUS_PUBLISHING, detail=detail)
        logger.info("Start prod deploy: record_id=%s, client_id=%s, trace_id=%s", record_id, client_id, trace_id)

        # 3.1 补充 repo commit 信息
        repos = get_client_repos(client_id=client_id, user_id=user_id)
        commits, repo_auth = _fill_commit_info(repos=repos, trace_id=trace_id)
        detail['commits'] = commits
        update_deploy_record_status(record_id=record_id, status=DeployRecord.STATUS_PUBLISHING, detail=detail)

        # 3.2 SSH 检查
        servers = get_client_servers(client_id=client_id, user_id=user_id)
        prod_server = next((s for s in servers if s.env == 'prod'), None)
        if not prod_server:
            raise RemoteDeployError("未配置生产环境云服务器")

        ip = (prod_server.ip or '').strip()
        username = (prod_server.name or '').strip()
        password = (prod_server.password or '').strip()
        if not ip or not username:
            raise RemoteDeployError("生产环境服务器 IP 或用户名为空")

        ssh = _create_ssh_client(ip=ip, username=username, password=password)
        try:
            logger.info("SSH connected: record_id=%s, ip=%s, trace_id=%s", record_id, ip, trace_id)

            # 环境初始化：传输并执行 server_env_init.sh
            _init_server_env(ssh=ssh, trace_id=trace_id)

            # 3.3 目录文件检查
            _setup_directories(ssh=ssh, username=username, client_id=client_id, repos=repos, repo_auth=repo_auth, trace_id=trace_id)

            # 3.4 遍历部署命令
            deploys = get_client_deploys(client_id=client_id, user_id=user_id)
            if not deploys:
                raise RemoteDeployError("未配置部署命令")

            key = detail.get('key', '')
            domains = get_client_domains(client_id=client_id, user_id=user_id)
            prod_domains = [d.domain for d in domains if d.env == 'prod']

            container_names = []
            for deploy in deploys:
                cname = _execute_single_deploy(
                    ssh=ssh, username=username, client_id=client_id, record_id=record_id,
                    deploy=deploy, commits=commits, repo_auth=repo_auth, user_id=user_id, key=key, trace_id=trace_id,
                )
                container_names.append(cname)

            # 创建 nginx 容器
            if prod_domains and container_names:
                _setup_nginx_container(
                    ssh=ssh, username=username, client_id=client_id,
                    container_names=container_names, key=key, prod_domains=prod_domains, trace_id=trace_id,
                )

            # 部署成功
            detail['deploy_log'] = '部署成功'
            update_deploy_record_status(record_id=record_id, status=DeployRecord.STATUS_SUCCESS, detail=detail)
            logger.info("Prod deploy success: record_id=%s, client_id=%s, trace_id=%s", record_id, client_id, trace_id)
        finally:
            ssh.close()

    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        detail['deploy_log'] = f'部署失败：{error_msg}'
        update_deploy_record_status(record_id=record_id, status=DeployRecord.STATUS_FAILED, detail=detail)
        logger.error("Prod deploy failed: record_id=%s, client_id=%s, trace_id=%s, error=%s\n%s", record_id, client_id, trace_id, error_msg, tb)


# ============================================================
# 步骤 3.1：Commit 信息补充
# ============================================================

def _fill_commit_info(repos, trace_id: str) -> tuple:
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

    for repo in repos:
        url = repo.url
        org, repo_name = parse_github_url(url=url)
        if not org or not repo_name:
            logger.warning("Cannot parse repo URL: %s, skip, trace_id=%s", url, trace_id)
            continue

        # 刷新 GitHub Installation Token
        try:
            token = refresh_repo_token_by_url(repo_url=url)
        except Exception as e:
            raise RemoteDeployError(f"刷新仓库 {repo_name} token 失败：{e}")

        # 获取默认分支最新 commit
        branch = repo.default_branch or 'main'
        try:
            commit_id = get_branch_latest_commit(token=token, organization=org, repo_name=repo_name, branch=branch)
        except Exception as e:
            raise RemoteDeployError(f"获取仓库 {repo_name} 分支 {branch} 最新提交失败：{e}")

        repo_id_str = str(repo.id)
        commits[repo_id_str] = {'url': url, 'branch': branch, 'commit_id': commit_id}
        repo_auth[repo_id_str] = {'token': token, 'org': org, 'repo_name': repo_name}
        logger.info("Got commit: repo=%s, branch=%s, commit=%s, trace_id=%s", repo_name, branch, commit_id[:8], trace_id)

    return commits, repo_auth


# ============================================================
# 步骤 3.3：目录文件检查
# ============================================================

_GIT_CLONE_TIMEOUT = 300
_GIT_CLONE_MAX_RETRIES = 2


def _is_git_auth_error(message: str) -> bool:
    lower_msg = (message or "").lower()
    return (
        "authentication failed" in lower_msg
        or "invalid username or token" in lower_msg
        or "password authentication is not supported" in lower_msg
    )


def _refresh_repo_token(repo_url: str, repo_name: str, trace_id: str) -> str:
    from service.git_service import refresh_repo_token_by_url

    logger.info("Refreshing repo token: repo=%s, trace_id=%s", repo_name, trace_id)
    try:
        return refresh_repo_token_by_url(repo_url=repo_url)
    except Exception as e:
        raise RemoteDeployError(f"刷新仓库 {repo_name} token 失败：{e}")


def _setup_directories(ssh, username: str, client_id: int, repos, repo_auth: dict, trace_id: str):
    """
    检查远程服务器目录结构并下载缺失的仓库。

    目录结构：
    /home/{username}/app{client_id}/
    ├── repo/          # 持久化 git 仓库
    │   ├── {repo1}/
    │   └── {repo2}/
    └── repo_tmp/      # 部署临时文件
    """
    base_dir = f'/home/{username}/app{client_id}'
    repo_dir = f'{base_dir}/repo'
    repo_tmp_dir = f'{base_dir}/repo_tmp'

    _ssh_exec(ssh=ssh, command=f'mkdir -p {repo_dir}')
    _ssh_exec(ssh=ssh, command=f'mkdir -p {repo_tmp_dir}')

    # 清理历史部署遗留的低速中断配置（该配置是持久化到 ~/.gitconfig 的）
    _ssh_exec_ignore_error(ssh=ssh, command='git config --global --unset-all http.lowSpeedLimit')
    _ssh_exec_ignore_error(ssh=ssh, command='git config --global --unset-all http.lowSpeedTime')
    _ssh_exec_ignore_error(ssh=ssh, command='git config --global http.postBuffer 524288000')
    git_cfg = _ssh_exec_ignore_error(
        ssh=ssh,
        command='git config --global --get-regexp "^http\\.(postBuffer|lowSpeedLimit|lowSpeedTime)$" || true',
    )
    logger.info("Remote git http config after sanitize, trace_id=%s, config=%s", trace_id, git_cfg or "<empty>")

    for repo in repos:
        repo_id_str = str(repo.id)
        if repo_id_str not in repo_auth:
            continue

        auth = repo_auth[repo_id_str]
        repo_name = auth['repo_name']
        token = auth['token']
        url = repo.url
        branch = repo.default_branch or 'main'
        auth_url = url.replace('https://github.com', f'https://x-access-token:{token}@github.com')

        target_path = f'{repo_dir}/{repo_name}'

        is_valid_repo = _ssh_exec_ignore_error(
            ssh=ssh,
            command=f'git -C {target_path} rev-parse --is-inside-work-tree 2>/dev/null || echo "invalid"',
        )

        try:
            if 'invalid' in is_valid_repo:
                _ssh_exec_ignore_error(ssh=ssh, command=f'rm -rf {target_path}')
                _clone_repo_with_retry(
                    ssh=ssh, auth_url=auth_url, branch=branch,
                    repo_dir=repo_dir, repo_name=repo_name, trace_id=trace_id,
                )
            else:
                _fetch_repo_with_retry(
                    ssh=ssh, target_path=target_path, auth_url=auth_url,
                    repo_name=repo_name, trace_id=trace_id,
                )
        except RemoteDeployError as e:
            if not _is_git_auth_error(e.message):
                raise
            # token 可能在部署过程中失效，认证失败时刷新一次并重试
            new_token = _refresh_repo_token(repo_url=url, repo_name=repo_name, trace_id=trace_id)
            repo_auth[repo_id_str]['token'] = new_token
            new_auth_url = url.replace('https://github.com', f'https://x-access-token:{new_token}@github.com')
            logger.warning("Git auth failed, retry with refreshed token: repo=%s, trace_id=%s", repo_name, trace_id)
            if 'invalid' in is_valid_repo:
                _ssh_exec_ignore_error(ssh=ssh, command=f'rm -rf {target_path}')
                _clone_repo_with_retry(
                    ssh=ssh, auth_url=new_auth_url, branch=branch,
                    repo_dir=repo_dir, repo_name=repo_name, trace_id=trace_id,
                )
            else:
                _fetch_repo_with_retry(
                    ssh=ssh, target_path=target_path, auth_url=new_auth_url,
                    repo_name=repo_name, trace_id=trace_id,
                )


def _clone_repo_with_retry(ssh, auth_url: str, branch: str, repo_dir: str, repo_name: str, trace_id: str):
    """clone 仓库（浅克隆 + 重试），失败时清理残留目录"""
    target_path = f'{repo_dir}/{repo_name}'
    clone_cmd = f'cd {repo_dir} && git clone --depth 1 --single-branch --branch {branch} {auth_url} {repo_name}'

    last_err = None
    for attempt in range(1, _GIT_CLONE_MAX_RETRIES + 1):
        logger.info(
            "Cloning repo (attempt %d/%d): %s -> %s, trace_id=%s",
            attempt, _GIT_CLONE_MAX_RETRIES, repo_name, target_path, trace_id,
        )
        try:
            _ssh_exec(ssh=ssh, command=clone_cmd, timeout=_GIT_CLONE_TIMEOUT)
            return
        except RemoteDeployError as e:
            last_err = e
            logger.warning(
                "Clone attempt %d failed, repo=%s, trace_id=%s, detail=%s",
                attempt, repo_name, trace_id, e.message,
            )
            _ssh_exec_ignore_error(ssh=ssh, command=f'rm -rf {target_path}')

    raise RemoteDeployError(f"仓库 {repo_name} 克隆失败（已重试{_GIT_CLONE_MAX_RETRIES}次）：{last_err.message if last_err else 'unknown'}")


def _fetch_repo_with_retry(ssh, target_path: str, auth_url: str, repo_name: str, trace_id: str):
    """更新已有仓库（刷新 token + fetch），带重试"""
    fetch_cmd = f'cd {target_path} && git remote set-url origin {auth_url} && git fetch origin'

    last_err = None
    for attempt in range(1, _GIT_CLONE_MAX_RETRIES + 1):
        logger.info(
            "Fetching repo (attempt %d/%d): %s, trace_id=%s",
            attempt, _GIT_CLONE_MAX_RETRIES, repo_name, trace_id,
        )
        try:
            _ssh_exec(ssh=ssh, command=fetch_cmd, timeout=_GIT_CLONE_TIMEOUT)
            return
        except RemoteDeployError as e:
            last_err = e
            logger.warning(
                "Fetch attempt %d failed, repo=%s, trace_id=%s, detail=%s",
                attempt, repo_name, trace_id, e.message,
            )

    raise RemoteDeployError(f"仓库 {repo_name} fetch 失败（已重试{_GIT_CLONE_MAX_RETRIES}次）：{last_err.message if last_err else 'unknown'}")


# ============================================================
# 步骤 3.4：单条部署命令执行
# ============================================================

def _execute_single_deploy(ssh, username: str, client_id: int, record_id: int, deploy, commits: dict, repo_auth: dict, user_id: int, key: str, trace_id: str) -> str:
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
    auth_url = commit_info['url'].replace('https://github.com', f"https://x-access-token:{auth['token']}@github.com")

    base_dir = f'/home/{username}/app{client_id}'
    repo_dir = f'{base_dir}/repo'
    tmp_dir = f'{base_dir}/repo_tmp/tmp_{record_id}'
    tmp_repo_dir = f'{tmp_dir}/{repo_name}'
    full_work_dir = f'{tmp_repo_dir}/{work_dir}' if work_dir else tmp_repo_dir

    # 创建临时目录并清理旧副本。
    # 历史部署可能遗留 root 拥有文件，先用 sudo 清理并修复权限，避免 cp 权限错误。
    _ssh_exec(ssh=ssh, command=f'mkdir -p {tmp_dir}')
    _ssh_exec_ignore_error(ssh=ssh, command=f'sudo rm -rf {tmp_repo_dir} || rm -rf {tmp_repo_dir}')
    _ssh_exec_ignore_error(ssh=ssh, command=f'sudo chown -R {username}:{username} {tmp_dir} || true')
    _ssh_exec(ssh=ssh, command=f'cp -r {repo_dir}/{repo_name} {tmp_repo_dir}')
    checkout_cmd = (
        f'cd {tmp_repo_dir} && '
        f'git checkout {commit_id} || '
        f'(git remote set-url origin {auth_url} && git fetch --depth 1 origin {commit_id} && git checkout {commit_id})'
    )
    try:
        _ssh_exec(ssh=ssh, command=checkout_cmd, timeout=_GIT_CLONE_TIMEOUT)
    except RemoteDeployError as e:
        if not _is_git_auth_error(e.message):
            raise
        new_token = _refresh_repo_token(repo_url=commit_info['url'], repo_name=repo_name, trace_id=trace_id)
        repo_auth[repo_id_str]['token'] = new_token
        auth_url = commit_info['url'].replace('https://github.com', f"https://x-access-token:{new_token}@github.com")
        retry_checkout_cmd = (
            f'cd {tmp_repo_dir} && '
            f'git remote set-url origin {auth_url} && '
            f'git fetch --depth 1 origin {commit_id} && git checkout {commit_id}'
        )
        logger.warning("Checkout auth failed, retry with refreshed token: repo=%s, trace_id=%s", repo_name, trace_id)
        _ssh_exec(ssh=ssh, command=retry_checkout_cmd, timeout=_GIT_CLONE_TIMEOUT)

    # Docker 镜像：检查 Dockerfile 是否存在
    image_name = f'app{client_id}_{deploy_uuid}'
    image_tag = commit_short
    image_full = f'{image_name}:{image_tag}'

    df_check = _ssh_exec_ignore_error(ssh=ssh, command=f'test -f {full_work_dir}/Dockerfile && echo "found" || echo "not_found"')
    if 'not_found' in df_check:
        raise RemoteDeployError(f"工作目录 {work_dir or '/'} 下未找到 Dockerfile")

    # 镜像打包（已存在则跳过）
    # docker 命令统一用 sudo：ubuntu 用户首次部署尚未加入 docker 组时兜底，避免 /var/run/docker.sock 无权限
    img_check = _ssh_exec_ignore_error(ssh=ssh, command=f'sudo docker image inspect {image_full} > /dev/null 2>&1 && echo "exists" || echo "not_exists"')
    if 'not_exists' in img_check:
        logger.info("Building image: %s from %s, trace_id=%s", image_full, full_work_dir, trace_id)
        _ssh_exec(ssh=ssh, command=f'cd {full_work_dir} && sudo docker build -t {image_full} .', timeout=600)
    else:
        logger.info("Image %s already exists, skip build, trace_id=%s", image_full, trace_id)

    # 生成 TOML 配置并写入远程服务器
    toml_content = generate_deploy_toml(
        client_id=client_id, user_id=user_id,
        official_configs=deploy.official_configs or [],
        custom_config=deploy.custom_config or '',
        env='prod',
    )
    config_dir = f'{base_dir}/config{deploy_uuid}'
    config_path = f'{config_dir}/config.toml'
    if toml_content:
        _ssh_write_file(ssh=ssh, remote_dir=config_dir, remote_path=config_path, content=toml_content)

    # 创建 Docker 网络
    network_name = f'network_{client_id}_{key}' if key else f'network_{client_id}'
    _ssh_exec_ignore_error(ssh=ssh, command=f'sudo docker network create {network_name} 2>/dev/null')

    # 停止并删除旧容器
    container_name = f'app_{client_id}_{deploy_uuid}'
    _ssh_exec_ignore_error(ssh=ssh, command=f'sudo docker rm -f {container_name} 2>/dev/null')

    # 生成随机端口
    port = _ssh_exec(ssh=ssh, command='shuf -i 10000-60000 -n 1')

    # 启动容器
    mount_opt = f'-v {config_path}:/config/config.toml:ro' if toml_content else ''
    run_cmd = f'sudo docker run -d --name {container_name} --network {network_name} -p {port}:8080 {mount_opt} {image_full}'
    if startup_command:
        escaped_cmd = startup_command.replace("'", "'\\''")
        run_cmd += f" sh -c '{escaped_cmd}'"

    _ssh_exec(ssh=ssh, command=run_cmd)
    logger.info("Container started: name=%s, port=%s, image=%s, trace_id=%s", container_name, port, image_full, trace_id)

    return container_name


# ============================================================
# Nginx 容器路由
# ============================================================

def _setup_nginx_container(ssh, username: str, client_id: int, container_names: list, key: str, prod_domains: list, trace_id: str):
    """
    创建 Nginx 容器用于域名路由。

    通过 Docker 网络内的容器名称实现反向代理：
    域名 key.{host}（或 {host}）→ Nginx 容器 → 应用容器:8080
    """
    if not prod_domains or not container_names:
        return

    base_dir = f'/home/{username}/app{client_id}'
    network_name = f'network_{client_id}_{key}' if key else f'network_{client_id}'
    nginx_name = f'nginx_{client_id}_{key}' if key else f'nginx_{client_id}'

    # 构建 server_name
    if key:
        server_names = ' '.join(f'{key}.{d}' for d in prod_domains)
    else:
        server_names = ' '.join(prod_domains)

    # 生成 Nginx 配置（代理到第一个应用容器）
    primary_container = container_names[0]
    nginx_conf = (
        'server {\n'
        '    listen 80;\n'
        f'    server_name {server_names};\n'
        '\n'
        '    location / {\n'
        f'        proxy_pass http://{primary_container}:8080;\n'
        '        proxy_set_header Host $host;\n'
        '        proxy_set_header X-Real-IP $remote_addr;\n'
        '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
        '        proxy_set_header X-Forwarded-Proto $scheme;\n'
        '        proxy_read_timeout 3600s;\n'
        '        proxy_send_timeout 3600s;\n'
        '    }\n'
        '}\n'
    )

    # 写入 Nginx 配置文件
    nginx_dir = f'{base_dir}/nginx_{key}' if key else f'{base_dir}/nginx'
    nginx_conf_path = f'{nginx_dir}/default.conf'
    _ssh_write_file(ssh=ssh, remote_dir=nginx_dir, remote_path=nginx_conf_path, content=nginx_conf)

    # 停止并删除旧 Nginx 容器
    _ssh_exec_ignore_error(ssh=ssh, command=f'sudo docker rm -f {nginx_name} 2>/dev/null')

    # 生成随机端口
    nginx_port = _ssh_exec(ssh=ssh, command='shuf -i 10000-60000 -n 1')

    # 启动 Nginx 容器
    _ssh_exec(ssh=ssh, command=(
        f'sudo docker run -d --name {nginx_name} --network {network_name} '
        f'-p {nginx_port}:80 '
        f'-v {nginx_conf_path}:/etc/nginx/conf.d/default.conf:ro '
        f'nginx:alpine'
    ))
    logger.info("Nginx container started: name=%s, port=%s, server_name=%s, trace_id=%s", nginx_name, nginx_port, server_names, trace_id)
