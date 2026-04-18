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
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from config_model import AppConfig, TencentDnsConfig
from dao.deploy_dao import get_pending_deploy_records, update_deploy_record_status, batch_cancel_deploy_records
from dao.client_dao import (
    get_client_repos, get_client_deploys, get_client_servers, get_client_domains,
    get_all_active_servers_by_env,
)
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
from utils.tencent_dns_utils import (
    TencentDnsError,
    ensure_a_records_for_fqdns,
    is_configured as is_tencent_dns_configured,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────
# 模块级 AppConfig 持有者
# 部署调度是后台线程，不在 Flask 请求上下文里，不能用 current_app.config 取配置。
# 由 main.create_app() 在启动调度器之前调用 set_app_config() 注入一次即可。
# ──────────────────────────────────────────────────────
_app_config: "AppConfig | None" = None


def set_app_config(config: "AppConfig") -> None:
    """由 main.create_app() 在启动阶段调用，注入全局应用配置供后台调度使用。"""
    global _app_config
    _app_config = config


def _get_tencent_dns_config() -> "TencentDnsConfig | None":
    if _app_config is None:
        return None
    return getattr(_app_config, 'tencent_dns', None)

_ENV_INIT_SCRIPT_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'utils', 'server_env_init.sh'))

# ──────────────────────────────────────────────────────
#  按服务器 IP 缓存本进程内已执行过的一次性幂等操作
#  - 跨部署复用，进程重启后清零（重启后成本 = 每台服务器付出一次，可接受）
#  - 缓存 key 带脚本/逻辑的"版本"，逻辑或脚本变更时自动重跑
# ──────────────────────────────────────────────────────
_server_init_lock = threading.Lock()
_server_env_init_done: "set[tuple[str, str]]" = set()  # (ip, script_version)
_server_git_config_done: "set[str]" = set()            # ip
_server_certbot_ready: "set[str]" = set()              # ip
_server_host_include_done: "set[tuple[str, int]]" = set()  # (ip, client_id)

# ──────────────────────────────────────────────────────
# certbot 首签时的 DNS/重试时间预算
# 目标：失败路径上新增的等待时间合计 <= 5 分钟
#   - 首次 certbot 前 DNS 探测:  _DNS_POLL_INITIAL_TIMEOUT_S        = 90s
#   - 每次 certbot 重试前再探测:  _DNS_POLL_RETRY_TIMEOUT_S × (N-1) = 45 × 2 = 90s
#   - certbot 自身 N 次尝试，失败态约 20~30s 一次              ≈ 60s
#   合计 ≲ 240s
# ──────────────────────────────────────────────────────
_CERTBOT_MAX_ATTEMPTS = 3
_DNS_POLL_INITIAL_TIMEOUT_S = 90
_DNS_POLL_RETRY_TIMEOUT_S = 45
_DNS_POLL_STEP_S = 3
_CERTBOT_SINGLE_RUN_TIMEOUT_S = 120

# 视为"DNS 尚未就绪"的 certbot 瞬态错误关键字（出现其一才会重试）
_CERTBOT_TRANSIENT_DNS_PATTERNS = (
    'NXDOMAIN',
    'DNS problem',
    'secondary validation',
    'no valid A records',
    'no valid AAAA records',
    'could not resolve host',
)

# DNSPod 公共权威 NS。polling 时同时查权威 + 公共递归，规避负缓存与同步延迟。
_DNSPOD_AUTH_NS = 'ns1.dnsv5.com'
_PUBLIC_DNS_RESOLVER = '8.8.8.8'


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
    """
    处理指定应用 test 环境的待发布记录（按 msg_id 分组，不取消旧记录）。

    与 prod 流程不同：
    - 同一 msg_id 下存在 publishing 记录 → 跳过（避免并发）
    - 每个 msg_id 独立部署；旧的 pending 记录保持原状（不做 cancel）
    - merge_request 通过 msg.extra 中的 merge_request 字段获取
    """
    records = get_pending_deploy_records(client_id=client_id, env='test')
    if not records:
        return

    msg_records: dict[int, list] = {}
    for record in records:
        msg_records.setdefault(record.msg_id or 0, []).append(record)

    for msg_id, grp in msg_records.items():
        trace_id = str(uuid.uuid4())
        scope_desc = f'client_id={client_id}, msg_id={msg_id}'

        if any(r.status == DeployRecord.STATUS_PUBLISHING for r in grp):
            logger.info("[trace_id=%s] %s has publishing record, skip", trace_id, scope_desc)
            continue

        pending = [r for r in grp if r.status == DeployRecord.STATUS_PENDING]
        if not pending:
            continue
        # 同一 msg_id 下理论上只会有一条 pending（因为唯一约束 (task, chat, msg, env)），
        # 仍保留 sort 作为防御：取 created_at 最新的一条。
        pending.sort(key=lambda r: r.created_at, reverse=True)
        publish_record = pending[0]

        if msg_id and msg_id > 0:
            msgs = batch_get_msg_by_msgids(user_id=publish_record.user_id, msg_ids=[msg_id])
            merge_request = (msgs[0].extra or {}).get('merge_request') if msgs else None
        else:
            merge_request = None
        if not merge_request:
            merge_request = (publish_record.detail or {}).get('merge_request') or None
        if not merge_request:
            logger.warning(
                "[trace_id=%s] %s no merge_request resolvable, skip",
                trace_id, scope_desc,
            )
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


