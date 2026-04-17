#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GitHub 代码仓库服务层 —— 业务逻辑（依赖 Resource 模型、DAO 查询）

功能：
- 从 Resource 中提取 GitHub App 配置并获取 Installation Token
- 组合流程：创建仓库 + 创建 scoped token
- Token 刷新

底层 GitHub REST API 操作委托给 utils.git_utils
"""

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from dao.models import Resource
from utils.git_utils import (
    GitHubServiceError,
    build_repo_url,
    create_installation_token,
    create_org_repo_api,
    create_org_repo_from_template_api,
    generate_app_jwt,
    get_installation_id,
    get_repo_id_api,
    parse_github_url,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────
#  Installation Token 缓存
#  - 避免每次 GitHub 操作都重新走 JWT → installation → token 流程
#  - 缓存 key: (organization, repo_name)；repo_name=None 表示 org 级 token
#  - 剩余有效期 > 10 分钟时复用，否则重新申请
# ──────────────────────────────────────────────────────
_TOKEN_REUSE_THRESHOLD = timedelta(minutes=10)
_token_cache: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
_token_cache_lock = threading.Lock()

# ──────────────────────────────────────────────────────
#  Installation ID 缓存
#  - installation_id 在组织生命周期内基本稳定，接近永不变更
#  - 按 (app_id, organization) 缓存，TTL 24h；遇到 404/401 再主动失效
# ──────────────────────────────────────────────────────
_INSTALLATION_ID_TTL = timedelta(hours=24)
_installation_id_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
_installation_id_cache_lock = threading.Lock()


def _get_cached_installation_id(app_id: str, organization: str) -> Optional[int]:
    """返回仍在 TTL 内的缓存 installation_id，过期则视为未命中。"""
    with _installation_id_cache_lock:
        entry = _installation_id_cache.get((app_id, organization))
        if not entry:
            return None
        if entry['cached_at'] + _INSTALLATION_ID_TTL < datetime.now(timezone.utc):
            return None
        return entry['installation_id']


def _set_cached_installation_id(app_id: str, organization: str, installation_id: int) -> None:
    with _installation_id_cache_lock:
        _installation_id_cache[(app_id, organization)] = {
            'installation_id': installation_id,
            'cached_at': datetime.now(timezone.utc),
        }


def invalidate_installation_id_cache(app_id: str, organization: str) -> None:
    """清除指定 (app_id, org) 的 installation_id 缓存（API 返回 404/401 时调用）。"""
    with _installation_id_cache_lock:
        _installation_id_cache.pop((app_id, organization), None)


def _resolve_installation_id(app_id: str, organization: str, jwt_token: str, trace_id: str = '') -> int:
    """
    获取 installation_id：优先命中 24h TTL 缓存，miss 时调用 API 并写入缓存。

    调用方负责传入已生成的 jwt_token（miss 时才真正发请求）；
    若未来遇到 GitHubServiceError 提示 App 未安装，应调用
    invalidate_installation_id_cache 强制下次重新查询。
    """
    cached = _get_cached_installation_id(app_id=app_id, organization=organization)
    if cached is not None:
        logger.info(
            "[trace_id=%s] installation_id cache hit: org=%s, installation_id=%s",
            trace_id, organization, cached,
        )
        return cached
    installation_id = get_installation_id(
        jwt_token=jwt_token, organization=organization, trace_id=trace_id,
    )
    _set_cached_installation_id(
        app_id=app_id, organization=organization, installation_id=installation_id,
    )
    return installation_id


def _get_cached_token(cache_key: Tuple[str, Optional[str]]) -> Optional[str]:
    """获取缓存中仍有效（剩余 > 10 分钟）的 token，否则返回 None。"""
    with _token_cache_lock:
        entry = _token_cache.get(cache_key)
        if not entry:
            return None
        expires_at: Optional[datetime] = entry.get('expires_at')
        if expires_at is None:
            return None
        if expires_at - datetime.now(timezone.utc) > _TOKEN_REUSE_THRESHOLD:
            return entry.get('token')
        return None


def _set_cached_token(cache_key: Tuple[str, Optional[str]], token: str, expires_at: Optional[datetime]) -> None:
    """写入 token 缓存；无 expires_at 时按 GitHub 默认 1 小时估算。"""
    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    with _token_cache_lock:
        _token_cache[cache_key] = {'token': token, 'expires_at': expires_at}


def invalidate_token_cache(organization: str, repo_name: Optional[str] = None) -> None:
    """清除指定 key 的 token 缓存（token 失效时可调用）。"""
    with _token_cache_lock:
        _token_cache.pop((organization, repo_name), None)

# 重导出，方便调用方从 service 层导入
__all__ = [
    'GitHubServiceError',
    'build_repo_url',
    'create_org_repo',
    'create_org_repo_from_template',
    'create_repo_scoped_token',
    'setup_repo_for_user',
    'refresh_repo_token_by_url',
    'invalidate_token_cache',
    'invalidate_installation_id_cache',
]


def _get_resource_config(resource: Resource) -> Dict[str, str]:
    """
    从 Resource 对象中提取 GitHub App 配置信息

    Args:
        resource: Resource 对象（type=code_repo, source=github）

    Returns:
        {"organization": "...", "app_id": "...", "private_key": "..."}

    Raises:
        GitHubServiceError: 配置缺失
    """
    extra = resource.get_raw_extra()
    organization = (extra.get('organization') or '').strip()
    app_id = (extra.get('app_id') or '').strip()
    private_key = (extra.get('private_key') or '').strip()

    if not organization:
        raise GitHubServiceError("资源缺少 GitHub Organization 配置")
    if not app_id:
        raise GitHubServiceError("资源缺少 GitHub App ID 配置")
    if not private_key:
        raise GitHubServiceError("资源缺少 GitHub App Private Key 配置")

    return {
        'organization': organization,
        'app_id': app_id,
        'private_key': private_key,
    }


def _get_installation_token_for_org(resource: Resource, trace_id: str = '') -> str:
    """
    获取组织级别的 Installation Access Token（用于创建仓库等操作）。

    优先复用缓存中剩余 > 10 分钟的 token；缓存未命中才重新生成。

    Args:
        resource: Resource 对象

    Returns:
        Installation Access Token

    Raises:
        GitHubServiceError: 获取失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']
    cache_key: Tuple[str, Optional[str]] = (organization, None)

    cached = _get_cached_token(cache_key=cache_key)
    if cached:
        logger.info(
            "[trace_id=%s] _get_installation_token_for_org: cache hit, org=%s",
            trace_id, organization,
        )
        return cached

    jwt_token = generate_app_jwt(app_id=config['app_id'], private_key_pem=config['private_key'], trace_id=trace_id)
    installation_id = _resolve_installation_id(
        app_id=config['app_id'], organization=organization,
        jwt_token=jwt_token, trace_id=trace_id,
    )
    result = create_installation_token(
        jwt_token=jwt_token,
        installation_id=installation_id,
        permissions={
            'administration': 'write',
            'contents': 'write',
            'metadata': 'read',
        },
        trace_id=trace_id,
    )
    _set_cached_token(cache_key=cache_key, token=result['token'], expires_at=result.get('expires_at'))
    logger.info(
        "[trace_id=%s] _get_installation_token_for_org: token created, org=%s, expires_at=%s",
        trace_id, organization,
        result['expires_at'].isoformat() if result.get('expires_at') else 'unknown',
    )
    return result['token']


