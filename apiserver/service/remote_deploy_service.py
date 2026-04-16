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
from urllib.parse import urlparse
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

    失败时日志与错误消息同时保留 stdout/stderr 的尾部 4000 字符。
    docker build（2>&1 合流到 stdout）场景下，npm/pip 等工具在失败时
    会连带打印长篇 usage/help 文本，若尾部过短会把真正的错误行挤出窗口。
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
            exit_status, elapsed_ms, safe_cmd[:600], out[-4000:], err[-4000:],
        )
        raise RemoteDeployError(f"命令失败(exit={exit_status}, elapsed_ms={elapsed_ms}): {safe_cmd[:400]}  stdout: {out[-4000:]}  stderr: {err[-4000:]}")
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


def _ssh_write_root_owned_file(ssh, remote_path: str, content: str) -> None:
    """写入需 root 权限的路径（先写 /tmp 再 sudo mv）"""
    tmp = f'/tmp/_ai_task_deploy_{uuid.uuid4().hex}'
    _ssh_write_file(ssh=ssh, remote_dir='/tmp', remote_path=tmp, content=content)
    _ssh_exec(
        ssh=ssh,
        command=(
            f'sudo mv {tmp} {remote_path} && '
            f'sudo chmod 644 {remote_path} && sudo chown root:root {remote_path}'
        ),
    )


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
_GIT_CLONE_MAX_RETRIES = 3
_GIT_RETRY_BACKOFF_BASE_SEC = 2  # 重试退避基数，第 N 次失败 sleep 2 * 2^(N-1) 秒

# 每条 git 命令前置 per-command 环境变量实现快速失败，避免跨境链路 stall 时等到 TCP 超时（~130s）才失败
# 不写入 ~/.gitconfig，不影响服务器上其他 git 使用
# - GIT_HTTP_LOW_SPEED_LIMIT=1000: 低于 1KB/s
# - GIT_HTTP_LOW_SPEED_TIME=20:   持续 20 秒 → 判定失败
_GIT_HTTP_FAIL_FAST_ENV = 'GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=20'


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
                _fetch_or_reclone(
                    ssh=ssh, target_path=target_path, repo_dir=repo_dir,
                    repo_name=repo_name, branch=branch, auth_url=auth_url, trace_id=trace_id,
                )
        except RemoteDeployError as e:
            if not _is_git_auth_error(e.message):
                raise
            # token 可能在部署过程中失效，认证失败时刷新一次并重建本地仓库（re-clone 最可靠）
            new_token = _refresh_repo_token(repo_url=url, repo_name=repo_name, trace_id=trace_id)
            repo_auth[repo_id_str]['token'] = new_token
            new_auth_url = url.replace('https://github.com', f'https://x-access-token:{new_token}@github.com')
            logger.warning("Git auth failed, retry with refreshed token: repo=%s, trace_id=%s", repo_name, trace_id)
            _ssh_exec_ignore_error(ssh=ssh, command=f'rm -rf {target_path}')
            _clone_repo_with_retry(
                ssh=ssh, auth_url=new_auth_url, branch=branch,
                repo_dir=repo_dir, repo_name=repo_name, trace_id=trace_id,
            )


def _clone_repo_with_retry(ssh, auth_url: str, branch: str, repo_dir: str, repo_name: str, trace_id: str):
    """clone 仓库（浅克隆 + 重试 + 指数退避），失败时清理残留目录"""
    target_path = f'{repo_dir}/{repo_name}'
    # --no-tags 减少拉取量；per-command 低速超时，卡住 20s 后立刻失败
    clone_cmd = (
        f'cd {repo_dir} && {_GIT_HTTP_FAIL_FAST_ENV} '
        f'git clone --depth 1 --single-branch --no-tags --branch {branch} {auth_url} {repo_name}'
    )

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
            if attempt < _GIT_CLONE_MAX_RETRIES and not _is_git_auth_error(e.message):
                backoff = _GIT_RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.info("Clone backoff %ds before retry, repo=%s, trace_id=%s", backoff, repo_name, trace_id)
                time.sleep(backoff)

    raise RemoteDeployError(f"仓库 {repo_name} 克隆失败（已重试{_GIT_CLONE_MAX_RETRIES}次）：{last_err.message if last_err else 'unknown'}")


