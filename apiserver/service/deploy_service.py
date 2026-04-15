#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发布相关业务逻辑 —— 合并分支到默认分支
"""

import logging
import re
from typing import List, Tuple
from urllib.parse import urlparse

import requests

from dao.client_dao import get_client_repos, get_client_by_id

logger = logging.getLogger(__name__)


def _parse_github_owner_repo(url: str) -> Tuple[str, str]:
    """
    从仓库 URL 解析 GitHub owner/repo。
    支持 https://github.com/owner/repo.git 和 git@github.com:owner/repo.git
    返回 (owner, repo)，解析失败返回 ('', '')。
    """
    url = (url or '').strip()
    # SSH 格式: git@github.com:owner/repo.git
    ssh_match = re.match(r'git@github\.com:(.+?)/(.+?)(?:\.git)?$', url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)
    # HTTPS 格式
    parsed = urlparse(url)
    if 'github.com' not in (parsed.hostname or ''):
        return '', ''
    parts = parsed.path.strip('/').replace('.git', '').split('/')
    if len(parts) >= 2:
        return parts[0], parts[1]
    return '', ''


def _parse_gitlab_project_path(url: str) -> Tuple[str, str]:
    """
    从仓库 URL 解析 GitLab host 和 project path。
    返回 (host, project_path_encoded)，解析失败返回 ('', '')。
    """
    url = (url or '').strip()
    # SSH: git@gitlab.example.com:group/repo.git
    ssh_match = re.match(r'git@(.+?):(.+?)(?:\.git)?$', url)
    if ssh_match:
        host = ssh_match.group(1)
        if 'github.com' in host:
            return '', ''
        project_path = ssh_match.group(2)
        return host, requests.utils.quote(project_path, safe='')
    # HTTPS
    parsed = urlparse(url)
    hostname = parsed.hostname or ''
    if 'github.com' in hostname or not hostname:
        return '', ''
    path = parsed.path.strip('/').replace('.git', '')
    if not path:
        return '', ''
    return hostname, requests.utils.quote(path, safe='')


def merge_branch_to_default(user_id: int, client_id: int, task_id: int, chat_id: int) -> List[dict]:
    """
    将 chat 分支合并到各仓库的默认分支。

    Returns:
        合并结果列表 [{"repo_name": ..., "success": bool, "message": ...}, ...]
    """
    client = get_client_by_id(client_id=client_id, user_id=user_id)
    if not client:
        return [{'repo_name': '-', 'success': False, 'message': '应用不存在'}]

    repos = get_client_repos(client_id=client_id, user_id=user_id)
    if not repos:
        return [{'repo_name': '-', 'success': False, 'message': '未配置代码仓库'}]

    results = []
    for repo in repos:
        if repo.docs_repo:
            continue

        repo_url = repo.url or ''
        token = repo.token or ''
        default_branch = repo.default_branch or 'main'
        branch_prefix = repo.branch_prefix or 'ai_'
        source_branch = f'{branch_prefix}{task_id}_{chat_id}'
        repo_name = repo.desc or repo_url.split('/')[-1].replace('.git', '')

        if not token:
            results.append({'repo_name': repo_name, 'success': False, 'message': '仓库未配置 token'})
            continue

        # 尝试 GitHub
        owner, repo_slug = _parse_github_owner_repo(repo_url)
        if owner and repo_slug:
            result = _merge_github(owner=owner, repo=repo_slug, token=token, source_branch=source_branch, target_branch=default_branch, repo_name=repo_name)
            results.append(result)
            continue

        # 尝试 GitLab
        host, project_path = _parse_gitlab_project_path(repo_url)
        if host and project_path:
            result = _merge_gitlab(host=host, project_path=project_path, token=token, source_branch=source_branch, target_branch=default_branch, repo_name=repo_name)
            results.append(result)
            continue

        results.append({'repo_name': repo_name, 'success': False, 'message': '不支持的仓库类型，仅支持 GitHub/GitLab'})

    return results


def _merge_github(owner: str, repo: str, token: str, source_branch: str, target_branch: str, repo_name: str) -> dict:
    """通过 GitHub API 将 source_branch 合并到 target_branch"""
    api_url = f'https://api.github.com/repos/{owner}/{repo}/merges'
    headers = {'Authorization': f'token {token}', 'Accept': 'application/vnd.github.v3+json'}
    payload = {'base': target_branch, 'head': source_branch, 'commit_message': f'Merge {source_branch} into {target_branch}'}

    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 201:
            return {'repo_name': repo_name, 'success': True, 'message': '合并成功'}
        if resp.status_code == 204:
            return {'repo_name': repo_name, 'success': True, 'message': '无需合并，分支已是最新'}
        if resp.status_code == 409:
            return {'repo_name': repo_name, 'success': False, 'message': '合并冲突，请手动解决'}
        if resp.status_code == 404:
            return {'repo_name': repo_name, 'success': False, 'message': f'仓库或分支不存在: {source_branch}'}
        body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
        msg = body.get('message', resp.text[:200])
        logger.warning(f'GitHub merge failed: {resp.status_code} {msg}')
        return {'repo_name': repo_name, 'success': False, 'message': f'GitHub API 错误({resp.status_code}): {msg}'}
    except requests.RequestException as e:
        logger.error(f'GitHub merge request error: {e}')
        return {'repo_name': repo_name, 'success': False, 'message': f'网络错误: {str(e)}'}


def _merge_gitlab(host: str, project_path: str, token: str, source_branch: str, target_branch: str, repo_name: str) -> dict:
    """通过 GitLab API 创建并合并 MR"""
    base_url = f'https://{host}/api/v4'
    headers = {'PRIVATE-TOKEN': token}

    try:
        # 创建 MR
        mr_url = f'{base_url}/projects/{project_path}/merge_requests'
        mr_payload = {'source_branch': source_branch, 'target_branch': target_branch, 'title': f'Merge {source_branch} into {target_branch}'}
        mr_resp = requests.post(mr_url, json=mr_payload, headers=headers, timeout=30)

        if mr_resp.status_code not in (200, 201):
            body = mr_resp.json() if mr_resp.headers.get('content-type', '').startswith('application/json') else {}
            msg = body.get('message', mr_resp.text[:200])
            # 如果已存在 MR，尝试获取现有的
            if 'already exists' in str(msg).lower():
                pass  # 继续尝试合并
            else:
                return {'repo_name': repo_name, 'success': False, 'message': f'GitLab 创建MR失败: {msg}'}

        mr_data = mr_resp.json() if mr_resp.status_code in (200, 201) else None
        if mr_data:
            iid = mr_data.get('iid')
            # 接受 MR（合并）
            accept_url = f'{base_url}/projects/{project_path}/merge_requests/{iid}/merge'
            accept_resp = requests.put(accept_url, headers=headers, timeout=30)
            if accept_resp.status_code == 200:
                return {'repo_name': repo_name, 'success': True, 'message': '合并成功'}
            body = accept_resp.json() if accept_resp.headers.get('content-type', '').startswith('application/json') else {}
            msg = body.get('message', accept_resp.text[:200])
            return {'repo_name': repo_name, 'success': False, 'message': f'GitLab 合并失败: {msg}'}

        return {'repo_name': repo_name, 'success': False, 'message': 'GitLab 创建MR失败'}
    except requests.RequestException as e:
        logger.error(f'GitLab merge request error: {e}')
        return {'repo_name': repo_name, 'success': False, 'message': f'网络错误: {str(e)}'}
