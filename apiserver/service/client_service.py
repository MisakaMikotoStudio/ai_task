#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端业务逻辑服务层
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from dao.client_dao import (
    check_client_name_exists,
    check_client_name_exists_exclude,
    count_cloud_deploy_clients,
    create_client,
    get_client_by_id,
    get_client_env_vars,
    get_client_repos,
    increment_client_version,
    update_client,
)
from dao.heartbeat_dao import get_heartbeat, get_heartbeats_by_user, add_heartbeat, update_heartbeat
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
    基本信息、editable、last_sync_at（合并心跳）、repos、env_vars、infrastructure。
    客户端不存在或无权访问时返回 None。
    """
    from service.client_infra_service import get_client_infrastructure

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
    from service.client_config_service import save_client_repos, save_client_env_vars
    from service.client_infra_service import save_all_infrastructure

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


# ---------------------------------------------------------------------------
# Backward-compatible re-exports: symbols that moved to sub-modules but may
# be imported from client_service by other parts of the codebase.
# ---------------------------------------------------------------------------
from service.client_infra_service import (  # noqa: E402, F401
    InfraConfigError,
    check_servers_ssh,
    check_ssh_connectivity,
    get_client_infrastructure,
    save_all_infrastructure,
    save_client_infrastructure,
)
from service.client_template_service import (  # noqa: E402, F401
    create_client_from_template,
)
from service.client_config_service import (  # noqa: E402, F401
    parse_repo_name_from_url,
    save_client_env_vars,
    save_client_repos,
)
from service.deploy_service import (  # noqa: E402, F401
    DeployConfigError,
    execute_deploy,
    generate_deploy_toml,
    save_deploy_configs,
)
