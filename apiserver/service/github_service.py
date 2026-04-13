#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GitHub 代码仓库服务层 —— 通过 GitHub REST API 管理组织下的仓库

功能：
- 使用 GitHub App 凭据（app_id + private_key）生成 JWT 并创建 Installation Access Token
- 在 GitHub 组织下创建仓库
- 为单个仓库创建仅具有 admin 权限的 scoped Installation Access Token
- 组合流程：创建仓库 + 创建访问 token（用于默认应用初始化）

认证方式说明：
- 资源 extra 中存储 GitHub App 凭据：organization、app_id、private_key（App 私钥 PEM）
- 通过 app_id + private_key 生成 JWT，再通过 JWT 获取 Installation Access Token
- Installation Access Token 可以限定只对某几个仓库有效，且权限可控
"""

import logging
import math
import time
from typing import Dict, Optional

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


def _generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    """
    使用 GitHub App ID 和私钥生成 JWT（RS256）。

    JWT 有效期 10 分钟（GitHub 要求最长 10 分钟）。

    Args:
        app_id: GitHub App ID
        private_key_pem: GitHub App 私钥（PEM 格式）

    Returns:
        JWT 字符串

    Raises:
        GitHubServiceError: JWT 生成失败
    """
    try:
        import jwt
    except ImportError:
        raise GitHubServiceError(
            "PyJWT 库未安装，请执行 pip install PyJWT[crypto] 以支持 GitHub App 认证"
        )

    now = int(time.time())
    payload = {
        'iat': now - 60,       # 签发时间（向前偏移 60 秒，容忍时钟偏差）
        'exp': now + (10 * 60),  # 过期时间（10 分钟后）
        'iss': str(app_id),    # GitHub App ID
    }

    try:
        token = jwt.encode(
            payload,
            private_key_pem,
            algorithm='RS256',
        )
        return token
    except Exception as e:
        logger.error("_generate_app_jwt: failed, app_id=%s, error=%s", app_id, str(e))
        raise GitHubServiceError(f"GitHub App JWT 生成失败：{str(e)}")


def _get_installation_id(jwt_token: str, organization: str) -> int:
    """
    获取 GitHub App 在指定组织中的 Installation ID。

    Args:
        jwt_token: GitHub App JWT
        organization: 组织名称

    Returns:
        Installation ID

    Raises:
        GitHubServiceError: 获取失败或 App 未安装在该组织
    """
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {jwt_token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }

    url = f"{GITHUB_API_BASE}/orgs/{organization}/installation"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        logger.info(
            "_get_installation_id: org=%s, status=%d",
            organization, resp.status_code,
        )

        if resp.status_code == 404:
            raise GitHubServiceError(
                f"GitHub App 未安装到组织 {organization}，请先在 GitHub 上安装 App"
            )
        if resp.status_code != 200:
            raise GitHubServiceError(
                f"获取 Installation 失败（HTTP {resp.status_code}）：{resp.text[:200]}"
            )

        data = resp.json()
        installation_id = data.get('id')
        if not installation_id:
            raise GitHubServiceError(
                f"获取 Installation ID 失败：响应中缺少 id 字段"
            )

        return installation_id

    except GitHubServiceError:
        raise
    except requests.RequestException as e:
        raise GitHubServiceError(f"获取 Installation 网络请求失败：{str(e)}")
    except Exception as e:
        raise GitHubServiceError(f"获取 Installation 失败：{str(e)}")


def _create_installation_token(
    jwt_token: str,
    installation_id: int,
    repository_ids: Optional[list] = None,
    permissions: Optional[Dict[str, str]] = None,
) -> str:
    """
    创建 GitHub App Installation Access Token。

    可选限定到特定仓库和权限范围。

    Args:
        jwt_token: GitHub App JWT
        installation_id: Installation ID
        repository_ids: 限定的仓库 ID 列表（None 表示所有已安装的仓库）
        permissions: 权限范围，如 {"contents": "write", "administration": "write"}

    Returns:
        Installation Access Token

    Raises:
        GitHubServiceError: 创建失败
    """
    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {jwt_token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }

    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    payload = {}
    if repository_ids:
        payload['repository_ids'] = repository_ids
    if permissions:
        payload['permissions'] = permissions

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        logger.info(
            "_create_installation_token: installation_id=%s, status=%d, scoped_repos=%s",
            installation_id, resp.status_code,
            len(repository_ids) if repository_ids else 'all',
        )

        if resp.status_code == 422 and permissions:
            raise GitHubServiceError(
                f"创建 Installation Token 失败（HTTP 422）：GitHub App 未授予所需权限。"
                f"请在 GitHub App Settings > Permissions > Repository permissions 中"
                f"启用以下权限并在组织 Installation 中接受更新：{list(permissions.keys())}"
            )

        if resp.status_code not in (200, 201):
            raise GitHubServiceError(
                f"创建 Installation Token 失败（HTTP {resp.status_code}）：{resp.text[:200]}"
            )

        data = resp.json()
        token = data.get('token', '')
        if not token:
            raise GitHubServiceError("创建 Installation Token 失败：响应中缺少 token 字段")

        return token

    except GitHubServiceError:
        raise
    except requests.RequestException as e:
        raise GitHubServiceError(f"创建 Installation Token 网络请求失败：{str(e)}")
    except Exception as e:
        raise GitHubServiceError(f"创建 Installation Token 失败：{str(e)}")


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
    jwt_token = _generate_app_jwt(
        app_id=config['app_id'],
        private_key_pem=config['private_key'],
    )
    installation_id = _get_installation_id(
        jwt_token=jwt_token,
        organization=config['organization'],
    )
    return _create_installation_token(
        jwt_token=jwt_token,
        installation_id=installation_id,
        permissions={
            'administration': 'write',
            'contents': 'write',
            'metadata': 'read',
        },
    )


def _make_headers(token: str) -> Dict[str, str]:
    """构建 GitHub API 请求头"""
    return {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28',
    }


def create_org_repo(resource: Resource, repo_name: str, description: str = '', private: bool = True) -> Dict:
    """
    在 GitHub 组织下创建仓库。

    使用 Installation Token 认证（而非直接使用 admin 凭据）。

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

    # 获取 Installation Token 用于创建仓库
    installation_token = _get_installation_token_for_org(resource=resource)
    headers = _make_headers(token=installation_token)

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

        if resp.status_code == 403:
            raise GitHubServiceError(
                f"创建仓库失败（HTTP 403 权限不足）：{resp.text[:200]}。"
                f"请检查 GitHub App 是否已授予 Repository permissions > Administration: Read and write 权限，"
                f"且 App 已安装到组织 {organization}"
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


def create_repo_scoped_token(resource: Resource, repo_name: str) -> str:
    """
    为指定仓库创建仅具有 admin 权限的 Installation Access Token。

    通过 GitHub App JWT 认证，创建限定到单个仓库的 Installation Access Token，
    该 Token 仅对目标仓库有效，不会暴露组织管理员的凭据。

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
    jwt_token = _generate_app_jwt(
        app_id=config['app_id'],
        private_key_pem=config['private_key'],
    )

    # 2. 获取 Installation ID
    installation_id = _get_installation_id(
        jwt_token=jwt_token,
        organization=organization,
    )

    # 3. 获取仓库 ID（使用 Installation Token 查询）
    org_token = _create_installation_token(
        jwt_token=jwt_token,
        installation_id=installation_id,
    )
    headers = _make_headers(token=org_token)
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

    # 4. 创建仅对该仓库有效的 scoped Installation Token
    scoped_token = _create_installation_token(
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
) -> Dict:
    """
    为用户创建仓库并生成 scoped access token 的组合流程。

    流程：
    1. 在组织下创建仓库（使用 Installation Token）
    2. 为该仓库创建 scoped Installation Access Token（仅对该仓库有效）
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
            "token": "ghs_xxxxx",
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


def _parse_github_url(url: str):
    """
    从 GitHub 仓库 URL 中解析出组织名和仓库名。

    支持格式：
    - https://github.com/org/repo.git
    - https://github.com/org/repo

    Args:
        url: 仓库 URL

    Returns:
        (organization, repo_name) 元组，解析失败返回 (None, None)
    """
    if not url:
        return None, None

    url = url.strip().rstrip('/')
    if url.endswith('.git'):
        url = url[:-4]

    parts = url.split('github.com/')
    if len(parts) != 2:
        return None, None

    path = parts[1].strip('/')
    segments = path.split('/')
    if len(segments) != 2:
        return None, None

    return segments[0], segments[1]


def refresh_repo_token_by_url(repo_url: str) -> str:
    """
    根据仓库 URL 重新生成 scoped Installation Access Token。

    从仓库 URL 解析出组织名和仓库名，找到匹配的 code_repo Resource，
    然后生成新的仅对该仓库有效的 Installation Access Token。

    Args:
        repo_url: 仓库 URL（如 https://github.com/org/repo.git）

    Returns:
        新的 scoped token

    Raises:
        GitHubServiceError: 刷新失败
    """
    from dao.resource_dao import get_online_resources_by_type_source

    org, repo_name = _parse_github_url(url=repo_url)
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
