#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端业务逻辑服务层
"""

import logging
import socket
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from urllib.parse import urlparse

from dao.client_dao import (
    add_client_database,
    apply_client_env_var_sync,
    apply_client_repo_sync,
    check_client_name_exists,
    check_client_name_exists_exclude,
    count_cloud_deploy_clients,
    create_client,
    get_client_by_id,
    get_client_env_vars,
    get_client_repos,
    increment_client_version,
    update_client,
    update_client_database,
    VALID_ENVS,
)
from dao.heartbeat_dao import get_heartbeat, get_heartbeats_by_user, add_heartbeat, update_heartbeat
from dao.models import ClientEnvVar, ClientRepo
from service import permission_service

logger = logging.getLogger(__name__)

# Agent 可选项（与路由 /agents 一致）
AVAILABLE_AGENTS = ['claude sdk', 'claude cli']


class ClientSaveError(Exception):
    """客户端整单保存失败（校验、冲突、子步骤错误等）"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ClientRepoSaveError(Exception):
    """仓库配置保存校验失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ClientEnvVarSaveError(Exception):
    """环境变量同步校验失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _strip_str(val) -> str:
    if val is None:
        return ''
    return str(val).strip()


def _parse_bit_flag(val, field_label: str):
    """解析 0/1：int 0/1，或 strip 后为 '0'/'1' 的字符串。"""
    if isinstance(val, int):
        if val in (0, 1):
            return True, val
        return False, f'{field_label}仅支持 0 或 1'
    s = _strip_str(val)
    if not s:
        return False, f'{field_label}不能为空'
    if s == '0':
        return True, 0
    if s == '1':
        return True, 1
    return False, f'{field_label}仅支持 0 或 1'


def _normalize_client_payload(data: dict) -> Optional[str]:
    """
    就地规范化客户端写请求 body。通过返回 None，失败返回错误文案。
    """
    name = _strip_str(data.get('name', ''))
    if not name:
        return '客户端名称不能为空'
    if len(name) > 16:
        return '客户端名称长度不能超过16个字符'
    data['name'] = name

    agent = _strip_str(data.get('agent'))
    if not agent:
        data['agent'] = AVAILABLE_AGENTS[0]
    elif agent not in AVAILABLE_AGENTS:
        return f'无效的Agent类型，可选值: {", ".join(AVAILABLE_AGENTS)}'
    else:
        data['agent'] = agent

    if 'official_cloud_deploy' not in data or data.get('official_cloud_deploy') is None:
        data['official_cloud_deploy'] = 0
    else:
        ok, v = _parse_bit_flag(data['official_cloud_deploy'], 'official_cloud_deploy')
        if not ok:
            return v
        data['official_cloud_deploy'] = v

    if 'repos' in data:
        repos = data.get('repos')
        if not isinstance(repos, list):
            return 'repos必须是数组'
        docs_repo_count = 0
        for idx, repo in enumerate(repos):
            repo_num = idx + 1
            if not isinstance(repo, dict):
                return f'仓库#{repo_num} 格式无效'
            url = _strip_str(repo.get('url'))
            desc = _strip_str(repo.get('desc'))
            token_raw = repo.get('token')
            token = _strip_str(token_raw) if token_raw is not None else ''
            default_branch = _strip_str(repo.get('default_branch'))
            branch_prefix = _strip_str(repo.get('branch_prefix')) or 'ai_'

            if not url:
                return f'仓库#{repo_num} URL不能为空'
            if not desc:
                return f'仓库#{repo_num} 简介不能为空'
            if url.startswith('http') and not token:
                return f'仓库#{repo_num} 使用HTTP地址时token必填'

            repo['url'] = url
            repo['desc'] = desc
            repo['token'] = token if token else None
            repo['default_branch'] = default_branch
            repo['branch_prefix'] = branch_prefix
            repo['docs_repo'] = bool(repo.get('docs_repo'))
            if repo['docs_repo']:
                docs_repo_count += 1

        if docs_repo_count == 0:
            return '必须指定一个文档仓库'
        if docs_repo_count > 1:
            return '只能指定一个文档仓库'
        non_docs_repo_count = len(repos) - docs_repo_count
        if non_docs_repo_count == 0:
            return '除文档仓库外，至少需要一个代码仓库'

    if 'env_vars' in data:
        env_vars_body = data.get('env_vars')
        if not isinstance(env_vars_body, list):
            return 'env_vars必须是数组'
        seen_keys: set = set()
        for idx, item in enumerate(env_vars_body):
            if not isinstance(item, dict):
                return f'环境变量#{idx + 1} 格式无效'
            key = _strip_str(item.get('key'))
            val = item.get('value')
            if val is None:
                value_s = ''
            elif isinstance(val, str):
                value_s = val.strip()
            else:
                value_s = _strip_str(val)
            if not key:
                return f'环境变量#{idx + 1} 变量名不能为空'
            env_val = _strip_str(item.get('env', '')) or None
            if env_val and env_val not in ('test', 'prod'):
                return f'环境变量#{idx + 1} env 值无效，只支持 test/prod'
            # env + key 组合不重复
            dedup_key = f'{env_val or ""}:{key}'
            if dedup_key in seen_keys:
                return f'环境变量中存在重复的键（同环境）: {key}'
            if not value_s:
                return f'环境变量#{idx + 1} 变量值不能为空'
            seen_keys.add(dedup_key)
            item['key'] = key
            item['value'] = value_s
            item['env'] = env_val

    return None