def create_org_repo(resource: Resource, repo_name: str, description: str = '', private: bool = True, trace_id: str = '') -> Dict:
    """
    在 GitHub 组织下创建仓库。

    Args:
        resource: Resource 对象（type=code_repo, source=github）
        repo_name: 仓库名称
        description: 仓库描述
        private: 是否为私有仓库（默认 True）

    Returns:
        {"repo_name", "full_name", "url", "default_branch", "repo_id"}

    Raises:
        GitHubServiceError: 创建失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']
    installation_token = _get_installation_token_for_org(resource=resource, trace_id=trace_id)

    return create_org_repo_api(
        token=installation_token,
        organization=organization,
        repo_name=repo_name,
        description=description,
        private=private,
        trace_id=trace_id,
    )


def create_org_repo_from_template(
    resource: Resource,
    repo_name: str,
    template_owner: str,
    template_repo: str,
    description: str = '',
    private: bool = True,
    trace_id: str = '',
) -> Dict:
    """
    使用 GitHub 模板仓库 API 在组织下创建新仓库。

    Args:
        resource: Resource 对象（type=code_repo, source=github）
        repo_name: 新仓库名称
        template_owner: 模板仓库所有者
        template_repo: 模板仓库名称
        description: 仓库描述
        private: 是否为私有仓库

    Returns:
        {"repo_name", "full_name", "url", "default_branch", "repo_id"}

    Raises:
        GitHubServiceError: 创建失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']
    installation_token = _get_installation_token_for_org(resource=resource, trace_id=trace_id)

    return create_org_repo_from_template_api(
        token=installation_token,
        organization=organization,
        repo_name=repo_name,
        template_owner=template_owner,
        template_repo=template_repo,
        description=description,
        private=private,
        trace_id=trace_id,
    )


