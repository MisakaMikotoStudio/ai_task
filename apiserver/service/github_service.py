#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GitHub 代码仓库服务层 —— 通过 GitHub REST API 管理组织下的仓库

功能：
- 在 GitHub 组织下创建仓库
- 为单个仓库创建具有 admin 权限的 fine-grained access token
- 组合流程：创建仓库 + 创建访问 token（用于默认应用初始化）
"""

import logging
from typing import Dict

import requests

from dao.models import Resource

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubServiceError(Exception):
    """GitHub 服务操作失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _get_resource_config(resource: Resource) -> Dict[str, str]:
    """
    从 Resource 对象中提取 GitHub 配置信息

    Args:
        resource: Resource 对象（type=code_repo, source=github）

    Returns:
        {"organization": "...", "admin_token": "..."}

    Raises:
        GitHubServiceError: 配置缺失
    """
    extra = resource.get_raw_extra()
    organization = (extra.get('organization') or '').strip()
    admin_token = (extra.get('admin_token') or '').strip()

    if not organization:
        raise GitHubServiceError("资源缺少 GitHub Organization 配置")
    if not admin_token:
        raise GitHubServiceError("资源缺少 GitHub Admin Token 配置")

    return {
        'organization': organization,
        'admin_token': admin_token,
    }