def get_client_detail(client_id: int, user_id: int) -> Optional[dict]:
    """
    组装客户端详情（与 GET /<client_id> 响应 data 一致）：
    基本信息、editable、last_sync_at（合并心跳）、repos、env_vars、infrastructure。
    客户端不存在或无权访问时返回 None。
    """
    client = get_client_by_id(client_id=client_id, user_id=user_id)
    if not client:
        return None
    heartbeats = get_heartbeats_by_user(user_id, client_id=client_id)
    payload = client.to_dict()
    payload['editable'] = client.user_id == user_id
    if heartbeats:
        payload['last_sync_at'] = heartbeats[0].get('last_sync_at')
    payload['repos'] = [repo.to_dict() for repo in get_client_repos(client_id, user_id)]
    payload['env_vars'] = [ev.to_dict() for ev in get_client_env_vars(client_id, user_id)]
    payload['infrastructure'] = get_client_infrastructure(client_id=client_id, user_id=user_id)
    return payload


def _ensure_utc_aware(dt: datetime) -> datetime:
    """将历史 datetime 统一为 UTC aware，避免 naive/aware 混算异常。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def update_client_heartbeat(
    user_id: int,
    client_id: int,
    instance_uuid: str,
    timeout_seconds: int = 3,
) -> Tuple[bool, str]:
    """
    客户端心跳业务逻辑：
    - 首次心跳：允许并创建/更新记录
    - 相同 instance_uuid：更新时间
    - 不同 instance_uuid：
        - last_sync_at 距今 < timeout_seconds：拒绝接管（防止短时间多实例）
        - 否则：允许接管（更新 instance_uuid + last_sync_at）
    """
    now = datetime.now(timezone.utc)

    heartbeat = get_heartbeat(user_id=user_id, client_id=client_id)
    if heartbeat is None:
        # 首次：允许并创建记录
        add_heartbeat(user_id=user_id, client_id=client_id, instance_uuid=instance_uuid)
        return True, ""

    # 相同 UUID：只更新时间（允许 upsert）
    if heartbeat.instance_uuid == instance_uuid:
        update_heartbeat(user_id=user_id, client_id=client_id, instance_uuid=instance_uuid)
        return True, ""

    # 不同 UUID：历史脏数据可能存在 last_sync_at 为空，直接按可接管处理
    if heartbeat.last_sync_at is None:
        update_heartbeat(user_id=user_id, client_id=client_id, instance_uuid=instance_uuid)
        return True, ""

    last_sync_at = _ensure_utc_aware(heartbeat.last_sync_at)
    time_diff = (now - last_sync_at).total_seconds()

    if time_diff < timeout_seconds:
        if timeout_seconds == 3:
            return False, "不同实例启动的客户端需要间隔三秒，确保不会同时启动多个实例"
        return (
            False,
            f"不同实例启动的客户端需要间隔{timeout_seconds}秒，确保不会同时启动多个实例",
        )

    update_heartbeat(user_id=user_id, client_id=client_id, instance_uuid=instance_uuid)
    return True, ""


def save_client(user_id: int, data: dict, client_id: Optional[int] = None) -> int:
    """
    规范化 body（就地），写入客户端基础信息；若 data 含 repos / env_vars 则全量同步并视变更 bump version。

    Args:
        user_id: 当前用户
        data: 请求 JSON（会被 _normalize_client_payload 修改）
        client_id: None 为新建，否则为更新指定客户端

    Returns:
        保存后的客户端 ID（新建为新建 ID，更新为传入的 client_id）。

    Raises:
        ClientSaveError
    """
    err = _normalize_client_payload(data)
    if err:
        raise ClientSaveError(err)

    name = data['name']
    agent = data['agent']
    official_cloud_deploy = data['official_cloud_deploy']

    # 云部署应用数量限制校验
    if official_cloud_deploy == 1:
        current_count = count_cloud_deploy_clients(
            user_id=user_id,
            exclude_client_id=client_id,
        )
        result = permission_service.check(
            user_id=user_id,
            key='official_cloud_client_count',
            params=current_count,
        )
        if not result.passed:
            raise ClientSaveError(result.message)

    if client_id is None:
        if check_client_name_exists(user_id, name):
            raise ClientSaveError('客户端名称已存在')
        new_id = create_client(
            user_id, name, agent=agent, official_cloud_deploy=official_cloud_deploy
        )
        cid = new_id
    else:
        cid = client_id
        if not get_client_by_id(client_id=cid, user_id=user_id):
            raise ClientSaveError('客户端不存在')
        if check_client_name_exists_exclude(user_id, name, cid):
            raise ClientSaveError('客户端名称已存在')
        update_client(
            client_id=cid,
            user_id=user_id,
            name=name,
            agent=agent,
            official_cloud_deploy=official_cloud_deploy,
        )

    save_client_repos(cid, data.get('repos', []), user_id=user_id)
    env_vars_changed = save_client_env_vars(cid, data.get('env_vars', []), user_id=user_id)

    if env_vars_changed:
        # 目前只有环境变量出现变更的时候，才有可能影响到客户端的执行版本号，所以这里直接调用 increment_client_version
        increment_client_version(cid, user_id)

    # 保存基础设施配置（若 data 中含 infrastructure 字段）
    infra_data = data.get('infrastructure')
    if infra_data and isinstance(infra_data, dict):
        save_all_infrastructure(client_id=cid, user_id=user_id, data=infra_data)

    return cid


def parse_repo_name_from_url(url: str) -> str:
    """
    从仓库 URL 解析仓库名（路径最后一段，去掉 .git）。
    支持 https/http、git@host:path、ssh:// 等形式。
    """
    u = (url or "").strip()
    if not u:
        raise ClientRepoSaveError("仓库 URL 不能为空")

    path = ""

    if u.startswith("git@") or (
        "@" in u and ":" in u and not u.startswith("http") and not u.startswith("ssh://")
    ):
        at = u.find("@")
        colon = u.find(":", at)
        if colon == -1:
            raise ClientRepoSaveError(f"无法从 URL 解析仓库名称: {u}")
        path = u[colon + 1 :]
    elif u.startswith("http://") or u.startswith("https://") or u.startswith("ssh://"):
        parsed = urlparse(u)
        path = parsed.path or ""
    else:
        path = u

    path = path.strip("/")
    if not path:
        raise ClientRepoSaveError(f"无法从 URL 解析仓库名称: {u}")

    parts = [p for p in path.split("/") if p]
    if not parts:
        raise ClientRepoSaveError(f"无法从 URL 解析仓库名称: {u}")

    name = parts[-1]
    if name.endswith(".git"):
        name = name[:-4]
    if not name:
        raise ClientRepoSaveError(f"无法从 URL 解析仓库名称: {u}")
    return name


def _row_fields_equal(existing: ClientRepo, incoming: dict) -> bool:
    ex_tok = "" if existing.token is None else existing.token
    in_tok = incoming.get("token")
    in_tok = "" if in_tok is None else in_tok
    return (
        (existing.desc or "") == (incoming.get("desc") or "")
        and (existing.url or "") == (incoming.get("url") or "")
        and ex_tok == in_tok
        and (existing.default_branch or "") == (incoming.get("default_branch") or "")
        and (existing.branch_prefix or "ai_") == (incoming.get("branch_prefix") or "ai_")
        and bool(existing.docs_repo) == bool(incoming.get("docs_repo", False))
    )


def _validate_docs_repo_policy(repos: List[dict]) -> None:
    docs_repo_count = sum(1 for r in repos if r.get("docs_repo"))
    if docs_repo_count == 0:
        raise ClientRepoSaveError("必须指定一个文档仓库")
    if docs_repo_count > 1:
        raise ClientRepoSaveError("只能指定一个文档仓库")
    non_docs_repo_count = len(repos) - docs_repo_count
    if non_docs_repo_count == 0:
        raise ClientRepoSaveError("除文档仓库外，至少需要一个代码仓库")


def save_client_repos(
    client_id: int,
    repos: List[dict],
    *,
    user_id: int,
    require_docs_repo: bool = True,
) -> bool:
    """
    按「仓库名（由 URL 解析）」全量同步仓库配置：删除输入中不存在的、
    新增未有记录、已存在且字段有变化则更新。

    Returns:
        是否发生了任意持久化变更（用于决定是否 bump 客户端版本等）

    Raises:
        ClientRepoSaveError: 解析失败、提交列表同名冲突、文档仓库策略不满足等
    """
    if require_docs_repo:
        _validate_docs_repo_policy(repos)

    existing_list = get_client_repos(client_id, user_id)
    exist_repos: Dict[str, ClientRepo] = {}
    delete_ids: List[int] = []
    for er in existing_list:
        key = parse_repo_name_from_url(er.url).lower()
        if key in exist_repos:
            delete_ids.append(er.id)
        else:
            exist_repos[key] = er

    input_repos: Dict[str, dict] = {}
    for repo in repos:
        url = (repo.get("url") or "").strip()
        if not url:
            raise ClientRepoSaveError("提交的仓库列表中存在 URL 为空的仓库")
        key = parse_repo_name_from_url(url).lower()
        if key in input_repos:
            raise ClientRepoSaveError("提交的仓库列表中存在重复的仓库（由 URL 解析）：" + key)
        input_repos[key] = repo

    updates: List[dict] = []
    inserts: List[dict] = []

    for key, inc in input_repos.items():
        ex = exist_repos.get(key)
        if ex is None:
            inserts.append(
                {
                    "desc": inc.get("desc", ""),
                    "url": inc.get("url", ""),
                    "token": inc.get("token"),
                    "default_branch": inc.get("default_branch", ""),
                    "branch_prefix": inc.get("branch_prefix", "ai_"),
                    "docs_repo": inc.get("docs_repo", False),
                }
            )
        elif not _row_fields_equal(ex, inc):
            updates.append(
                {
                    "id": ex.id,
                    "desc": inc.get("desc", ""),
                    "url": inc.get("url", ""),
                    "token": inc.get("token"),
                    "default_branch": inc.get("default_branch", ""),
                    "branch_prefix": inc.get("branch_prefix", "ai_"),
                    "docs_repo": inc.get("docs_repo", False),
                }
            )

    if not delete_ids and not updates and not inserts:
        return False

    apply_client_repo_sync(
        client_id=client_id,
        user_id=user_id,
        delete_ids=delete_ids,
        updates=updates,
        inserts=inserts,
    )
    return True


def _env_value_equal(existing: ClientEnvVar, incoming_value: str) -> bool:
    return (existing.value or "") == (incoming_value or "")


def _env_var_env_equal(existing: ClientEnvVar, incoming_env) -> bool:
    """比较 env 字段是否相等（None 和 '' 视为相同）"""
    ex_env = existing.env or None
    in_env = incoming_env or None
    return ex_env == in_env


def save_client_env_vars(client_id: int, env_items: List[dict], *, user_id: int) -> bool:
    """
    以 (env, key) 组合为维度全量同步环境变量。
    - 请求体中不存在的已激活记录做软删除
    - 不存在则新增；已存在且 value/env 有变化则更新
    - 支持 env 字段（test/prod/None）

    Returns:
        是否发生了任意持久化变更（用于决定是否 bump 客户端版本等）

    Raises:
        ClientEnvVarSaveError: 空 key、提交列表中 key 重复等
    """
    # input: {(env, key): value}
    input_env_vars: Dict[tuple, str] = {}
    input_env_map: Dict[tuple, Optional[str]] = {}
    for item in env_items:
        k = (item.get("key") or "").strip()
        if not k:
            raise ClientEnvVarSaveError("环境变量名不能为空")
        v = item.get("value", "")
        if v is None:
            v = ""
        else:
            v = str(v)
        env_val = item.get("env") or None
        dedup_key = (env_val, k)
        if dedup_key in input_env_vars:
            raise ClientEnvVarSaveError("提交的环境变量中存在重复的键（同环境）: " + k)
        input_env_vars[dedup_key] = v
        input_env_map[dedup_key] = env_val

    existing_list = get_client_env_vars(client_id, user_id)
    exist_env_vars: Dict[tuple, ClientEnvVar] = {}
    delete_ids: List[int] = []
    for ev in existing_list:
        k = (ev.key or "").strip()
        if not k:
            delete_ids.append(ev.id)
            continue
        ev_env = ev.env or None
        dedup_key = (ev_env, k)
        if dedup_key in exist_env_vars:
            delete_ids.append(ev.id)
        else:
            exist_env_vars[dedup_key] = ev

    inserts: List[dict] = []
    updates: List[dict] = []

    for dedup_key, v in input_env_vars.items():
        env_val, k = dedup_key
        ex = exist_env_vars.get(dedup_key)
        if ex is None:
            inserts.append({"key": k, "value": v, "env": env_val})
        elif not _env_value_equal(ex, v) or not _env_var_env_equal(ex, env_val):
            updates.append({"id": ex.id, "key": k, "value": v, "env": env_val})

    for dedup_key, ex in exist_env_vars.items():
        if dedup_key not in input_env_vars:
            delete_ids.append(ex.id)

    if not delete_ids and not updates and not inserts:
        return False

    apply_client_env_var_sync(
        client_id=client_id,
        user_id=user_id,
        delete_ids=delete_ids,
        updates=updates,
        inserts=inserts,
    )
    return True


# ============================================================
# 基础设施配置服务函数
# ============================================================

SSH_CHECK_TIMEOUT = 5  # SSH 连通性检查超时秒数


class InfraConfigError(ClientSaveError):
    """基础设施配置校验或操作失败"""
    pass


def check_ssh_connectivity(ip: str, username: str, password: str) -> Tuple[bool, str]:
    """
    使用 paramiko 检查 SSH 连通性。

    Returns:
        (success: bool, error_message: str)
    """
    try:
        import paramiko
    except ImportError:
        logger.error("paramiko not installed, cannot check SSH connectivity")
        return False, "SSH 校验依赖未安装，请联系管理员"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ip,
            username=username,
            password=password,
            timeout=SSH_CHECK_TIMEOUT,
            allow_agent=False,
            look_for_keys=False,
        )
        client.close()
        logger.info("SSH connectivity check passed for ip=%s user=%s", ip, username)
        return True, ""
    except Exception as e:
        # 不在日志中打印 password
        logger.warning("SSH connectivity check failed for ip=%s user=%s: %s", ip, username, type(e).__name__)
        return False, f"SSH 连接失败（{ip}）：{type(e).__name__}: {str(e)}"
    finally:
        client.close()


def _is_valid_ip_address(value: str) -> bool:
    """判断字符串是否为合法的 IP 地址（IPv4 或 IPv6）"""
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def check_servers_ssh(servers_data: dict) -> Tuple[bool, str]:
    """
    对 servers_data 中所有非空 ip 的服务器配置进行校验。

    - 如果 ip 是合法的 IP 地址，执行 SSH 连通性检查
    - 如果 ip 不是合法 IP，检查是否为有效的、上架中的云服务器资源名称

    Args:
        servers_data: {"test": {"name": ..., "password": ..., "ip": ...}, "prod": {...}}

    Returns:
        (all_passed: bool, error_message: str)
    """
    from dao.resource_dao import get_online_resource_by_name
    from dao.models import Resource

    env_labels = {'test': '测试环境', 'prod': '生产环境'}
    for env_key, cfg in servers_data.items():
        if not isinstance(cfg, dict):
            continue
        ip = (cfg.get('ip') or '').strip()
        if not ip:
            continue
        label = env_labels.get(env_key, env_key)

        if _is_valid_ip_address(ip):
            # 是合法 IP 地址，执行 SSH 连通性校验
            name = (cfg.get('name') or '').strip()
            password = (cfg.get('password') or '').strip()
            ok, err = check_ssh_connectivity(ip=ip, username=name, password=password)
            if not ok:
                return False, f"{label} SSH 校验失败：{err}"
        else:
            # 不是 IP 地址，检查是否为有效的云服务器资源名称
            resource = get_online_resource_by_name(name=ip)
            if not resource or resource.type != Resource.TYPE_CLOUD_SERVER:
                return False, f"{label} 云服务器 \"{ip}\" 不是有效的IP地址，也不是有效的、上架中的云服务器资源名称"
    return True, ""


def save_client_infrastructure(
    client_id: int,
    user_id: int,
    infra_type: str,
    data: dict,
) -> None:
    """
    保存指定类型的基础设施配置（支持 servers/domains/databases/payments/oss）。

    Args:
        client_id: 客户端 ID
        user_id: 用户 ID
        infra_type: 配置类型（servers/domains/databases/payments/oss）
        data: {"test": {...}, "prod": {...}}

    Raises:
        InfraConfigError: 参数校验失败
    """
    from dao.client_dao import (
        upsert_client_server, delete_client_server_by_env,
        sync_client_domains,
        sync_client_databases,
        upsert_client_payment, delete_client_payment_by_env,
        upsert_client_oss, delete_client_oss_by_env,
    )

    for env_key in VALID_ENVS:
        env_data = data.get(env_key)

        if infra_type == 'servers':
            if env_data is None:
                continue
            if not isinstance(env_data, dict):
                raise InfraConfigError(f'{env_key} 云服务器配置格式无效')
            ip = (env_data.get('ip') or '').strip()
            name = (env_data.get('name') or '').strip()
            password = (env_data.get('password') or '').strip()
            if ip:
                upsert_client_server(
                    client_id=client_id,
                    user_id=user_id,
                    env=env_key,
                    name=name,
                    password=password,
                    ip=ip,
                )
            else:
                delete_client_server_by_env(client_id=client_id, user_id=user_id, env=env_key)

        elif infra_type == 'domains':
            if env_data is None:
                env_data = []
            if not isinstance(env_data, list):
                raise InfraConfigError(f'{env_key} 域名配置格式无效')
            sync_client_domains(
                client_id=client_id,
                user_id=user_id,
                env=env_key,
                domains=[str(d).strip() for d in env_data if str(d).strip()],
            )

        elif infra_type == 'databases':
            if env_data is None:
                env_data = []
            if not isinstance(env_data, list):
                raise InfraConfigError(f'{env_key} 数据库配置格式无效')
            for idx, db in enumerate(env_data):
                if not isinstance(db, dict):
                    raise InfraConfigError(f'{env_key} 数据库配置 #{idx + 1} 格式无效')
                db_type = (db.get('db_type') or 'mysql').strip()
                if db_type not in ('mysql',):
                    raise InfraConfigError(f'不支持的数据库类型：{db_type}')
                if not (db.get('host') or '').strip():
                    raise InfraConfigError(f'{env_key} 数据库 #{idx + 1} 地址不能为空')
            sync_client_databases(
                client_id=client_id,
                user_id=user_id,
                env=env_key,
                databases=env_data,
            )

        elif infra_type == 'payments':
            if env_data is None:
                continue
            if not isinstance(env_data, dict):
                raise InfraConfigError(f'{env_key} 支付配置格式无效')
            payment_type = (env_data.get('payment_type') or 'alipay').strip()
            if payment_type not in ('alipay',):
                raise InfraConfigError(f'不支持的支付类型：{payment_type}')
            has_content = any(
                (env_data.get(f) or '').strip()
                for f in ('appid', 'app_private_key', 'alipay_public_key', 'notify_url', 'return_url', 'gateway')
            )
            if has_content:
                upsert_client_payment(
                    client_id=client_id,
                    user_id=user_id,
                    env=env_key,
                    payment_type=payment_type,
                    fields=env_data,
                )
            else:
                delete_client_payment_by_env(client_id=client_id, user_id=user_id, env=env_key)

        elif infra_type == 'oss':
            if env_data is None:
                continue
            if not isinstance(env_data, dict):
                raise InfraConfigError(f'{env_key} 对象存储配置格式无效')
            oss_type = (env_data.get('oss_type') or 'cos').strip()
            if oss_type not in ('cos',):
                raise InfraConfigError(f'不支持的对象存储类型：{oss_type}')
            has_content = any(
                (env_data.get(f) or '').strip()
                for f in ('secret_id', 'secret_key', 'region', 'bucket')
            )
            if has_content:
                upsert_client_oss(
                    client_id=client_id,
                    user_id=user_id,
                    env=env_key,
                    oss_type=oss_type,
                    fields=env_data,
                )
            else:
                delete_client_oss_by_env(client_id=client_id, user_id=user_id, env=env_key)

        else:
            raise InfraConfigError(f'未知配置类型：{infra_type}')


def get_client_infrastructure(client_id: int, user_id: int) -> dict:
    """
    获取客户端全量基础设施配置。

    Returns:
        {
            "servers": {"test": {...}, "prod": {...}},
            "domains": {"test": [...], "prod": [...]},
            "databases": {"test": [...], "prod": [...]},
            "payments": {"test": {...}, "prod": {...}},
            "oss": {"test": {...}, "prod": {...}},
        }
    """
    from dao.client_dao import (
        get_client_servers, get_client_domains,
        get_client_databases, get_client_payment, get_client_oss,
    )

    servers_result: dict = {}
    for srv in get_client_servers(client_id=client_id, user_id=user_id):
        servers_result[srv.env] = srv.to_dict()

    domains_result: dict = {e: [] for e in VALID_ENVS}
    for dom in get_client_domains(client_id=client_id, user_id=user_id):
        domains_result.setdefault(dom.env, []).append(dom.domain)

    databases_result: dict = {e: [] for e in VALID_ENVS}
    for db in get_client_databases(client_id=client_id, user_id=user_id):
        databases_result.setdefault(db.env, []).append(db.to_dict())

    payments_result: dict = {}
    for pay in get_client_payment(client_id=client_id, user_id=user_id):
        payments_result[pay.env] = pay.to_dict()

    oss_result: dict = {}
    for oss in get_client_oss(client_id=client_id, user_id=user_id):
        oss_result[oss.env] = oss.to_dict()

    return {
        'servers': servers_result,
        'domains': domains_result,
        'databases': databases_result,
        'payments': payments_result,
        'oss': oss_result,
    }


def save_all_infrastructure(client_id: int, user_id: int, data: dict) -> None:
    """
    一次性保存全量基础设施配置（云服务器、域名、数据库、支付、对象存储）。

    Args:
        client_id: 客户端 ID
        user_id: 用户 ID
        data: {
            "servers": {"test": {...}, "prod": {...}},
            "domains": {"test": [...], "prod": [...]},
            "databases": {"test": [...], "prod": [...]},
            "payments": {"test": {...}, "prod": {...}},
            "oss": {"test": {...}, "prod": {...}}
        }

    Raises:
        InfraConfigError: 参数校验或 SSH 校验失败
    """
    servers_data = data.get('servers') or {}
    domains_data = data.get('domains') or {}
    databases_data = data.get('databases') or {}
    payments_data = data.get('payments') or {}
    oss_data = data.get('oss') or {}

    # SSH 连通性校验（仅在有服务器 ip 时）
    if servers_data:
        ssh_ok, ssh_err = check_servers_ssh(servers_data)
        if not ssh_ok:
            raise InfraConfigError(ssh_err)

    # 逐类型保存
    if servers_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='servers', data=servers_data)
    if domains_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='domains', data=domains_data)
    if databases_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='databases', data=databases_data)
    if payments_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='payments', data=payments_data)
    if oss_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='oss', data=oss_data)


VALID_APP_TYPES = ['web']


def create_client_from_template(user_id: int, app_types: list, app_name: str = '') -> int:
    """
    从模板生成默认应用：创建 Client，创建默认仓库（文档 + 代码），然后在 test/prod 环境下各创建一个默认数据库。

    流程：
    1. 使用用户指定的名称或自动生成不重复的应用名称
    2. 创建 Client 记录
    3. 创建默认仓库：
       a. 随机选择一个 code_repo 类型的资源
       b. 文档仓库 {user_id}_docs：检查是否已存在，不存在则创建
       c. 代码仓库 {user_id}_app_{timestamp}：创建新仓库
       d. 绑定仓库到应用
    4. 对于 test、prod 两个环境：
       a. 查找可用的 MySQL Resource
       b. 生成 db_name = {env}_{user_id}_{yyyyMMddHHmmss}
       c. 写入 ClientDatabase 记录
       d. 调用资源服务在云上创建数据库 + 专属账号
       e. 回写连接信息到 ClientDatabase

    Args:
        user_id: 用户 ID
        app_types: 应用形态列表，如 ["web"]
        app_name: 用户指定的应用名称，为空则自动生成

    Returns:
        新创建的客户端 ID

    Raises:
        ClientSaveError: 校验失败或创建失败
    """
    import random
    import time
    from datetime import datetime, timezone
    from dao.resource_dao import get_online_resources_by_type_source
    from service.resource_mysql_service import create_database_with_name, ResourceMySQLError

    # 校验 app_types
    if not app_types or not isinstance(app_types, list):
        raise ClientSaveError('请选择至少一种应用形态')
    for at in app_types:
        if at not in VALID_APP_TYPES:
            raise ClientSaveError(f'不支持的应用形态：{at}')

    # 确定应用名称：优先使用用户指定名称，否则自动生成
    timestamp = int(time.time())
    if app_name:
        client_name = app_name[:16]
        if check_client_name_exists(user_id, client_name):
            raise ClientSaveError(f'应用名称 "{client_name}" 已存在，请更换名称')
    else:
        base_name = f"默认应用_{timestamp}"
        client_name = base_name[:16]
        retries = 0
        while check_client_name_exists(user_id, client_name):
            retries += 1
            if retries > 5:
                raise ClientSaveError('应用名称生成失败，请稍后重试')
            suffix = f"_{retries}"
            client_name = base_name[:16 - len(suffix)] + suffix

    # 创建 Client
    client_id = create_client(
        user_id=user_id,
        name=client_name,
        agent=AVAILABLE_AGENTS[0],
        official_cloud_deploy=0,
    )
    logger.info(
        "create_client_from_template: user_id=%s, client_id=%s, app_types=%s",
        user_id, client_id, app_types,
    )

    # 创建默认仓库
    _create_default_repos(
        user_id=user_id,
        client_id=client_id,
        timestamp=timestamp,
    )

    # 为 test、prod 两个环境分别创建数据库
    for env in VALID_ENVS:
        # 从资源管理中获取可用的 MySQL 资源
        resources = get_online_resources_by_type_source(
            type='mysql',
            source='aliyun',
            env=env,
        )
        if not resources:
            logger.warning(
                "create_client_from_template: no mysql resource for env=%s, user_id=%s, skipping",
                env, user_id,
            )
            continue

        resource = resources[0]
        db_name = f"{env}_{user_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        # 先写入 ClientDatabase 记录
        record_id = add_client_database(
            client_id=client_id,
            user_id=user_id,
            env=env,
            db_name=db_name,
            db_type='mysql',
        )
        logger.info(
            "create_client_from_template: db record created, record_id=%s, env=%s, db_name=%s, resource_id=%s",
            record_id, env, db_name, resource.id,
        )

        # 调用资源服务在云上创建数据库 + 专属账号
        try:
            result = create_database_with_name(
                resource=resource,
                user_id=user_id,
                db_name=db_name,
            )
            # 回写连接信息
            update_client_database(
                record_id=record_id,
                user_id=user_id,
                host=result['instance_url'],
                port=result.get('port', 3306),
                username=result['account_name'],
                password=result['account_password'],
            )
            logger.info(
                "create_client_from_template: cloud db created, env=%s, db_name=%s, host=%s",
                env, db_name, result['instance_url'],
            )
        except ResourceMySQLError as e:
            logger.error(
                "create_client_from_template: cloud db creation failed, env=%s, db_name=%s, resource_id=%s, error=%s",
                env, db_name, resource.id, e.message,
            )
            # 不阻断其他环境的创建，继续处理

    # 为 test、prod 两个环境分别分配云服务器资源
    from dao.client_dao import upsert_client_server
    for env in VALID_ENVS:
        cloud_server_resources = get_online_resources_by_type_source(
            type='cloud_server',
            source='tencent_cloud',
            env=env,
        )
        if not cloud_server_resources:
            logger.warning(
                "create_client_from_template: no cloud_server resource for env=%s, user_id=%s, skipping",
                env, user_id,
            )
            continue

        selected = random.choice(cloud_server_resources)
        upsert_client_server(
            client_id=client_id,
            user_id=user_id,
            env=env,
            name='',
            password='',
            ip=selected.name,
        )
        logger.info(
            "create_client_from_template: cloud server assigned, env=%s, resource_name=%s, resource_id=%s",
            env, selected.name, selected.id,
        )

    return client_id


def _create_default_repos(user_id: int, client_id: int, timestamp: int) -> None:
    """
    为默认应用创建文档仓库和代码仓库。

    流程：
    1. 随机选择一个 code_repo 类型的资源
    2. 文档仓库（{user_id}_docs）：检查数据库是否已存在，不存在则创建
    3. 代码仓库（{user_id}_app_{timestamp}）：直接创建
    4. 绑定仓库记录到应用

    Args:
        user_id: 用户 ID
        client_id: 客户端 ID
        timestamp: 秒级时间戳（用于代码仓库命名）
    """
    import random
    from dao.resource_dao import get_online_resources_by_type_source
    from service.github_service import (
        GitHubServiceError, build_repo_url,
    )

    # 1. 随机选择一个 code_repo 资源
    code_repo_resources = get_online_resources_by_type_source(
        type='code_repo',
        source='github',
    )
    if not code_repo_resources:
        logger.warning(
            "_create_default_repos: no code_repo resource available, user_id=%s, skipping repo creation",
            user_id,
        )
        return

    resource = random.choice(code_repo_resources)
    extra = resource.get_raw_extra()
    organization = (extra.get('organization') or '').strip()
    if not organization:
        logger.error(
            "_create_default_repos: code_repo resource id=%s missing organization, user_id=%s",
            resource.id, user_id,
        )
        return

    # 2. 文档仓库: {user_id}_docs
    docs_repo_name = f"{user_id}_docs"
    _ensure_and_bind_repo(
        resource=resource,
        organization=organization,
        user_id=user_id,
        client_id=client_id,
        repo_name=docs_repo_name,
        is_docs_repo=True,
        description=f"用户 {user_id} 的文档仓库",
    )

    # 3. 代码仓库: {user_id}_app_{timestamp}（从模板创建）
    code_repo_name = f"{user_id}_app_{timestamp}"
    _ensure_and_bind_repo(
        resource=resource,
        organization=organization,
        user_id=user_id,
        client_id=client_id,
        repo_name=code_repo_name,
        is_docs_repo=False,
        description=f"用户 {user_id} 的代码仓库",
        template_owner='MisakaMikotoStudio',
        template_repo='template',
    )


def _ensure_and_bind_repo(
    resource,
    organization: str,
    user_id: int,
    client_id: int,
    repo_name: str,
    is_docs_repo: bool,
    description: str,
    template_owner: str = '',
    template_repo: str = '',
) -> None:
    """
    确保仓库存在并绑定到应用。

    流程：
    1. 拼接仓库 URL，查询数据库是否已有记录
    2. 如果已存在：直接绑定到当前应用（新增一条 ClientRepo 记录指向同一 URL）
    3. 如果不存在：
       a. 先新建数据库记录
       b. 调用 GitHub API 创建仓库（若指定模板则优先从模板创建）
       c. 创建仓库 scoped token
       d. 回写 token 和 default_branch 到数据库记录

    Args:
        resource: Resource 对象
        organization: GitHub 组织名
        user_id: 用户 ID
        client_id: 客户端 ID
        repo_name: 仓库名称
        is_docs_repo: 是否为文档仓库
        description: 仓库描述
        template_owner: 模板仓库所有者（为空则创建空仓库）
        template_repo: 模板仓库名称（为空则创建空仓库）
    """
    from dao.client_dao import get_repo_by_url, add_client_repo, update_client_repo_after_creation
    from service.github_service import GitHubServiceError, setup_repo_for_user, build_repo_url

    repo_url = build_repo_url(organization=organization, repo_name=repo_name)
    repo_type_label = "文档仓库" if is_docs_repo else "代码仓库"

    # 检查数据库是否已有该 URL 的记录
    existing_repo = get_repo_by_url(user_id=user_id, url=repo_url)

    if existing_repo:
        # 仓库已存在，直接绑定到当前应用
        logger.info(
            "_ensure_and_bind_repo: repo already exists, binding to client, "
            "user_id=%s, client_id=%s, repo_url=%s, existing_repo_id=%s",
            user_id, client_id, repo_url, existing_repo.id,
        )
        add_client_repo(
            client_id=client_id,
            user_id=user_id,
            url=existing_repo.url,
            desc=existing_repo.desc or description,
            token=existing_repo.token,
            default_branch=existing_repo.default_branch or 'main',
            branch_prefix=existing_repo.branch_prefix or 'ai_',
            docs_repo=is_docs_repo,
        )
        return

    # 仓库不存在，先创建数据库记录
    repo_record_id = add_client_repo(
        client_id=client_id,
        user_id=user_id,
        url=repo_url,
        desc=description,
        token=None,
        default_branch='main',
        branch_prefix='ai_',
        docs_repo=is_docs_repo,
    )
    logger.info(
        "_ensure_and_bind_repo: db record created, repo_record_id=%s, user_id=%s, "
        "repo_name=%s, type=%s",
        repo_record_id, user_id, repo_name, repo_type_label,
    )

    # 调用 GitHub API 创建仓库 + 生成 token
    try:
        result = setup_repo_for_user(
            resource=resource,
            user_id=user_id,
            repo_name=repo_name,
            description=description,
            is_docs_repo=is_docs_repo,
            template_owner=template_owner,
            template_repo=template_repo,
        )

        # 回写 token 和 default_branch 到数据库记录
        token = result.get('token', '')
        default_branch = result.get('default_branch', 'main')
        if token or default_branch != 'main':
            update_client_repo_after_creation(
                repo_id=repo_record_id,
                user_id=user_id,
                token=token,
                default_branch=default_branch,
            )

        logger.info(
            "_ensure_and_bind_repo: github repo created, user_id=%s, repo=%s/%s, type=%s, default_branch=%s",
            user_id, organization, repo_name, repo_type_label, default_branch,
        )
    except GitHubServiceError as e:
        logger.error(
            "_ensure_and_bind_repo: github repo creation failed, user_id=%s, "
            "repo_name=%s, type=%s, error=%s",
            user_id, repo_name, repo_type_label, e.message,
        )
        # 不阻断应用创建流程，继续处理


def generate_default_database(user_id: int, config) -> dict:
    """
    在默认数据库实例上为用户创建一个新数据库。

    数据库命名规则：u{user_id}_app_{version}，version 从 1 开始递增，
    直到找到一个不存在的数据库名称。

    Args:
        user_id: 用户 ID
        config: DefaultDatabaseConfig 对象

    Returns:
        dict: 数据库配置信息 {db_type, host, port, username, password, db_name}

    Raises:
        ClientSaveError: 功能未启用或创建失败
    """
    import pymysql

    admin_conn = None
    try:
        admin_conn = pymysql.connect(
            host=config.url,
            port=config.port,
            user=config.admin_username,
            password=config.admin_password,
            connect_timeout=10,
        )
        cursor = admin_conn.cursor()

        # 查询已存在的数据库列表
        cursor.execute("SHOW DATABASES")
        existing_dbs = {row[0] for row in cursor.fetchall()}

        # 生成数据库名称：u{user_id}_app_{version}，以字母开头（Aliyun RDS 要求）
        version = 1
        while True:
            db_name = f"u{user_id}_app_{version}"
            if db_name not in existing_dbs:
                break
            version += 1
            if version > 9999:
                raise ClientSaveError('数据库名称生成失败：版本号超出上限')

        # 创建数据库
        cursor.execute(
            f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
        )

        # 授权应用账号访问新数据库
        app_username = config.app_username
        app_password = config.app_password
        if app_username:
            cursor.execute(
                f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO %s@'%%'",
                (app_username,)
            )
            cursor.execute("FLUSH PRIVILEGES")

        admin_conn.commit()

        logger.info(
            "Default database created: db_name=%s, user_id=%s, host=%s",
            db_name, user_id, config.url,
        )

        return {
            'db_type': 'mysql',
            'host': config.url,
            'port': config.port,
            'username': app_username or config.admin_username,
            'password': app_password or config.admin_password,
            'db_name': db_name,
        }

    except pymysql.Error as e:
        logger.error(
            "Failed to create default database: user_id=%s, error=%s",
            user_id, str(e),
        )
        raise ClientSaveError(f'创建数据库失败：{str(e)}')
    finally:
        if admin_conn:
            admin_conn.close()
