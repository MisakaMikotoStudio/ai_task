#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GitHub 仓库初始化服务 — 在指定 Organization 下创建文档仓库和业务代码仓库
"""

import logging
import re
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class GitHubServiceError(Exception):
    """GitHub 操作失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _headers(token: str) -> dict:
    return {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }


def _get_org_repo(
    org: str, repo_name: str, *, token: str, api_base: str
) -> Optional[dict]:
    """查询组织下某个仓库，存在返回仓库信息 dict，不存在返回 None"""
    url = f'{api_base}/repos/{org}/{repo_name}'
    resp = requests.get(url, headers=_headers(token), timeout=15)
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 404:
        return None
    body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
    logger.error(
        "GitHub API error checking repo %s/%s: %s %s",
        org, repo_name, resp.status_code, body.get('message', resp.text[:200]),
    )
    raise GitHubServiceError(
        f'查询仓库 {org}/{repo_name} 失败: HTTP {resp.status_code}'
    )


def _create_org_repo(
    org: str,
    repo_name: str,
    description: str,
    *,
    token: str,
    api_base: str,
    private: bool = True,
) -> dict:
    """在组织下创建仓库（auto_init=True 会自动创建 README）"""
    url = f'{api_base}/orgs/{org}/repos'
    payload = {
        'name': repo_name,
        'description': description,
        'private': private,
        'auto_init': True,
    }
    resp = requests.post(url, json=payload, headers=_headers(token), timeout=15)
    if resp.status_code in (200, 201):
        return resp.json()
    body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
    msg = body.get('message', resp.text[:200])
    logger.error(
        "GitHub API error creating repo %s/%s: %s %s",
        org, repo_name, resp.status_code, msg,
    )
    raise GitHubServiceError(
        f'创建仓库 {org}/{repo_name} 失败: {msg}'
    )


def _find_max_app_version(
    org: str, prefix: str, *, token: str, api_base: str
) -> int:
    """
    分页遍历组织仓库，找出匹配 prefix 的最大版本号。
    prefix 示例: "42_app_"
    """
    max_version = 0
    page = 1
    while True:
        url = f'{api_base}/orgs/{org}/repos'
        params = {'per_page': 100, 'page': page, 'type': 'all'}
        resp = requests.get(
            url, params=params, headers=_headers(token), timeout=15
        )
        if resp.status_code != 200:
            body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
            logger.error(
                "GitHub API error listing repos for org %s: %s %s",
                org, resp.status_code, body.get('message', resp.text[:200]),
            )
            raise GitHubServiceError(f'列举组织仓库失败: HTTP {resp.status_code}')
        data = resp.json()
        if not data:
            break
        for repo in data:
            name = repo.get('name', '')
            if name.startswith(prefix):
                suffix = name[len(prefix):]
                if re.fullmatch(r'\d+', suffix):
                    max_version = max(max_version, int(suffix))
        if len(data) < 100:
            break
        page += 1
    return max_version


def _repo_to_info(repo_data: dict, *, token: str, is_docs: bool) -> dict:
    """将 GitHub API 返回的仓库对象转为前端需要的仓库信息"""
    return {
        'url': repo_data.get('clone_url', ''),
        'desc': '文档仓库' if is_docs else '业务代码仓库',
        'default_branch': repo_data.get('default_branch', 'main'),
        'token': token,
        'branch_prefix': 'ai_',
        'docs_repo': is_docs,
    }


def init_repos_from_template(
    user_id: int,
    *,
    organization: str,
    admin_token: str,
    api_base: str = 'https://api.github.com',
) -> dict:
    """
    为指定用户在 GitHub Organization 下初始化仓库：

    1. {user_id}_docs  — 文档仓库（若已存在则直接复用）
    2. {user_id}_app_{version} — 业务代码仓库（若已有版本则 version+1）

    Returns:
        {
            "repos": [docs_repo_info, app_repo_info]
        }
    """
    docs_repo_name = f'{user_id}_docs'
    app_prefix = f'{user_id}_app_'

    # ---- docs 仓库 ----
    logger.info("Checking docs repo: %s/%s", organization, docs_repo_name)
    docs_repo = _get_org_repo(
        organization, docs_repo_name,
        token=admin_token, api_base=api_base,
    )
    if docs_repo is None:
        logger.info("Creating docs repo: %s/%s", organization, docs_repo_name)
        docs_repo = _create_org_repo(
            organization, docs_repo_name,
            description=f'Documentation repo for user {user_id}',
            token=admin_token, api_base=api_base, private=True,
        )
    else:
        logger.info("Docs repo already exists: %s/%s", organization, docs_repo_name)

    # ---- app 仓库 ----
    max_version = _find_max_app_version(
        organization, app_prefix,
        token=admin_token, api_base=api_base,
    )
    new_version = max_version + 1
    app_repo_name = f'{app_prefix}{new_version}'

    logger.info(
        "Creating app repo: %s/%s (version %d)",
        organization, app_repo_name, new_version,
    )
    app_repo = _create_org_repo(
        organization, app_repo_name,
        description=f'Application code repo for user {user_id}, version {new_version}',
        token=admin_token, api_base=api_base, private=True,
    )

    return {
        'repos': [
            _repo_to_info(docs_repo, token=admin_token, is_docs=True),
            _repo_to_info(app_repo, token=admin_token, is_docs=False),
        ]
    }