def create_repo_scoped_token(
    resource: Resource, repo_name: str, trace_id: str = '',
    return_cache_status: bool = False,
) -> Any:
    """
    为指定仓库创建仅具有 admin 权限的 Installation Access Token。

    优先复用缓存中剩余 > 10 分钟的 scoped token；缓存未命中才重新申请。

    Args:
        resource: Resource 对象
        repo_name: 仓库名称（不含组织前缀）
        return_cache_status: True 时返回 (token, 'reused'|'refreshed')；默认仅返回 token，保持向后兼容

    Returns:
        token: str（默认），或 (token, 'reused'|'refreshed')

    Raises:
        GitHubServiceError: 创建失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']
    cache_key: Tuple[str, Optional[str]] = (organization, repo_name)

    cached = _get_cached_token(cache_key=cache_key)
    if cached:
        logger.info(
            "[trace_id=%s] create_repo_scoped_token: cache hit, org=%s, repo=%s",
            trace_id, organization, repo_name,
        )
        return (cached, 'reused') if return_cache_status else cached

    jwt_token = generate_app_jwt(
        app_id=config['app_id'],
        private_key_pem=config['private_key'],
        trace_id=trace_id,
    )

    installation_id = _resolve_installation_id(
        app_id=config['app_id'],
        organization=organization,
        jwt_token=jwt_token,
        trace_id=trace_id,
    )

    # 3. 获取仓库 ID —— 优先复用缓存中的 org 级 token，否则临时申请一个用于查询
    org_cache_key: Tuple[str, Optional[str]] = (organization, None)
    org_token = _get_cached_token(cache_key=org_cache_key)
    if not org_token:
        org_result = create_installation_token(
            jwt_token=jwt_token,
            installation_id=installation_id,
            trace_id=trace_id,
        )
        org_token = org_result['token']
        # 将新申请的 org token 写入缓存，后续同组织的 scoped token 刷新或其它
        # org 级 API 调用（如 get_repo_id_api）可直接复用，减少一次 access_tokens 调用
        _set_cached_token(
            cache_key=org_cache_key,
            token=org_token,
            expires_at=org_result.get('expires_at'),
        )
    repo_id = get_repo_id_api(
        token=org_token,
        organization=organization,
        repo_name=repo_name,
        trace_id=trace_id,
    )

    scoped_result = create_installation_token(
        jwt_token=jwt_token,
        installation_id=installation_id,
        repository_ids=[repo_id],
        permissions={
            'contents': 'write',
            'metadata': 'read',
            'administration': 'write',
        },
        trace_id=trace_id,
    )
    _set_cached_token(
        cache_key=cache_key,
        token=scoped_result['token'],
        expires_at=scoped_result.get('expires_at'),
    )

    logger.info(
        "[trace_id=%s] create_repo_scoped_token: org=%s, repo=%s, repo_id=%s, token created, expires_at=%s",
        trace_id, organization, repo_name, repo_id,
        scoped_result['expires_at'].isoformat() if scoped_result.get('expires_at') else 'unknown',
    )
    token_value = scoped_result['token']
    return (token_value, 'refreshed') if return_cache_status else token_value


def setup_repo_for_user(
    resource: Resource,
    user_id: int,
    repo_name: str,
    description: str = '',
    is_docs_repo: bool = False,
    template_owner: str = '',
    template_repo: str = '',
    trace_id: str = '',
) -> Dict:
    """
    为用户创建仓库并生成 scoped access token 的组合流程。

    流程：
    1. 在组织下创建仓库（若指定模板则优先从模板创建，失败自动降级为空仓库）
    2. 为该仓库创建 scoped Installation Access Token
    3. 返回完整的仓库信息

    Args:
        resource: Resource 对象（type=code_repo, source=github）
        user_id: 用户 ID
        repo_name: 仓库名称
        description: 仓库描述
        is_docs_repo: 是否为文档仓库
        template_owner: 模板仓库所有者（为空则创建空仓库）
        template_repo: 模板仓库名称（为空则创建空仓库）

    Returns:
        {"repo_name", "full_name", "url", "token", "default_branch", "is_docs_repo"}

    Raises:
        GitHubServiceError: 创建失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']

    logger.info(
        "[trace_id=%s] setup_repo_for_user: user_id=%s, org=%s, repo=%s, is_docs=%s, template=%s/%s",
        trace_id, user_id, organization, repo_name, is_docs_repo,
        template_owner or '-', template_repo or '-',
    )

    # 1. 创建仓库（优先从模板创建，失败降级为空仓库）
    repo_type = "文档仓库" if is_docs_repo else "代码仓库"
    repo_desc = description or f"用户 {user_id} 的{repo_type}"

    repo_info = None
    if template_owner and template_repo:
        try:
            repo_info = create_org_repo_from_template(
                resource=resource,
                repo_name=repo_name,
                template_owner=template_owner,
                template_repo=template_repo,
                description=repo_desc,
                private=True,
                trace_id=trace_id,
            )
            logger.info(
                "[trace_id=%s] setup_repo_for_user: created from template %s/%s, user_id=%s, repo=%s",
                trace_id, template_owner, template_repo, user_id, repo_name,
            )
        except GitHubServiceError as e:
            logger.warning(
                "[trace_id=%s] setup_repo_for_user: template creation failed, falling back to empty repo, "
                "user_id=%s, repo=%s, template=%s/%s, error=%s",
                trace_id, user_id, repo_name, template_owner, template_repo, e.message,
            )

    if repo_info is None:
        repo_info = create_org_repo(
            resource=resource,
            repo_name=repo_name,
            description=repo_desc,
            private=True,
            trace_id=trace_id,
        )

    # 2. 创建仅对该仓库有效的 scoped token
    token = create_repo_scoped_token(resource=resource, repo_name=repo_name, trace_id=trace_id)

    logger.info(
        "[trace_id=%s] setup_repo_for_user: completed, user_id=%s, repo=%s/%s",
        trace_id, user_id, organization, repo_name,
    )

    return {
        'repo_name': repo_info['repo_name'],
        'full_name': repo_info['full_name'],
        'url': repo_info['url'],
        'token': token,
        'default_branch': repo_info['default_branch'],
        'is_docs_repo': is_docs_repo,
    }


