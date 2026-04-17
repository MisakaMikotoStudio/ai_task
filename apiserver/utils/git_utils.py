#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GitHub REST API 底层工具 —— 纯第三方 API 操作，不依赖业务模型

功能：
- GitHub App JWT 生成
- Installation ID / Installation Access Token 获取
- 组织级仓库创建（空仓库 / 模板仓库）
- 仓库信息查询
- URL 解析工具
"""

import logging
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubServiceError(Exception):
    """GitHub 服务操作失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ──────────────────────────────────────────────────────
#  认证相关
# ──────────────────────────────────────────────────────

def generate_app_jwt(app_id: str, private_key_pem: str, trace_id: str = '') -> str:
    """
    使用 GitHub App ID 和私钥生成 JWT（RS256）。

    JWT 有效期 10 分钟（GitHub 要求最长 10 分钟）。

    Args:
        app_id: GitHub App ID
        private_key_pem: GitHub App 私钥（PEM 格式）
        trace_id: 链路追踪 ID，用于日志聚合

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
        'iat': now - 60,
        'exp': now + (10 * 60),
        'iss': str(app_id),
    }

    try:
        token = jwt.encode(payload, private_key_pem, algorithm='RS256')
        return token
    except Exception as e:
        logger.error("[trace_id=%s] generate_app_jwt failed: app_id=%s, error=%s", trace_id, app_id, str(e))
        raise GitHubServiceError(f"GitHub App JWT 生成失败：{str(e)}")


def get_installation_id(jwt_token: str, organization: str, trace_id: str = '') -> int:
    """
    获取 GitHub App 在指定组织中的 Installation ID。

    Args:
        jwt_token: GitHub App JWT
        organization: 组织名称
        trace_id: 链路追踪 ID，用于日志聚合

    Returns:
        Installation ID

    Raises:
        GitHubServiceError: 获取失败或 App 未安装在该组织
    """
    headers = make_headers(token=jwt_token)
    url = f"{GITHUB_API_BASE}/orgs/{organization}/installation"

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        logger.info(
            "[trace_id=%s] get_installation_id: org=%s, status=%d",
            trace_id, organization, resp.status_code,
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


def create_installation_token(
    jwt_token: str,
    installation_id: int,
    repository_ids: Optional[list] = None,
    permissions: Optional[Dict[str, str]] = None,
    trace_id: str = '',
) -> str:
    """
    创建 GitHub App Installation Access Token。

    可选限定到特定仓库和权限范围。

    Args:
        jwt_token: GitHub App JWT
        installation_id: Installation ID
        repository_ids: 限定的仓库 ID 列表（None 表示所有已安装的仓库）
        permissions: 权限范围，如 {"contents": "write", "administration": "write"}
        trace_id: 链路追踪 ID，用于日志聚合

    Returns:
        Installation Access Token

    Raises:
        GitHubServiceError: 创建失败
    """
    headers = make_headers(token=jwt_token)
    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    payload = {}
    if repository_ids:
        payload['repository_ids'] = repository_ids
    if permissions:
        payload['permissions'] = permissions

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        logger.info(
            "[trace_id=%s] create_installation_token: installation_id=%s, status=%d, scoped_repos=%s",
            trace_id, installation_id, resp.status_code,
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


# ──────────────────────────────────────────────────────
#  仓库操作
# ──────────────────────────────────────────────────────

def create_org_repo_api(
    token: str,
    organization: str,
    repo_name: str,
    description: str = '',
    private: bool = True,
    trace_id: str = '',
) -> Dict:
    """
    在 GitHub 组织下创建仓库（纯 API 调用）。

    Args:
        token: Installation Access Token
        organization: GitHub 组织名
        repo_name: 仓库名称
        description: 仓库描述
        private: 是否为私有仓库（默认 True）

    Returns:
        {"repo_name", "full_name", "url", "default_branch", "repo_id"}

    Raises:
        GitHubServiceError: 创建失败
    """
    headers = make_headers(token=token)
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
            "[trace_id=%s] create_org_repo_api: org=%s, repo=%s, status=%d",
            trace_id, organization, repo_name, resp.status_code,
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
            "[trace_id=%s] create_org_repo_api network error: org=%s, repo=%s, error=%s",
            trace_id, organization, repo_name, str(e),
        )
        raise GitHubServiceError(f"创建仓库网络请求失败：{str(e)}")
    except Exception as e:
        logger.error(
            "[trace_id=%s] create_org_repo_api unexpected error: org=%s, repo=%s, error=%s",
            trace_id, organization, repo_name, str(e),
        )
        raise GitHubServiceError(f"创建仓库失败：{str(e)}")


def create_org_repo_from_template_api(
    token: str,
    organization: str,
    repo_name: str,
    template_owner: str,
    template_repo: str,
    description: str = '',
    private: bool = True,
    trace_id: str = '',
) -> Dict:
    """
    使用 GitHub 模板仓库 API 在组织下创建新仓库（纯 API 调用）。

    Args:
        token: Installation Access Token
        organization: 目标组织名
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
    headers = make_headers(token=token)
    url = f"{GITHUB_API_BASE}/repos/{template_owner}/{template_repo}/generate"
    payload = {
        'owner': organization,
        'name': repo_name,
        'description': description,
        'private': private,
        'include_all_branches': False,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        logger.info(
            "[trace_id=%s] create_org_repo_from_template_api: template=%s/%s, target=%s/%s, status=%d",
            trace_id, template_owner, template_repo, organization, repo_name, resp.status_code,
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
                f"从模板创建仓库失败（422）：{error_data.get('message', resp.text)}"
            )

        if resp.status_code == 403:
            raise GitHubServiceError(
                f"从模板创建仓库失败（HTTP 403 权限不足）：{resp.text[:200]}"
            )

        if resp.status_code not in (200, 201):
            raise GitHubServiceError(
                f"从模板创建仓库失败（HTTP {resp.status_code}）：{resp.text[:200]}"
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
            "[trace_id=%s] create_org_repo_from_template_api network error: template=%s/%s, target=%s/%s, error=%s",
            trace_id, template_owner, template_repo, organization, repo_name, str(e),
        )
        raise GitHubServiceError(f"从模板创建仓库网络请求失败：{str(e)}")
    except Exception as e:
        logger.error(
            "[trace_id=%s] create_org_repo_from_template_api unexpected error: template=%s/%s, target=%s/%s, error=%s",
            trace_id, template_owner, template_repo, organization, repo_name, str(e),
        )
        raise GitHubServiceError(f"从模板创建仓库失败：{str(e)}")


def get_repo_id_api(token: str, organization: str, repo_name: str, trace_id: str = '') -> int:
    """
    获取 GitHub 仓库的数字 ID。

    Args:
        token: Installation Access Token
        organization: 组织名
        repo_name: 仓库名

    Returns:
        仓库 ID

    Raises:
        GitHubServiceError: 查询失败
    """
    headers = make_headers(token=token)
    url = f"{GITHUB_API_BASE}/repos/{organization}/{repo_name}"

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        logger.info(
            "[trace_id=%s] get_repo_id_api: org=%s, repo=%s, status=%d",
            trace_id, organization, repo_name, resp.status_code,
        )
        if resp.status_code != 200:
            raise GitHubServiceError(
                f"获取仓库信息失败（HTTP {resp.status_code}）：{resp.text[:200]}"
            )
        return resp.json()['id']
    except GitHubServiceError:
        raise
    except Exception as e:
        raise GitHubServiceError(f"获取仓库信息失败：{str(e)}")


# ──────────────────────────────────────────────────────
#  URL 工具
# ──────────────────────────────────────────────────────

def make_headers(token: str) -> Dict[str, str]:
    """构建 GitHub API 请求头"""
    return {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28',
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


def get_branch_latest_commit(token: str, organization: str, repo_name: str, branch: str, trace_id: str = '') -> str:
    """
    获取 GitHub 仓库指定分支的最新 commit SHA。

    Args:
        token: Installation Access Token
        organization: 组织名
        repo_name: 仓库名
        branch: 分支名

    Returns:
        完整的 commit SHA 字符串

    Raises:
        GitHubServiceError: 查询失败
    """
    headers = make_headers(token=token)
    url = f"{GITHUB_API_BASE}/repos/{organization}/{repo_name}/commits/{branch}"
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        logger.info(
            "[trace_id=%s] get_branch_latest_commit: org=%s, repo=%s, branch=%s, status=%d",
            trace_id, organization, repo_name, branch, resp.status_code,
        )
        if resp.status_code != 200:
            raise GitHubServiceError(f"获取仓库 {repo_name} 分支 {branch} 最新提交失败（HTTP {resp.status_code}）：{resp.text[:200]}")
        return resp.json()['sha']
    except GitHubServiceError:
        raise
    except requests.RequestException as e:
        raise GitHubServiceError(f"获取仓库 {repo_name} 分支 {branch} 最新提交网络请求失败：{str(e)}")
    except Exception as e:
        raise GitHubServiceError(f"获取仓库 {repo_name} 分支 {branch} 最新提交失败：{str(e)}")


def parse_github_url(url: str):
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