def _fetch_repo_with_retry(ssh, target_path: str, auth_url: str, repo_name: str, branch: str, trace_id: str):
    """更新已有仓库（浅 fetch 指定分支 + 重试 + 指数退避）

    相比无参数的 `git fetch origin`，只拉取目标分支的最新 1 个 commit，数据量最小化，
    显著降低跨境链路 TLS 被中断的概率。
    """
    fetch_cmd = (
        f'cd {target_path} && git remote set-url origin {auth_url} && '
        f'{_GIT_HTTP_FAIL_FAST_ENV} git fetch --depth 1 --no-tags origin {branch}'
    )

    last_err = None
    for attempt in range(1, _GIT_CLONE_MAX_RETRIES + 1):
        logger.info(
            "Fetching repo (attempt %d/%d): %s, branch=%s, trace_id=%s",
            attempt, _GIT_CLONE_MAX_RETRIES, repo_name, branch, trace_id,
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
            # 认证错误不退避、不重试，立即抛给上层刷新 token
            if _is_git_auth_error(e.message):
                break
            if attempt < _GIT_CLONE_MAX_RETRIES:
                backoff = _GIT_RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.info("Fetch backoff %ds before retry, repo=%s, trace_id=%s", backoff, repo_name, trace_id)
                time.sleep(backoff)

    raise RemoteDeployError(f"仓库 {repo_name} fetch 失败（已重试{_GIT_CLONE_MAX_RETRIES}次）：{last_err.message if last_err else 'unknown'}")


def _fetch_or_reclone(ssh, target_path: str, repo_dir: str, repo_name: str, branch: str, auth_url: str, trace_id: str):
    """优先增量 fetch；fetch 持续失败（非认证错误）时删除本地目录重新浅 clone。

    认证错误保留原语义，抛给上层统一刷新 token 后重建。
    """
    try:
        _fetch_repo_with_retry(
            ssh=ssh, target_path=target_path, auth_url=auth_url,
            repo_name=repo_name, branch=branch, trace_id=trace_id,
        )
    except RemoteDeployError as fetch_err:
        if _is_git_auth_error(fetch_err.message):
            raise
        logger.warning(
            "Fetch persistently failed for %s, fallback to re-clone, trace_id=%s, detail=%s",
            repo_name, trace_id, fetch_err.message,
        )
        _ssh_exec_ignore_error(ssh=ssh, command=f'rm -rf {target_path}')
        _clone_repo_with_retry(
            ssh=ssh, auth_url=auth_url, branch=branch,
            repo_dir=repo_dir, repo_name=repo_name, trace_id=trace_id,
        )


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
        f'(git remote set-url origin {auth_url} && '
        f'{_GIT_HTTP_FAIL_FAST_ENV} git fetch --depth 1 --no-tags origin {commit_id} && git checkout {commit_id})'
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
            f'{_GIT_HTTP_FAIL_FAST_ENV} git fetch --depth 1 --no-tags origin {commit_id} && git checkout {commit_id}'
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
        # BuildKit 在默认 progress=auto 下会以步骤为单位缓冲输出，失败时容易只剩 header 一行日志，真实错误丢失。
        # 强制 --progress=plain + 2>&1 合流，确保失败原因在 stdout tail 中完整可见；sudo -E 保留 BUILDKIT 相关环境变量。
        build_cmd = (
            f'cd {full_work_dir} && '
            f'BUILDKIT_PROGRESS=plain DOCKER_BUILDKIT=1 DOCKER_CLI_HINTS=false '
            f'sudo -E docker build --progress=plain -t {image_full} . 2>&1'
        )
        _ssh_exec(ssh=ssh, command=build_cmd, timeout=600)
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


def _normalize_prod_domains(prod_domains: list) -> list:
    out = []
    for raw in prod_domains or []:
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
    _ssh_write_root_owned_file(ssh=ssh, remote_path=inc_path, content=content)
    _ssh_exec(ssh=ssh, command='sudo mkdir -p /var/www/certbot')
    logger.info("Ensured host nginx include: path=%s trace_id=%s", inc_path, trace_id)


def _render_inner_nginx_conf(primary_container: str) -> str:
    """Docker 内 nginx：仅 HTTP，按容器名转发到应用"""
    return (
        'server {\n'
        '    listen 80;\n'
        '    listen [::]:80;\n'
        '    server_name _;\n'
        '\n'
        '    location / {\n'
        f'        proxy_pass http://{primary_container}:8080;\n'
        '        proxy_http_version 1.1;\n'
        '        proxy_set_header Host $host;\n'
        '        proxy_set_header X-Real-IP $remote_addr;\n'
        '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
        '        proxy_set_header X-Forwarded-Proto $scheme;\n'
        '        proxy_read_timeout 3600s;\n'
        '        proxy_send_timeout 3600s;\n'
        '    }\n'
        '}\n'
    )


def _ensure_certbot_ready(ssh, trace_id: str) -> None:
    """确保 certbot 与 nginx 插件可用，并初始化 letsencrypt 附加文件。"""
    _ssh_exec(
        ssh=ssh,
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
    _ssh_exec(ssh=ssh, command='sudo mkdir -p /var/www/certbot')
    _ssh_exec(
        ssh=ssh,
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
    _ssh_exec(
        ssh=ssh,
        command=(
            'if [ ! -f /etc/letsencrypt/ssl-dhparams.pem ]; then '
            'sudo openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048; '
            'fi'
        ),
        timeout=600,
    )
    logger.info("Certbot runtime ready on host, trace_id=%s", trace_id)


def _resolve_cert_lineage_name(ssh, primary_domain: str) -> str:
    """解析 certbot 实际 lineage 名称（可能是 domain 或 domain-0001）。"""
    exact = _ssh_exec_ignore_error(
        ssh=ssh,
        command=(
            f'test -f /etc/letsencrypt/live/{primary_domain}/fullchain.pem '
            f'-a -f /etc/letsencrypt/live/{primary_domain}/privkey.pem && echo "{primary_domain}" || true'
        ),
    ).strip()
    if exact:
        return exact

    wildcard = _ssh_exec_ignore_error(
        ssh=ssh,
        command=(
            f'for d in /etc/letsencrypt/live/{primary_domain}*; do '
            f'[ -d "$d" ] || continue; '
            f'[ -f "$d/fullchain.pem" ] || continue; '
            f'[ -f "$d/privkey.pem" ] || continue; '
            f'basename "$d"; break; '
            f'done'
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
    key = f'{cert_base}/privkey.pem'

    cert_exists = _ssh_exec_ignore_error(
        ssh=ssh,
        command=f'test -f {crt} -a -f {key} && echo "yes" || echo "no"',
    )
    if 'yes' in cert_exists:
        return primary

    domain_flags = ' '.join(f'-d {d}' for d in server_names.split() if d)
    if not domain_flags:
        raise RemoteDeployError('证书签发失败：server_name 为空')

    # 使用 webroot 模式，依赖 80 端口可达与 DNS 正确。
    _ssh_exec(
        ssh=ssh,
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
            f'证书签发后仍未找到证书文件: {crt} / {key}，请检查 DNS 与 80 端口可达性'
        )
    logger.info("Certificate ensured for domains=%s trace_id=%s", server_names, trace_id)
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
    _ssh_exec(ssh=ssh, command='sudo nginx -t && sudo systemctl reload nginx')
    logger.info("Host nginx reloaded, trace_id=%s", trace_id)


def _setup_nginx_container(ssh, username: str, client_id: int, container_names: list, key: str, prod_domains: list, trace_id: str):
    """
    创建 Nginx 容器用于 Docker 网络内路由，并在宿主机写入 HTTPS vhost + reload。

    目录约定（均在 /home/{user}/app{client_id}/ 下）：
    - nginx/{key}.conf 或 nginx/default.conf — 宿主机 nginx 加载（仅 *.conf，不含子目录）
    - nginx/container/{inner}/default.conf — 挂载进容器，反代到应用容器:8080

    宿主机通过 /etc/nginx/conf.d/ai_task_app_{client_id}.conf 包含 nginx/*.conf。
    """
    if not prod_domains or not container_names:
        return

    norm_domains = _normalize_prod_domains(prod_domains)
    if not norm_domains:
        raise RemoteDeployError('生产环境域名配置无效（无法解析为 hostname）')

    base_dir = f'/home/{username}/app{client_id}'

    # 与 _execute_single_deploy 中 Docker 网络/容器命名一致（沿用原始 key 的真值判断与插值）
    if key:
        network_name = f'network_{client_id}_{key}'
        nginx_name = f'nginx_{client_id}_{key}'
        key_stripped = (key or '').strip()
        if not key_stripped:
            raise RemoteDeployError('发布 key 无效（仅空白字符）')
        key_fs = _sanitize_key_for_nginx_filename(key_stripped)
        server_names = ' '.join(f'{key_stripped}.{d}' for d in norm_domains)
        host_conf_basename = f'{key_fs}.conf'
        inner_segment = key_fs
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
    _ssh_write_file(ssh=ssh, remote_dir=f'{base_dir}/nginx', remote_path=host_conf_path, content=acme_conf)
    _ensure_host_nginx_includes_app_vhosts(
        ssh=ssh, username=username, client_id=client_id, trace_id=trace_id,
    )
    _reload_host_nginx(ssh=ssh, trace_id=trace_id)

    primary_for_cert = _ensure_domain_certificate(ssh=ssh, server_names=server_names, trace_id=trace_id)

    primary_container = container_names[0]
    inner_conf_dir = f'{base_dir}/nginx/container/{inner_segment}'
    inner_conf_path = f'{inner_conf_dir}/default.conf'
    inner_conf = _render_inner_nginx_conf(primary_container=primary_container)
    _ssh_write_file(ssh=ssh, remote_dir=inner_conf_dir, remote_path=inner_conf_path, content=inner_conf)

    nginx_port = _ssh_exec(ssh=ssh, command='shuf -i 10000-60000 -n 1').strip()

    host_conf = _render_host_nginx_vhost(
        server_names=server_names,
        upstream_port=nginx_port,
        primary_for_cert=primary_for_cert,
    )
    _ssh_write_file(ssh=ssh, remote_dir=f'{base_dir}/nginx', remote_path=host_conf_path, content=host_conf)

    _ssh_exec_ignore_error(ssh=ssh, command=f'sudo docker rm -f {nginx_name} 2>/dev/null')

    _ssh_exec(ssh=ssh, command=(
        f'sudo docker run -d --name {nginx_name} --network {network_name} '
        f'-p {nginx_port}:80 '
        f'-v {inner_conf_path}:/etc/nginx/conf.d/default.conf:ro '
        f'nginx:alpine'
    ))
    logger.info(
        "Nginx container started: name=%s, port=%s, server_name=%s, host_vhost=%s trace_id=%s",
        nginx_name, nginx_port, server_names, host_conf_path, trace_id,
    )

    _reload_host_nginx(ssh=ssh, trace_id=trace_id)