def refresh_repo_token_by_url(repo_url: str, trace_id: str = '', force: bool = False) -> str:
    """
    根据仓库 URL 获取 scoped Installation Access Token。

    内部会优先复用剩余有效期 > 10 分钟的缓存 token；
    若需要强制申请新 token（如外部发现旧 token 被吊销），可传 force=True。

    Args:
        repo_url: 仓库 URL（如 https://github.com/org/repo.git）
        trace_id: 链路追踪 ID，用于日志聚合
        force: True 时先清空缓存再重新申请

    Returns:
        scoped token

    Raises:
        GitHubServiceError: 刷新失败
    """
    from dao.resource_dao import get_online_resources_by_type_source

    org, repo_name = parse_github_url(url=repo_url)
    if not org or not repo_name:
        raise GitHubServiceError(f"无法从 URL 解析出组织和仓库名：{repo_url}")

    resources = get_online_resources_by_type_source(type='code_repo', source='github')
    matched_resource = None
    for r in resources:
        extra = r.get_raw_extra()
        if (extra.get('organization') or '').strip() == org:
            matched_resource = r
            break

    if not matched_resource:
        raise GitHubServiceError(
            f"未找到组织 {org} 对应的代码仓库资源，无法刷新 token"
        )

    if force:
        invalidate_token_cache(organization=org, repo_name=repo_name)

    new_token, cache_status = create_repo_scoped_token(
        resource=matched_resource, repo_name=repo_name, trace_id=trace_id,
        return_cache_status=True,
    )

    logger.info(
        "[trace_id=%s] refresh_repo_token_by_url: org=%s, repo=%s, force=%s, status=%s",
        trace_id, org, repo_name, force, cache_status,
    )
    return new_token