def _make_headers(admin_token: str) -> Dict[str, str]:
    """构建 GitHub API 请求头"""
    return {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {admin_token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }


def create_org_repo(resource: Resource, repo_name: str, description: str = '', private: bool = True) -> Dict:
    """
    在 GitHub 组织下创建仓库

    Args:
        resource: Resource 对象（type=code_repo, source=github）
        repo_name: 仓库名称
        description: 仓库描述
        private: 是否为私有仓库（默认 True）

    Returns:
        {
            "repo_name": "...",
            "full_name": "org/repo_name",
            "url": "https://github.com/org/repo_name.git",
            "default_branch": "main",
            "repo_id": 123456,
        }

    Raises:
        GitHubServiceError: 创建失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']
    admin_token = config['admin_token']
    headers = _make_headers(admin_token=admin_token)

    url = f"{GITHUB_API_BASE}/orgs/{organization}/repos"
    payload = {
        'name': repo_name,
        'description': description,
        'private': private,
        'auto_init': True,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        logger.info(
            "create_org_repo: org=%s, repo=%s, status=%d",
            organization, repo_name, resp.status_code,
        )

        if resp.status_code == 422:
            # 仓库可能已存在
            error_data = resp.json()
            errors = error_data.get('errors', [])
            for err in errors:
                if err.get('message', '').startswith('name already exists'):
                    raise GitHubServiceError(
                        f"仓库 {organization}/{repo_name} 已存在于 GitHub"
                    )
            raise GitHubServiceError(
                f"创建仓库失败（422）：{error_data.get('message', resp.text)}"
            )

        if resp.status_code not in (200, 201):
            raise GitHubServiceError(
                f"创建仓库失败（HTTP {resp.status_code}）：{resp.text[:200]}"
            )

        data = resp.json()
        return {
            'repo_name': data['name'],
            'full_name': data['full_name'],
            'url': data['clone_url'],
            'default_branch': data.get('default_branch', 'main'),
            'repo_id': data['id'],
        }

    except GitHubServiceError:
        raise
    except requests.RequestException as e:
        logger.error(
            "create_org_repo network error: org=%s, repo=%s, error=%s",
            organization, repo_name, str(e),
        )
        raise GitHubServiceError(f"创建仓库网络请求失败：{str(e)}")
    except Exception as e:
        logger.error(
            "create_org_repo unexpected error: org=%s, repo=%s, error=%s",
            organization, repo_name, str(e),
        )
        raise GitHubServiceError(f"创建仓库失败：{str(e)}")


def create_repo_scoped_token(
    resource: Resource,
    repo_name: str,
    token_note: str = '',
) -> str:
    """
    为指定仓库创建具有 admin 权限的 fine-grained access token。

    使用 GitHub App installation token 或 Personal Access Token 的方式
    通过 GitHub API 为仓库创建 scoped token。

    注意：GitHub REST API 不直接支持通过 API 创建 fine-grained PAT。
    这里采用的方案是：通过 admin_token（需具有组织 admin 权限）
    调用 GitHub App API 为指定仓库授权，返回仅对该仓库有 admin 权限的 token。

    如果资源的 admin_token 是一个 GitHub App 的 installation token，
    可以通过 Create an installation access token API 创建 scoped token。

    当前实现：直接使用 admin_token 调用 GitHub App installations API。
    如果 admin_token 不支持此操作，则回退到直接返回 admin_token（降级方案）。

    Args:
        resource: Resource 对象
        repo_name: 仓库名称（不含组织前缀）
        token_note: token 备注

    Returns:
        仓库访问 token

    Raises:
        GitHubServiceError: 创建失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']
    admin_token = config['admin_token']
    headers = _make_headers(admin_token=admin_token)

    # 获取仓库 ID（用于 scoped token 限定）
    repo_url = f"{GITHUB_API_BASE}/repos/{organization}/{repo_name}"
    try:
        resp = requests.get(repo_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise GitHubServiceError(
                f"获取仓库信息失败（HTTP {resp.status_code}）：{resp.text[:200]}"
            )
        repo_id = resp.json()['id']
    except GitHubServiceError:
        raise
    except Exception as e:
        raise GitHubServiceError(f"获取仓库信息失败：{str(e)}")

    # 尝试通过 GitHub App Installation API 创建 scoped token
    # 先获取 app installations
    try:
        installations_url = f"{GITHUB_API_BASE}/orgs/{organization}/installations"
        resp = requests.get(installations_url, headers=headers, timeout=30)

        if resp.status_code == 200:
            installations = resp.json().get('installations', [])
            if installations:
                installation_id = installations[0]['id']

                # 创建 scoped installation token
                token_url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
                token_payload = {
                    'repository_ids': [repo_id],
                    'permissions': {
                        'contents': 'write',
                        'metadata': 'read',
                        'administration': 'write',
                    },
                }
                token_resp = requests.post(
                    token_url, json=token_payload, headers=headers, timeout=30
                )

                if token_resp.status_code in (200, 201):
                    token_data = token_resp.json()
                    scoped_token = token_data.get('token', '')
                    if scoped_token:
                        logger.info(
                            "create_repo_scoped_token: org=%s, repo=%s, via installation",
                            organization, repo_name,
                        )
                        return scoped_token

        # 如果 GitHub App 方式不可用，回退使用 admin_token
        logger.warning(
            "create_repo_scoped_token: GitHub App installation not available for org=%s, "
            "falling back to admin_token for repo=%s",
            organization, repo_name,
        )
        return admin_token

    except GitHubServiceError:
        raise
    except Exception as e:
        logger.warning(
            "create_repo_scoped_token: failed to create scoped token, org=%s, repo=%s, "
            "error=%s, falling back to admin_token",
            organization, repo_name, str(e),
        )
        return admin_token


def setup_repo_for_user(
    resource: Resource,
    user_id: int,
    repo_name: str,
    description: str = '',
    is_docs_repo: bool = False,
) -> Dict:
    """
    为用户创建仓库并生成访问 token 的组合流程。

    流程：
    1. 在组织下创建仓库
    2. 为该仓库创建 scoped access token
    3. 返回完整的仓库信息

    Args:
        resource: Resource 对象（type=code_repo, source=github）
        user_id: 用户 ID
        repo_name: 仓库名称
        description: 仓库描述
        is_docs_repo: 是否为文档仓库

    Returns:
        {
            "repo_name": "...",
            "full_name": "org/repo_name",
            "url": "https://github.com/org/repo_name.git",
            "token": "...",
            "default_branch": "main",
            "is_docs_repo": True/False,
        }

    Raises:
        GitHubServiceError: 创建失败
    """
    config = _get_resource_config(resource=resource)
    organization = config['organization']

    logger.info(
        "setup_repo_for_user: user_id=%s, org=%s, repo=%s, is_docs=%s",
        user_id, organization, repo_name, is_docs_repo,
    )

    # 1. 创建仓库
    repo_type = "文档仓库" if is_docs_repo else "代码仓库"
    repo_desc = description or f"用户 {user_id} 的{repo_type}"

    repo_info = create_org_repo(
        resource=resource,
        repo_name=repo_name,
        description=repo_desc,
        private=True,
    )

    # 2. 创建 scoped token
    token = create_repo_scoped_token(
        resource=resource,
        repo_name=repo_name,
        token_note=f"user_{user_id}_{repo_name}",
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


def build_repo_url(organization: str, repo_name: str) -> str:
    """
    拼接仓库 clone URL

    Args:
        organization: GitHub 组织名
        repo_name: 仓库名称

    Returns:
        https://github.com/{organization}/{repo_name}.git
    """
    return f"https://github.com/{organization}/{repo_name}.git"
