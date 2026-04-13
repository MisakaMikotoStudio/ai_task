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
from typing import Dict

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

# 重导出，方便调用方从 service 层导入
__all__ = [
    'GitHubServiceError',
    'build_repo_url',
    'create_org_repo',
    'create_org_repo_from_template',
    'create_repo_scoped_token',
    'setup_repo_for_user',
    'refresh_repo_token_by_url',
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


def _get_installation_token_for_org(resource: Resource) -> str:
    """
    获取组织级别的 Installation Access Token（用于创建仓库等操作）。

    Args:
        resource: Resource 对象

    Returns:
        Installation Access Token

    Raises:
        GitHubServiceError: 获取失败
    """
    config = _get_resource_config(resource=resource)
    jwt_token = generate_app_jwt(
        app_id=config['app_id'],
        private_key_pem=config['private_key'],
    )
    installation_id = get_installation_id(
        jwt_token=jwt_token,
        organization=config['organization'],
    )
    return create_installation_token(
        jwt_token=jwt_token,
        installation_id=installation_id,
        permissions={
            'administration': 'write',
            'contents': 'write',
            'metadata': 'read',
        },
    )


def create_org_repo(resource: Resource, repo_name: str, description: str = '', private: bool = True) -> Dict:
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
    installation_token = _get_installation_token_for_org(resource=resource)

    return create_org_repo_api(
        token=installation_token,
        organization=organization,
        repo_name=repo_name,
        description=description,
        private=private,
    )


def create_org_repo_from_template(
    resource: Resource,
    repo_name: str,
    template_owner: str,
    template_repo: str,
    description: str = '',
    private: bool = True,
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
    installation_token = _get_installation_token_for_org(resource=resource)

    return create_org_repo_from_template_api(
        token=installation_token,
        organization=organization,
        repo_name=repo_name,
        template_owner=template_owner,
        template_repo=template_repo,
        description=description,
        private=private,
    )


def create_repo_scoped_token(resource: Resource, repo_name: str) -> str:
    """
    为指定仓库创建仅具有 admin 权限的 Installation Access Token。

    Args:
        resource: Resource 对象
        repo_name: 仓库名称（不含组织前缀）

    Returns:
        仓库 scoped Installation Access Token

    Raises:
        GitHubServiceError: 创建失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']

    # 1. 生成 App JWT
    jwt_token = generate_app_jwt(
        app_id=config['app_id'],
        private_key_pem=config['private_key'],
    )

    # 2. 获取 Installation ID
    installation_id = get_installation_id(
        jwt_token=jwt_token,
        organization=organization,
    )

    # 3. 获取仓库 ID
    org_token = create_installation_token(
        jwt_token=jwt_token,
        installation_id=installation_id,
    )
    repo_id = get_repo_id_api(
        token=org_token,
        organization=organization,
        repo_name=repo_name,
    )

    # 4. 创建仅对该仓库有效的 scoped Installation Token
    scoped_token = create_installation_token(
        jwt_token=jwt_token,
        installation_id=installation_id,
        repository_ids=[repo_id],
        permissions={
            'contents': 'write',
            'metadata': 'read',
            'administration': 'write',
        },
    )

    logger.info(
        "create_repo_scoped_token: org=%s, repo=%s, repo_id=%s, token created",
        organization, repo_name, repo_id,
    )
    return scoped_token


def setup_repo_for_user(
    resource: Resource,
    user_id: int,
    repo_name: str,
    description: str = '',
    is_docs_repo: bool = False,
    template_owner: str = '',
    template_repo: str = '',
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
        "setup_repo_for_user: user_id=%s, org=%s, repo=%s, is_docs=%s, template=%s/%s",
        user_id, organization, repo_name, is_docs_repo,
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
            )
            logger.info(
                "setup_repo_for_user: created from template %s/%s, user_id=%s, repo=%s",
                template_owner, template_repo, user_id, repo_name,
            )
        except GitHubServiceError as e:
            logger.warning(
                "setup_repo_for_user: template creation failed, falling back to empty repo, "
                "user_id=%s, repo=%s, template=%s/%s, error=%s",
                user_id, repo_name, template_owner, template_repo, e.message,
            )

    if repo_info is None:
        repo_info = create_org_repo(
            resource=resource,
            repo_name=repo_name,
            description=repo_desc,
            private=True,
        )

    # 2. 创建仅对该仓库有效的 scoped token
    token = create_repo_scoped_token(
        resource=resource,
        repo_name=repo_name,
    )

    logger.info(
        "setup_repo_for_user: completed, user_id=%s, repo=%s/%s",
        user_id, organization, repo_name,
    )

    return {
        'repo_name': repo_info['repo_name'],
        'full_name': repo_info['full_name'],
        'url': repo_info['url'],
        'token': token,
        'default_branch': repo_info['default_branch'],
        'is_docs_repo': is_docs_repo,
    }


def refresh_repo_token_by_url(repo_url: str) -> str:
    """
    根据仓库 URL 重新生成 scoped Installation Access Token。

    Args:
        repo_url: 仓库 URL（如 https://github.com/org/repo.git）

    Returns:
        新的 scoped token

    Raises:
        GitHubServiceError: 刷新失败
    """
    from dao.resource_dao import get_online_resources_by_type_source

    org, repo_name = parse_github_url(url=repo_url)
    if not org or not repo_name:
        raise GitHubServiceError(f"无法从 URL 解析出组织和仓库名：{repo_url}")

    resources = get_online_resources_by_type_source(
        type='code_repo',
        source='github',
    )
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

    new_token = create_repo_scoped_token(
        resource=matched_resource,
        repo_name=repo_name,
    )

    logger.info(
        "refresh_repo_token_by_url: org=%s, repo=%s, token refreshed",
        org, repo_name,
    )
    return new_token