def check_docker_network_exists(client_id: int, user_id: int, env: str, host_key: str) -> bool:
    """检查任一目标环境服务器上是否存在对应的 docker 网络。

    网络命名与 `_execute_single_deploy` / `_setup_nginx_container` 保持一致：
    - host_key 非空（test 预览）：`network_{client_id}_{host_key}`
    - host_key 为空（prod 默认）：`network_{client_id}`

    只要有一台 env 对应的服务器存在该网络，即认为容器在线。

    任意服务器 SSH 失败时仅记录日志并视为不存在，由上层决定是否触发重新部署。
    """
    trace_id = str(uuid.uuid4())
    env = (env or '').strip() or 'test'
    host_key = (host_key or '').strip()

    servers = get_client_servers(client_id=client_id, user_id=user_id, env=env)
    if not servers:
        return False

    network_name = f'network_{client_id}_{host_key}' if host_key else f'network_{client_id}'
    check_cmd = (
        f'sudo docker network inspect {network_name} > /dev/null 2>&1 '
        f'&& echo "exists" || echo "missing"'
    )

    for server in servers:
        ip = (server.ip or '').strip()
        username = (server.name or '').strip()
        password = (server.password or '').strip()
        if not ip or not username:
            continue
        try:
            with SshClient(
                ip=ip, username=username, password=password,
                connect_timeout=5, retries=1, trace_id=trace_id,
            ) as ssh:
                out = ssh.execute_ignore_error(command=check_cmd)
                if 'exists' in (out or ''):
                    logger.info(
                        "[trace_id=%s] view network exists: env=%s ip=%s network=%s",
                        trace_id, env, ip, network_name,
                    )
                    return True
        except Exception:
            logger.warning(
                "[trace_id=%s] view ssh check failed: env=%s ip=%s network=%s",
                trace_id, env, ip, network_name, exc_info=True,
            )
            continue

    logger.info(
        "[trace_id=%s] view network missing on all %s servers: client_id=%s network=%s",
        trace_id, env, client_id, network_name,
    )
    return False


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
    """传输并执行服务器环境初始化脚本（安装 git、docker、nginx、certbot）。

    脚本本身幂等，但即使空跑也要 ~8 秒。使用 (ip, 脚本内容哈希) 做进程内缓存，
    同一脚本版本 + 同一台服务器在进程生命周期内只会真正执行一次。
    """
    try:
        with open(_ENV_INIT_SCRIPT_PATH, 'r', encoding='utf-8') as f:
            script_content = f.read()
    except FileNotFoundError:
        raise RemoteDeployError(f"环境初始化脚本不存在: {_ENV_INIT_SCRIPT_PATH}")

    # 用脚本内容哈希做版本号，脚本更新后会自动 cache miss 重新执行
    import hashlib
    script_version = hashlib.sha1(script_content.encode('utf-8')).hexdigest()[:12]
    cache_key = (ssh.ip, script_version)
    with _server_init_lock:
        if cache_key in _server_env_init_done:
            logger.info(
                "[trace_id=%s] Server env init skipped (cached): ip=%s, version=%s",
                trace_id, ssh.ip, script_version,
            )
            return

    remote_path = '/tmp/server_env_init.sh'
    ssh.write_file(remote_dir='/tmp', remote_path=remote_path, content=script_content)
    ssh.execute(command=f'chmod +x {remote_path}')
    logger.info("[trace_id=%s] Executing server env init script on remote server", trace_id)
    ssh.execute(command=f'bash {remote_path}')
    logger.info("[trace_id=%s] Server env init completed successfully", trace_id)
    with _server_init_lock:
        _server_env_init_done.add(cache_key)


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

    # 预先取 deploys 并过滤出实际会用到的 repos，避免对未被任何 deploy 引用的
    # 仓库（例如独立文档仓库）做无谓的 token 刷新 / commit 查询 / 远端 fetch。
    deploys = get_client_deploys(client_id=record.client_id, user_id=record.user_id)
    if not deploys:
        raise RemoteDeployError("未配置部署命令")
    required_repo_ids = {d.repo_id for d in deploys if d.repo_id}
    required_repos = [r for r in repos if r.id in required_repo_ids]
    if not required_repos:
        raise RemoteDeployError("所有部署命令均未关联有效的代码仓库")
    skipped = [r.url for r in repos if r.id not in required_repo_ids]
    if skipped:
        logger.info(
            "[trace_id=%s] Skip repos not referenced by any deploy: %s",
            trace_id, ','.join(skipped),
        )

    commits, repo_auth = _fill_commit_info(repos=required_repos, trace_id=trace_id, merge_request=merge_request)

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

            # 3.3 目录文件检查（只同步被 deploys 引用的仓库）
            _setup_directories(
                ssh=ssh, username=username, client_id=record.client_id,
                repos=required_repos, repo_auth=repo_auth, commits=commits,
                trace_id=trace_id,
            )

            # 3.4 遍历部署命令
            domains = [d.domain for d in get_client_domains(client_id=record.client_id, user_id=record.user_id, env=record.env)]

            # 同一 record 下的多个 deploy 可能共用同一个 (repo, commit)，
            # 共享 tmp_repo_dir，整条 record 结束后统一清理，避免对同一仓库重复 cp/checkout。
            tmp_dir = f'/home/{username}/app{record.client_id}/repo_tmp/tmp_{record.id}'
            prepared_repos: "set[tuple[str, str]]" = set()  # (repo_name, commit_id)
            container_names = []
            try:
                for deploy in deploys:
                    cname = _execute_single_deploy(
                        ssh=ssh, username=username, client_id=record.client_id, record_id=record.id,
                        deploy=deploy, commits=commits, repo_auth=repo_auth, user_id=record.user_id,
                        host_key=host_key, trace_id=trace_id, env=record.env,
                        prepared_repos=prepared_repos,
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
            finally:
                ssh.execute_ignore_error(command=f'sudo rm -rf {tmp_dir} || rm -rf {tmp_dir}')

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
        - commits: {repo_id_str: {repo_id, url, branch, commit_id}} — 写入数据库 detail；
          值里冗余 repo_id 便于重试 / 跨环境复用时直接按 repo_id 匹配
        - repo_auth: {repo_id_str: {token, org, repo_name}} — 仅运行时使用，不落库
    """
    from service.git_service import refresh_repo_token_by_url

    commits = {}
    repo_auth = {}
    # client 侧 after_execute 与服务端 _build_merge_request_for_client 都会在 merge_request
    # 中写入 repo_id，这里优先按 repo_id 精确匹配（repo_name 兜底用于历史数据）。
    mr_by_repo_id: dict[int, dict] = {}
    mr_by_repo_name: dict[str, dict] = {}
    for mr in (merge_request or []):
        rid = mr.get('repo_id')
        if rid:
            mr_by_repo_id[int(rid)] = mr
        if mr.get('repo_name'):
            mr_by_repo_name[mr['repo_name']] = mr

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

        mr_hit = mr_by_repo_id.get(repo.id) or mr_by_repo_name.get(repo_name)
        if mr_hit:
            branch_name = mr_hit['branch_name']
            latest_commitId = mr_hit['latest_commitId']
        else:
            branch_name = repo.default_branch or 'main'
            latest_commitId = get_branch_latest_commit(
                token=token, organization=org, repo_name=repo_name,
                branch=branch_name, trace_id=trace_id,
            )

        if not latest_commitId:
            raise RemoteDeployError(f"仓库 {repo_name} 分支 {branch_name} 未返回有效 commit")

        repo_id_str = str(repo.id)
        commits[repo_id_str] = {
            'repo_id': repo.id,
            'url': url,
            'branch': branch_name,
            'commit_id': latest_commitId,
        }
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


def _setup_directories(
    ssh, username: str, client_id: int, repos, repo_auth: dict,
    commits: dict, trace_id: str,
):
    """
    检查远程服务器目录结构并保证每个仓库的目标 commit 在本地可用。

    目录结构：
    /home/{username}/app{client_id}/
    ├── repo/          # 持久化 git 仓库
    │   ├── {repo1}/
    │   └── {repo2}/
    └── repo_tmp/      # 部署临时文件
    └── nginx/         # nginx 配置文件

    优化要点：`commits` 中给出的目标 commit 若在本地 `repo/{repo_name}` 已经存在，
    则 fetch_or_reclone 内部直接走缓存命中分支，零远端请求；这是跨境链路下最关键的
    "不必要网络调用消除"。
    """
    base_dir = f'/home/{username}/app{client_id}'
    repo_dir = f'{base_dir}/repo'
    repo_tmp_dir = f'{base_dir}/repo_tmp'
    nginx_dir = f'{base_dir}/nginx'

    ssh.execute(command=f'mkdir -p {repo_dir}')
    ssh.execute(command=f'mkdir -p {repo_tmp_dir}')
    ssh.execute(command=f'mkdir -p {nginx_dir}')

    # 清理历史部署遗留的低速中断配置（该配置是持久化到 ~/.gitconfig 的），按 IP 幂等缓存，
    # 同一进程对同一台服务器只做一次。
    with _server_init_lock:
        git_cfg_done = ssh.ip in _server_git_config_done
    if not git_cfg_done:
        ssh.execute_ignore_error(command='git config --global --unset-all http.lowSpeedLimit')
        ssh.execute_ignore_error(command='git config --global --unset-all http.lowSpeedTime')
        ssh.execute_ignore_error(command='git config --global http.postBuffer 524288000')
        git_cfg = ssh.execute_ignore_error(
            command='git config --global --get-regexp "^http\\.(postBuffer|lowSpeedLimit|lowSpeedTime)$" || true',
        )
        logger.info("[trace_id=%s] Remote git http config after sanitize, config=%s", trace_id, git_cfg or "<empty>")
        with _server_init_lock:
            _server_git_config_done.add(ssh.ip)
    else:
        logger.info("[trace_id=%s] Remote git http config sanitize skipped (cached): ip=%s", trace_id, ssh.ip)

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
        target_commit = (commits.get(repo_id_str) or {}).get('commit_id') or ''

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
                    repo_name=repo_name, branch=branch, auth_url=auth_url,
                    trace_id=trace_id, commit_id=target_commit,
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
    prepared_repos: "set[tuple[str, str]] | None" = None,
) -> str:
    """
    执行单条部署命令（ClientDeploy）：拷贝仓库、打包镜像、启动容器。

    Args:
        prepared_repos: 本次 record 已准备好（cp + checkout）的 (repo_name, commit_id) 集合；
            调用方跨多个 deploy 共享该集合，命中时跳过重复的 cp/checkout。
            整个 record 结束后由调用方负责清理 tmp_dir。

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

    image_name = f'app{client_id}_{deploy_uuid}'
    image_tag = commit_short
    image_full = f'{image_name}:{image_tag}'

    # 前置 docker image inspect：镜像已存在时直接跳过 cp / checkout / Dockerfile 检查 / build，
    # 对「images already exist」场景可省掉 GB 级 IO 与一次远端认证交互
    # 注意：echo 标记需保证互不为子串，曾用 "exists"/"not_exists" 导致
    # `'exists' in 'not_exists'` 误判为 True，镜像不存在时仍跳过 build。
    img_check = ssh.execute_ignore_error(
        command=f'sudo docker image inspect {image_full} > /dev/null 2>&1 && echo "exists" || echo "missing"',
    )
    image_exists = 'exists' in img_check and 'missing' not in img_check

    if not image_exists:
        repo_share_key = (repo_name, commit_id)
        already_prepared = prepared_repos is not None and repo_share_key in prepared_repos
        if not already_prepared:
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
            if prepared_repos is not None:
                prepared_repos.add(repo_share_key)
        else:
            logger.info(
                "[trace_id=%s] Reuse prepared tmp_repo_dir for repo=%s commit=%s, skip cp+checkout",
                trace_id, repo_name, commit_short,
            )

        # Docker 镜像：检查 Dockerfile 是否存在
        df_check = ssh.execute_ignore_error(
            command=f'test -f {full_work_dir}/Dockerfile && echo "found" || echo "not_found"',
        )
        if 'not_found' in df_check:
            raise RemoteDeployError(f"工作目录 {work_dir or '/'} 下未找到 Dockerfile")

        # BuildKit 在默认 progress=auto 下会以步骤为单位缓冲输出，失败时容易只剩 header 一行日志，
        # 真实错误丢失。强制 --progress=plain + 2>&1 合流，确保失败原因在 stdout tail 中完整可见；
        # sudo -E 保留 BUILDKIT 相关环境变量。
        # docker 命令统一用 sudo：ubuntu 用户首次部署尚未加入 docker 组时兜底，
        # 避免 /var/run/docker.sock 无权限。
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
        logger.info(
            "[trace_id=%s] Image %s already exists, skip cp+checkout+build",
            trace_id, image_full,
        )

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
    """在宿主机 /etc/nginx/conf.d 下增加 include，加载 /home/{user}/app{id}/nginx/*.conf。

    进程内按 (ip, client_id) 幂等缓存：include 文件内容仅依赖 username/client_id，
    同一 (ip, client_id) 首次写入后，后续部署直接跳过。/var/www/certbot 目录在
    _ensure_certbot_ready 里已创建，这里不再重复 mkdir。
    """
    cache_key = (ssh.ip, client_id)
    with _server_init_lock:
        if cache_key in _server_host_include_done:
            logger.info(
                "[trace_id=%s] Host nginx include already ensured (cached): ip=%s, client_id=%s",
                trace_id, ssh.ip, client_id,
            )
            return

    base_nginx = f'/home/{username}/app{client_id}/nginx'
    inc_path = f'/etc/nginx/conf.d/ai_task_app_{client_id}.conf'
    content = (
        f'# Managed by ai-task remote deploy (client_id={client_id}). Do not hand-edit.\n'
        f'include {base_nginx}/*.conf;\n'
    )
    ssh.write_root_owned_file(remote_path=inc_path, content=content)
    logger.info("[trace_id=%s] Ensured host nginx include: path=%s", trace_id, inc_path)
    with _server_init_lock:
        _server_host_include_done.add(cache_key)


_INNER_PROXY_COMMON = (
    '        proxy_http_version 1.1;\n'
    '        proxy_set_header Host $host;\n'
    '        proxy_set_header X-Real-IP $remote_addr;\n'
    '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
    '        proxy_set_header X-Forwarded-Proto $scheme;\n'
    '        proxy_read_timeout 3600s;\n'
    '        proxy_send_timeout 3600s;\n'
    # 应用容器常监听非 80/443 端口（模板约定 :8080），若其返回绝对 Location
    # （例如 `return 302 /xxx;` 被 nginx 按 $scheme://$host:$server_port 拼绝对 URL），
    # 会把容器端口泄漏到 Location 头；再叠加宿主 HSTS，浏览器会升级到
    # https://host:8080/... 而访问不通。这里把上游返回的任意 http(s) 绝对 Location
    # 统一改写为相对路径，由浏览器按当前 URL 的 scheme/host/port 自行补齐，彻底兜底。
    '        proxy_redirect ~^https?://[^/]+(/.*)$ $1;\n'
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


def _wait_remote_dns_ready(
    ssh, fqdns: list, expected_ip: str, trace_id: str, timeout_s: int,
) -> bool:
    """在远端服务器上通过 `dig` 轮询 FQDN，直到权威 NS + 公共递归都返回 expected_ip。

    - 同时查询 DNSPod 权威 NS（绕过递归缓存）与公共递归（近似 Let's Encrypt 视角）；
      两者都命中才算就绪，规避"权威已更新但 LE 的递归还缓存 NXDOMAIN"的场景。
    - 任一 FQDN 超时仅返回 False，不抛异常——让上层决定是否仍尝试 certbot。
    - `dig` 由 server_env_init.sh 预装（dnsutils/bind-utils），进入本函数时应可直接使用；
      兜底检测到缺失时记 warning 后降级为「不轮询」，让 certbot 按原流程尝试。

    Returns:
        True 当且仅当所有 fqdn 在超时前均被双路解析为 expected_ip。
    """
    fqdns = [f for f in (fqdns or []) if f]
    expected_ip = (expected_ip or '').strip()
    if not fqdns or not expected_ip or timeout_s <= 0:
        return False

    has_dig = (ssh.execute_ignore_error(
        command='command -v dig >/dev/null 2>&1 && echo yes || echo no',
    ) or '').strip()
    if 'yes' not in has_dig:
        logger.warning(
            "[trace_id=%s] dig unavailable on %s (server_env_init.sh should have installed it), "
            "skip DNS readiness polling",
            trace_id, ssh.ip,
        )
        return False

    all_ok = True
    for fqdn in fqdns:
        # 每个 FQDN 单独给完整预算，避免前一个慢的吃光后一个；超时后返回 'timeout'。
        # `exec_timeout` 给 ssh.execute 留 15s 缓冲防止 recv_exit_status 悬挂。
        script = (
            f'end=$(( $(date +%s) + {int(timeout_s)} )); '
            f'while [ $(date +%s) -lt $end ]; do '
            f'  auth=$(dig @{_DNSPOD_AUTH_NS} +time=2 +tries=1 +short A {fqdn} 2>/dev/null | head -n 1); '
            f'  pub=$(dig @{_PUBLIC_DNS_RESOLVER} +time=2 +tries=1 +short A {fqdn} 2>/dev/null | head -n 1); '
            f'  if [ "$auth" = "{expected_ip}" ] && [ "$pub" = "{expected_ip}" ]; then '
            f'    echo "ok auth=$auth pub=$pub"; exit 0; '
            f'  fi; '
            f'  sleep {_DNS_POLL_STEP_S}; '
            f'done; '
            f'last_auth=$(dig @{_DNSPOD_AUTH_NS} +time=2 +tries=1 +short A {fqdn} 2>/dev/null | head -n 1); '
            f'last_pub=$(dig @{_PUBLIC_DNS_RESOLVER} +time=2 +tries=1 +short A {fqdn} 2>/dev/null | head -n 1); '
            f'echo "timeout auth=$last_auth pub=$last_pub"'
        )
        try:
            out = ssh.execute(command=script, timeout=timeout_s + 15)
        except Exception as e:
            logger.warning(
                "[trace_id=%s] dns poll exec error fqdn=%s err=%s",
                trace_id, fqdn, str(e)[:200],
            )
            all_ok = False
            continue

        first_token = (out or '').strip().split('\n')[-1]
        if first_token.startswith('ok'):
            logger.info(
                "[trace_id=%s] dns poll ready: fqdn=%s expected=%s detail=%s",
                trace_id, fqdn, expected_ip, first_token,
            )
        else:
            logger.warning(
                "[trace_id=%s] dns poll timeout: fqdn=%s expected=%s detail=%s",
                trace_id, fqdn, expected_ip, first_token,
            )
            all_ok = False

    return all_ok


def _ensure_dns_for_server_names(ssh, server_names: str, trace_id: str) -> bool:
    """在 certbot 签证书前，自动把 server_names 中每个 FQDN 的 A 记录写入 DNSPod。

    - 未配置凭据/managed_zones 或未命中 managed_zones 时：静默跳过（保留原有 prod 流程：域名由客户自管）
    - 任意一个 FQDN 命中 managed_zones 但 API 调用失败：抛 RemoteDeployError，让整条部署标为失败
      （否则 certbot 紧接着就会因 DNS 未就绪而失败，错误原因反而被埋得更深）
    - 变更后的传播等待在 ensure_a_records_for_fqdns 内部统一 sleep 一次

    Returns:
        True 当且仅当本次调用实际 created/modified 了记录——调用方应据此决定
        是否需要在 certbot 前等 DNS 传播、并在瞬态失败时重试。
    """
    dns_config = _get_tencent_dns_config()
    if not is_tencent_dns_configured(config=dns_config):
        return False

    fqdns = [d for d in (server_names or '').split() if d]
    if not fqdns:
        return False

    target_ip = (ssh.ip or '').strip()
    if not target_ip:
        raise RemoteDeployError('自动 DNS 解析失败：目标服务器 IP 为空')

    try:
        results = ensure_a_records_for_fqdns(
            config=dns_config, fqdns=fqdns, ip=target_ip, trace_id=trace_id,
        )
    except TencentDnsError as e:
        raise RemoteDeployError(f'自动 DNS 解析失败（DNSPod）：{e}') from e

    # 全部 fqdn 都属于非托管域时，视为用户手动维护 DNS；记一条 info 即可
    if results and all(v == 'unmanaged' for v in results.values()):
        logger.info(
            '[trace_id=%s] tencent_dns: all fqdns unmanaged, rely on user-managed DNS: %s',
            trace_id, fqdns,
        )
        return False

    return any(v in ('created', 'modified') for v in (results or {}).values())


def _ensure_certbot_ready(ssh, trace_id: str) -> None:
    """确保 certbot 与 nginx 插件可用，并初始化 letsencrypt 附加文件。

    进程内按 IP 幂等缓存：同一台服务器在进程生命周期内只真正执行一次
    （安装检查 + 附加文件生成），后续部署直接跳过。
    """
    with _server_init_lock:
        if ssh.ip in _server_certbot_ready:
            logger.info("[trace_id=%s] Certbot runtime ready (cached): ip=%s", trace_id, ssh.ip)
            return

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
    with _server_init_lock:
        _server_certbot_ready.add(ssh.ip)


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


def _is_transient_dns_error(err_msg: str) -> bool:
    """certbot 输出里是否包含典型"DNS 尚未就绪"瞬态信号（区分大小写宽松）。"""
    if not err_msg:
        return False
    low = err_msg.lower()
    return any(p.lower() in low for p in _CERTBOT_TRANSIENT_DNS_PATTERNS)


def _ensure_domain_certificate(
    ssh, server_names: str, trace_id: str, dns_just_changed: bool = False,
) -> str:
    """确保 server_names 对应证书可用，返回证书 lineage 名称。

    Args:
        dns_just_changed: 本次部署期间刚刚 created/modified 过 DNS 记录时设为 True；
            此时首次 certbot 前会主动探测 DNS 传播（最多 _DNS_POLL_INITIAL_TIMEOUT_S 秒），
            且瞬态 DNS 错误时会重试并再次探测。

    重试策略（为避免 Let's Encrypt 达到账号速率上限，整个重试窗口控制在 ~5 分钟内）：
        - 最多 _CERTBOT_MAX_ATTEMPTS 次尝试（= 1 次 + _CERTBOT_MAX_ATTEMPTS-1 次重试）
        - 每次重试前重新探测 DNS，超时上限 _DNS_POLL_RETRY_TIMEOUT_S
        - 仅当错误消息匹配 _CERTBOT_TRANSIENT_DNS_PATTERNS 时重试，
          其他错误（80 端口不通、webroot 权限、账号被 ban 等）立即抛出
    """
    primary = server_names.split()[0]
    cert_lineage = _resolve_cert_lineage_name(ssh=ssh, primary_domain=primary)
    if cert_lineage:
        return cert_lineage

    cert_base = f'/etc/letsencrypt/live/{primary}'
    crt = f'{cert_base}/fullchain.pem'

    fqdns = [d for d in server_names.split() if d]
    domain_flags = ' '.join(f'-d {d}' for d in fqdns)
    if not domain_flags:
        raise RemoteDeployError('证书签发失败：server_name 为空')

    target_ip = (ssh.ip or '').strip()

    # 首次 certbot 前：DNS 刚被改动过 → 主动等它传播到权威 NS + 公共递归
    if dns_just_changed and target_ip:
        _wait_remote_dns_ready(
            ssh=ssh, fqdns=fqdns, expected_ip=target_ip,
            trace_id=trace_id, timeout_s=_DNS_POLL_INITIAL_TIMEOUT_S,
        )

    certbot_cmd = (
        f'sudo certbot certonly --webroot -w /var/www/certbot '
        f'--register-unsafely-without-email --agree-tos -n '
        f'--cert-name {primary} {domain_flags}'
    )

    last_err: "Exception | None" = None
    for attempt in range(1, _CERTBOT_MAX_ATTEMPTS + 1):
        try:
            ssh.execute(command=certbot_cmd, timeout=_CERTBOT_SINGLE_RUN_TIMEOUT_S)
            last_err = None
            break
        except Exception as e:
            last_err = e
            err_msg = str(e)
            if attempt >= _CERTBOT_MAX_ATTEMPTS or not _is_transient_dns_error(err_msg=err_msg):
                # 终局失败或非瞬态错误 → 直接抛，保留原始上下文
                raise
            logger.warning(
                "[trace_id=%s] certbot transient DNS failure (attempt %s/%s), "
                "will re-poll DNS and retry: %s",
                trace_id, attempt, _CERTBOT_MAX_ATTEMPTS, err_msg[:400],
            )
            if target_ip:
                _wait_remote_dns_ready(
                    ssh=ssh, fqdns=fqdns, expected_ip=target_ip,
                    trace_id=trace_id, timeout_s=_DNS_POLL_RETRY_TIMEOUT_S,
                )

    # 兜底：理论上成功或已 raise；保留该分支以防未来改动破坏循环不变式
    if last_err is not None:
        raise last_err

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


_AI_TASK_INCLUDE_RE = re.compile(r'^/etc/nginx/conf\.d/ai_task_app_(\d+)\.conf$')


def _purge_conflicting_cross_client_nginx_includes(
    ssh, username: str, client_id: int, server_names: str, trace_id: str,
) -> None:
    """摘除其它 client 下声明了相同 server_name 的 ai-task include 文件。

    ai-task 每个 client 通过 `/etc/nginx/conf.d/ai_task_app_{cid}.conf` include
    `/home/{user}/app{cid}/nginx/*.conf`。当两个 client 在同一台服务器上配了相同的
    生产域名时，nginx reload 会出现 duplicate server_name，按文件加载顺序（字典序小的
    先加载）命中老 client 的 vhost，流量被路由到已经不活跃或跑着别的代码的旧应用。

    这里只删 `/etc/nginx/conf.d/ai_task_app_{other_cid}.conf`，保留
    `/home/{user}/app{other_cid}/nginx/*.conf` 原始文件：
    - 宿主 nginx 立刻不再加载老 client 的 vhost，通过本次部署末尾的 reload 生效；
    - 老 client 若后续再部署到这台机器，`_ensure_host_nginx_includes_app_vhosts` 会把
      include 文件重新写回，不丢数据也不影响冷迁回来；
    - 同步清掉本进程内 `_server_host_include_done` 的 `(ip, other_cid)` 缓存，避免老
      client 下一次部署走进"已 ensured"快捷路径却没重新写文件。

    仅做 include 层面的清理（保守策略）；若需要同时清掉老 client 的 `.conf` 原文件，
    应由独立的"应用卸载"流程负责。
    """
    my_names = {tok for tok in (server_names or '').split() if tok}
    if not my_names:
        return

    ls_out = ssh.execute_ignore_error(
        command='sudo ls -1 /etc/nginx/conf.d/ai_task_app_*.conf 2>/dev/null',
    )
    include_paths = [line.strip() for line in (ls_out or '').splitlines() if line.strip()]
    if not include_paths:
        return

    for inc_path in include_paths:
        m = _AI_TASK_INCLUDE_RE.match(inc_path)
        if not m:
            continue
        other_cid = int(m.group(1))
        if other_cid == client_id:
            continue

        other_dir = f'/home/{username}/app{other_cid}/nginx'
        # glob 无匹配时 grep 会报错到 stderr，已用 2>/dev/null 丢弃；
        # execute_ignore_error 吞掉非零退出码，只拿 stdout。
        grep_out = ssh.execute_ignore_error(
            command=(
                f"sudo grep -hE '^[[:space:]]*server_name[[:space:]]+' "
                f"{other_dir}/*.conf 2>/dev/null"
            ),
        )
        other_names: "set[str]" = set()
        for line in (grep_out or '').splitlines():
            stripped = line.strip()
            if not stripped.lower().startswith('server_name'):
                continue
            rest = stripped[len('server_name'):].strip().rstrip(';').strip()
            for tok in rest.split():
                tok = tok.strip()
                if tok:
                    other_names.add(tok)

        collisions = my_names & other_names
        if not collisions:
            continue

        ssh.execute_ignore_error(command=f'sudo rm -f {inc_path}')
        with _server_init_lock:
            _server_host_include_done.discard((ssh.ip, other_cid))
        logger.warning(
            "[trace_id=%s] Purged cross-client nginx include to resolve "
            "duplicate server_name: other_client_id=%s path=%s collisions=%s "
            "my_server_names=%s",
            trace_id, other_cid, inc_path, sorted(collisions), sorted(my_names),
        )


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

    # 先摘掉其它 client 在本机声明了相同 server_name 的 include，避免 duplicate
    # server_name 让老应用抢走流量。本次部署末尾的 _reload_host_nginx 会让变更生效。
    _purge_conflicting_cross_client_nginx_includes(
        ssh=ssh, username=username, client_id=client_id,
        server_names=server_names, trace_id=trace_id,
    )

    _ensure_certbot_ready(ssh=ssh, trace_id=trace_id)

    # certbot HTTP-01 依赖公网 DNS 能解析到本台服务器。test 预览子域名是动态生成的，
    # 必须在签证书前把 A 记录写入 DNSPod，否则会触发 NXDOMAIN 导致 challenge 失败。
    # 未配置或未命中 managed_zones 时自动跳过，保持向后兼容。
    # 返回值 True 表示本次确实 created/modified 了记录，后续签证书需要等传播。
    dns_just_changed = _ensure_dns_for_server_names(
        ssh=ssh, server_names=server_names, trace_id=trace_id,
    )

    host_conf_path = f'{base_dir}/nginx/{host_conf_basename}'

    # 稳态（证书已存在）下无需 ACME HTTP-01 验证，可跳过一次 bootstrap vhost 写入 + reload。
    primary_domain = server_names.split()[0]
    existing_lineage = _resolve_cert_lineage_name(ssh=ssh, primary_domain=primary_domain)
    if existing_lineage:
        # 证书已就绪：直接生成最终 HTTPS vhost 后再 reload 一次即可
        _ensure_host_nginx_includes_app_vhosts(
            ssh=ssh, username=username, client_id=client_id, trace_id=trace_id,
        )
        primary_for_cert = existing_lineage
        logger.info(
            "[trace_id=%s] Certificate lineage already present, skip ACME bootstrap reload: %s",
            trace_id, existing_lineage,
        )
    else:
        # 首次签发：先写 HTTP-01 bootstrap vhost 让 webroot 校验能通过
        acme_conf = _render_acme_http_only_vhost(server_names=server_names)
        ssh.write_file(remote_dir=f'{base_dir}/nginx', remote_path=host_conf_path, content=acme_conf)
        _ensure_host_nginx_includes_app_vhosts(
            ssh=ssh, username=username, client_id=client_id, trace_id=trace_id,
        )
        _reload_host_nginx(ssh=ssh, trace_id=trace_id)
        primary_for_cert = _ensure_domain_certificate(
            ssh=ssh, server_names=server_names, trace_id=trace_id,
            dns_just_changed=dns_just_changed,
        )

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


# ============================================================
# 测试环境过期容器清理
# ============================================================

# 测试环境容器 host_key 格式：task{task_id}chat{chat_id}msg{msg_id}
# app 容器命名：app_{client_id}_{env}_{deploy_uuid}_{host_key}
_TEST_APP_CONTAINER_RE = re.compile(r'^app_(\d+)_(test|prod)_(.+)_(task\d+chat\d+msg\d+)$')
# nginx 容器命名：nginx_{client_id}_{host_key}
_TEST_NGINX_CONTAINER_RE = re.compile(r'^nginx_(\d+)_(task\d+chat\d+msg\d+)$')

# 超过多少秒视为过期（默认 1 天）
TEST_CONTAINER_EXPIRE_SECONDS = 24 * 60 * 60


def process_cleanup_expired_test_containers():
    """
    清理所有测试环境云服务器上超过 1 天的测试部署容器。

    识别规则：容器名包含 `task{task_id}chat{chat_id}msg{msg_id}` 片段，
    即 app_{cid}_test_{uuid}_{host_key} 与 nginx_{cid}_{host_key}。

    清理项（按 (client_id, host_key) 分组，组内最新容器创建时间超 1 天才清理）：
    - app 容器、nginx 容器
    - 对应 Docker 网络 network_{cid}_{host_key}
    - 宿主机 nginx vhost：/home/{user}/app{cid}/nginx/{host_key}.conf
    - 内层 nginx 配置目录：/home/{user}/app{cid}/nginx/container/{host_key}/
    - 若存在文件删除，最后 reload 宿主机 nginx
    """
    servers = get_all_active_servers_by_env(env='test')
    if not servers:
        return

    # 同一台物理服务器可能被多个 client 共用，按 (ip, username, password) 去重避免重复 SSH
    unique_servers: dict[tuple, object] = {}
    for server in servers:
        ip = (server.ip or '').strip()
        username = (server.name or '').strip()
        password = (server.password or '').strip()
        if not ip or not username:
            continue
        unique_servers.setdefault((ip, username, password), server)

    for (ip, username, password), _server in unique_servers.items():
        trace_id = str(uuid.uuid4())
        try:
            _cleanup_expired_test_containers_on_server(
                ip=ip, username=username, password=password, trace_id=trace_id,
            )
        except Exception:
            logger.exception(
                "[trace_id=%s] cleanup test containers failed on server ip=%s user=%s",
                trace_id, ip, username,
            )


def _cleanup_expired_test_containers_on_server(
    ip: str, username: str, password: str, trace_id: str,
) -> None:
    """在单台测试环境服务器上清理过期容器（主入口的 per-server 逻辑）。"""
    with SshClient(ip=ip, username=username, password=password, trace_id=trace_id) as ssh:
        # 单次 SSH 拉出所有候选容器名 + 创建时间（ISO）
        list_cmd = (
            "sudo docker ps -a --format '{{.Names}}' 2>/dev/null "
            "| grep -E 'task[0-9]+chat[0-9]+msg[0-9]+' "
            "| while read -r name; do "
            "  created=$(sudo docker inspect --format '{{.Created}}' \"$name\" 2>/dev/null); "
            "  if [ -n \"$created\" ]; then printf '%s|%s\\n' \"$name\" \"$created\"; fi; "
            "done"
        )
        raw = ssh.execute_ignore_error(command=list_cmd)
        if not raw:
            return

        # 按 (client_id, host_key) 分组候选容器
        groups: dict[tuple, dict] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or '|' not in line:
                continue
            name, created_str = line.split('|', 1)
            name = name.strip()
            created = _parse_docker_created_iso(created_str.strip())
            if not created:
                continue

            m_app = _TEST_APP_CONTAINER_RE.match(name)
            m_nginx = _TEST_NGINX_CONTAINER_RE.match(name)
            if m_app:
                cid = int(m_app.group(1))
                host_key = m_app.group(4)
                kind = 'app'
            elif m_nginx:
                cid = int(m_nginx.group(1))
                host_key = m_nginx.group(2)
                kind = 'nginx'
            else:
                continue

            g = groups.setdefault(
                (cid, host_key),
                {'app': [], 'nginx': [], 'latest_created': created},
            )
            g[kind].append(name)
            if created > g['latest_created']:
                g['latest_created'] = created

        if not groups:
            return

        now_utc = datetime.now(timezone.utc)
        expire_delta = timedelta(seconds=TEST_CONTAINER_EXPIRE_SECONDS)

        removed_any_vhost = False
        for (cid, host_key), g in groups.items():
            age = now_utc - g['latest_created']
            if age < expire_delta:
                continue

            logger.info(
                "[trace_id=%s] Cleanup expired test group: ip=%s client_id=%s host_key=%s age_s=%s",
                trace_id, ip, cid, host_key, int(age.total_seconds()),
            )

            # 删除 app + nginx 容器（容错：可能已被手动清理）
            containers = list(g['app']) + list(g['nginx'])
            if containers:
                names = ' '.join(containers)
                ssh.execute_ignore_error(command=f'sudo docker rm -f {names} 2>/dev/null')

            # 删除 Docker 网络（容器删除后网络才能移除）
            network_name = f'network_{cid}_{host_key}'
            ssh.execute_ignore_error(command=f'sudo docker network rm {network_name} 2>/dev/null')

            # 删除宿主机 vhost 与内层 nginx 配置目录
            base_dir = f'/home/{username}/app{cid}'
            host_conf_path = f'{base_dir}/nginx/{host_key}.conf'
            inner_conf_dir = f'{base_dir}/nginx/container/{host_key}'
            check_vhost = ssh.execute_ignore_error(
                command=f'test -f {host_conf_path} && echo "exists" || echo "missing"',
            )
            if 'exists' in check_vhost:
                removed_any_vhost = True
            ssh.execute_ignore_error(command=f'sudo rm -f {host_conf_path}')
            ssh.execute_ignore_error(command=f'sudo rm -rf {inner_conf_dir}')

        # 只要有 vhost 文件被删除，就需要 reload 宿主机 nginx 让变更生效
        if removed_any_vhost:
            try:
                _reload_host_nginx(ssh=ssh, trace_id=trace_id)
            except Exception:
                logger.exception(
                    "[trace_id=%s] reload host nginx failed after test cleanup ip=%s",
                    trace_id, ip,
                )


def _parse_docker_created_iso(s: str):
    """
    解析 `docker inspect --format '{{.Created}}'` 输出的 ISO 时间。

    Docker 输出形如 `2024-01-15T12:34:56.123456789Z`，亚秒可能到纳秒；
    Python `datetime.fromisoformat` 在 3.11 前不支持 `Z` 且亚秒上限为微秒，
    这里统一做归一化后解析，失败返回 None。
    """
    s = (s or '').strip()
    if not s:
        return None
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    if '.' in s:
        # 仅处理日期时间主体后的第一个小数点（时区偏移不含小数）
        head, rest = s.split('.', 1)
        digits = []
        i = 0
        while i < len(rest) and rest[i].isdigit():
            digits.append(rest[i])
            i += 1
        frac = ''.join(digits)[:6]  # 截断到微秒
        suffix = rest[i:]
        s = f'{head}.{frac}{suffix}' if frac else f'{head}{suffix}'
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
