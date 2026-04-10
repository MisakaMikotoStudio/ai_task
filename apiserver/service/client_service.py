#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端业务逻辑服务层
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dao.client_dao import (
    apply_client_env_var_sync,
    apply_client_repo_sync,
    check_client_name_exists,
    check_client_name_exists_exclude,
    create_client,
    get_client_by_id,
    get_client_env_vars,
    get_client_repos,
    increment_client_version,
    update_client,
)
from dao.heartbeat_dao import get_heartbeat, get_heartbeats_by_user, add_heartbeat, update_heartbeat
from dao.models import ClientEnvVar, ClientRepo

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
        seen_keys = set()
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
            if key in seen_keys:
                return f'环境变量中存在重复的键: {key}'
            if not value_s:
                return f'环境变量#{idx + 1} 变量值不能为空'
            seen_keys.add(key)
            item['key'] = key
            item['value'] = value_s

    return None


def get_client_detail(client_id: int, user_id: int) -> Optional[dict]:
    """
    组装客户端详情（与 GET /<client_id> 响应 data 一致）：
    基本信息、editable、last_sync_at（合并心跳）、repos、env_vars。
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


def save_client_env_vars(client_id: int, env_items: List[dict], *, user_id: int) -> bool:
    """
    以环境变量 key 为维度全量同步：请求体中不存在的已激活记录做软删除（deleted_at 使用 UTC）；
    不存在则新增；已存在且 value 有变化则更新。同一 key 在库中若有多条，保留一条其余软删除。

    Returns:
        是否发生了任意持久化变更（用于决定是否 bump 客户端版本等）

    Raises:
        ClientEnvVarSaveError: 空 key、提交列表中 key 重复等
    """
    input_env_vars: Dict[str, str] = {}
    for item in env_items:
        k = (item.get("key") or "").strip()
        if not k:
            raise ClientEnvVarSaveError("环境变量名不能为空")
        v = item.get("value", "")
        if v is None:
            v = ""
        else:
            v = str(v)
        if k in input_env_vars:
            raise ClientEnvVarSaveError("提交的环境变量中存在重复的键: " + k)
        input_env_vars[k] = v

    existing_list = get_client_env_vars(client_id, user_id)
    exist_env_vars: Dict[str, ClientEnvVar] = {}
    delete_ids: List[int] = []
    for ev in existing_list:
        k = (ev.key or "").strip()
        if not k:
            delete_ids.append(ev.id)
            continue
        if k in exist_env_vars:
            delete_ids.append(ev.id)
        else:
            exist_env_vars[k] = ev

    inserts: List[dict] = []
    updates: List[dict] = []

    for k, v in input_env_vars.items():
        ex = exist_env_vars.get(k)
        if ex is None:
            inserts.append({"key": k, "value": v})
        elif not _env_value_equal(ex, v):
            updates.append({"id": ex.id, "key": k, "value": v})

    for k, ex in exist_env_vars.items():
        if k not in input_env_vars:
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
