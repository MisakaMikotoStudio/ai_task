#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Git 工作流操作
提供分支同步、rebase、PR 创建、差异比较、批量提交推送等工作流功能
"""

import logging
import os
import subprocess
from typing import Optional

try:
    import requests as _requests
except ImportError:
    _requests = None

from utils.git_utils import (
    GitResult, _run_git_command, _validate_repo_dir, _fetch_all,
    _check_remote_branch_exists, _check_local_branch_exists,
    get_web_url, run_git_command_or_raise, _build_merge_request_url,
    git_error_is_auth_failure,
)

logger = logging.getLogger(__name__)


def sync_and_rebase_branch(
    repo_dir: str,
    dev_branch: str,
    default_branch: str,
    timeout_cmd: int = 60,
    trace_id: Optional[str] = None,
) -> GitResult:
    """
    同步开发分支并从主分支进行 rebase。

    流程:
    1. fetch 远端
    2. 准备开发分支（远端存在则对齐，否则从主分支创建）
    3. rebase origin/<default_branch>
    4. force-with-lease push
    """
    try:
        if not dev_branch or not default_branch:
            return GitResult(success=False, message="dev_branch 和 default_branch 不能为空")
        if dev_branch == default_branch:
            return GitResult(success=False, message="开发分支不能与主分支同名")

        err = _validate_repo_dir(repo_dir)
        if err:
            return err

        # 1. fetch
        fetch_result = _fetch_all(repo_dir, timeout_cmd, trace_id=trace_id)
        if not fetch_result.success:
            return GitResult(success=False, message=f"fetch 远端失败: {fetch_result.message}")
        logger.info(f"[trace_id={trace_id}] [{repo_dir}] 已 fetch 远端最新信息")

        if not _check_remote_branch_exists(repo_dir, default_branch, timeout_cmd, trace_id=trace_id):
            return GitResult(success=False, message=f"远端主分支不存在: origin/{default_branch}")

        # 2. 准备开发分支
        if _check_remote_branch_exists(repo_dir, dev_branch, timeout_cmd, trace_id=trace_id):
            sync = _run_git_command(
                ['git', 'checkout', '-B', dev_branch, f'origin/{dev_branch}'],
                cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
            )
            if not sync.success:
                return GitResult(success=False, message=f"同步开发分支失败: {sync.message}")
            logger.info(f"[trace_id={trace_id}] [{repo_dir}] 已同步到云端分支: origin/{dev_branch}")
        else:
            align_default = _run_git_command(
                ['git', 'checkout', '-B', default_branch, f'origin/{default_branch}'],
                cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
            )
            if not align_default.success:
                return GitResult(
                    success=False,
                    message=f"切换并同步主分支 {default_branch} 失败: {align_default.message}",
                )
            create = _run_git_command(
                ['git', 'checkout', '-B', dev_branch],
                cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
            )
            if not create.success:
                return GitResult(success=False, message=f"创建分支 {dev_branch} 失败: {create.message}")
            logger.info(f"[trace_id={trace_id}] [{repo_dir}] 已从主分支 {default_branch} 创建开发分支: {dev_branch}")

        # 3. rebase
        rebase_result = _run_git_command(
            ['git', 'rebase', f'origin/{default_branch}'],
            cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
        )
        if not rebase_result.success:
            _run_git_command(['git', 'rebase', '--abort'], cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id)
            return GitResult(success=False, message=f"rebase 失败: {rebase_result.message}")
        logger.info(f"[trace_id={trace_id}] [{repo_dir}] rebase 成功: origin/{default_branch}")

        # 4. push
        push_result = _run_git_command(
            ['git', 'push', '--force-with-lease', 'origin', dev_branch],
            cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
        )
        if not push_result.success:
            return GitResult(success=False, message=f"push 失败: {push_result.message}")
        logger.info(f"[trace_id={trace_id}] [{repo_dir}] 已强制推送到云端: origin/{dev_branch}")

        return GitResult(success=True, message=f"分支 {dev_branch} 已成功 rebase 并推送到云端")

    except Exception as e:
        logger.error(f"[trace_id={trace_id}] [{repo_dir}] sync_and_rebase_branch 异常: {e}", exc_info=True)
        return GitResult(success=False, message=f"操作异常: {str(e)}")


def detect_default_branch_from_url(
    auth_url: str,
    repo_name: str,
    timeout: int = 30,
    trace_id: Optional[str] = None,
) -> Optional[str]:
    """
    通过远端 URL 检测 Git 仓库的默认分支（无需本地仓库）。
    使用 git ls-remote --symref 获取 HEAD 指向的分支。
    """
    try:
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        result = subprocess.run(
            ['git', 'ls-remote', '--symref', auth_url, 'HEAD'],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if result.returncode != 0:
            logger.error(f"[trace_id={trace_id}] [{repo_name}] 检测默认分支失败: {result.stderr.strip()}")
            return None

        for line in result.stdout.strip().split('\n'):
            if line.startswith('ref:'):
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith('refs/heads/'):
                    return parts[1].replace('refs/heads/', '')

        logger.error(f"[trace_id={trace_id}] [{repo_name}] 无法解析默认分支: {result.stdout}")
        return None
    except subprocess.TimeoutExpired:
        logger.error(f"[trace_id={trace_id}] [{repo_name}] 检测默认分支超时: {repo_name}")
        return None
    except Exception as e:
        logger.error(f"[trace_id={trace_id}] [{repo_name}] 检测默认分支异常: {e}")
        return None


def collect_remote_branch_diff_info(
    repo_dir: str,
    dev_branch: str,
    main_branch: str,
    timeout_cmd: int = 60,
    trace_id: Optional[str] = None,
) -> GitResult:
    """
    比较云端 dev_branch 与 main_branch 的实际文件内容差异。

    使用 git diff --stat 检查两个分支间是否存在真实的文件变更，
    而非仅依赖 rev-list 提交计数。当存在差异时，返回 commit_id 和 merge_url。
    """
    try:
        if not dev_branch or not main_branch:
            return GitResult(success=False, message="dev_branch 和 main_branch 不能为空")

        err = _validate_repo_dir(repo_dir)
        if err:
            return err

        fetch_result = _fetch_all(repo_dir, timeout_cmd, trace_id=trace_id)
        if not fetch_result.success:
            return GitResult(success=False, message=f"fetch 远端失败: {fetch_result.message}")

        repo_name = os.path.basename(os.path.normpath(repo_dir))
        no_diff = GitResult(success=True, message="no_diff", repo_name=repo_name, branch_name=dev_branch, merge_url='')

        if not _check_remote_branch_exists(repo_dir, dev_branch, timeout_cmd, trace_id=trace_id):
            return no_diff

        diff_stat = _run_git_command(
            ["git", "diff", "--stat", f"origin/{main_branch}", f"origin/{dev_branch}"],
            cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
        )
        if not diff_stat.success:
            return GitResult(success=False, message=f"diff 检查失败: {diff_stat.message}")

        if not diff_stat.message.strip():
            logger.info(f"[trace_id={trace_id}] [{repo_dir}] {dev_branch} 与 {main_branch} 无实际文件差异")
            return no_diff

        sha = _run_git_command(
            ["git", "rev-parse", f"origin/{dev_branch}"],
            cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
        )
        if not sha.success:
            return GitResult(success=False, message=f"获取 commitId 失败: {sha.message}")

        merge_url = _build_merge_request_url(repo_dir, dev_branch, timeout_cmd)
        return GitResult(
            success=True, message="has_diff",
            repo_name=repo_name, branch_name=dev_branch,
            commit_id=sha.message.strip(), merge_url=merge_url,
        )

    except Exception as e:
        logger.error(f"[trace_id={trace_id}] [{repo_dir}] collect_remote_branch_diff_info 异常: {e}", exc_info=True)
        return GitResult(success=False, message=f"操作异常: {str(e)}")


def get_local_head_commit_id(repo_dir: str, timeout_cmd: int = 30, trace_id: Optional[str] = None) -> GitResult:
    """获取本地仓库当前 HEAD 的 commit ID。"""
    err = _validate_repo_dir(repo_dir)
    if err:
        return err
    result = _run_git_command(["git", "rev-parse", "HEAD"], cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id)
    if not result.success:
        return GitResult(success=False, message=f"获取 HEAD commit ID 失败: {result.message}")
    return GitResult(success=True, commit_id=result.message.strip())


def get_current_branch(repo_dir: str, timeout_cmd: int = 30, trace_id: Optional[str] = None) -> Optional[str]:
    """获取仓库当前所在分支名称，失败返回 None。"""
    err = _validate_repo_dir(repo_dir)
    if err:
        return None
    result = _run_git_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir,
        timeout=timeout_cmd,
        trace_id=trace_id,
    )
    if result.success and result.message:
        return result.message.strip()
    return None


def abort_rebase(repo_dir: str, timeout_cmd: int = 30, trace_id: Optional[str] = None) -> GitResult:
    """中止正在进行的 rebase 操作。"""
    return _run_git_command(["git", "rebase", "--abort"], cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id)


def create_github_pr_if_not_exists(
    repo_url: str,
    token: Optional[str],
    head_branch: str,
    base_branch: str,
    pr_title: str,
    pr_body: str = "",
    trace_id: Optional[str] = None,
) -> str:
    """
    检查指定分支是否已有对应的 GitHub PR，若无则自动创建。

    支持说明：
    - GitHub：调用 REST API 查询并按需创建 PR，返回 PR HTML URL
    - GitLab：打印不支持日志，返回空字符串（不报错）
    - 其他平台 / token 未配置 / API 异常：打印日志，返回空字符串（不报错）

    Args:
        repo_url:     仓库地址（git@ 或 https://，含或不含 .git 均可）
        token:        GitHub Personal Access Token（需有 repo 权限）
        head_branch:  来源分支（PR 的 head）
        base_branch:  目标分支（PR 的 base，合并目标）
        pr_title:     PR 标题
        pr_body:      PR 描述正文（可为空）
        trace_id:     日志追踪 ID

    Returns:
        成功时返回 PR 的 HTML URL；失败或不支持时返回空字符串。
    """
    if _requests is None:
        logger.warning(f"[trace_id={trace_id}] requests 库未安装，跳过 GitHub PR 创建")
        return ""

    try:
        base_url = get_web_url(repo_url)
    except ValueError as e:
        logger.error(f"[trace_id={trace_id}] 解析仓库 URL 失败，跳过 PR 创建: {e}")
        return ""

    if "gitlab" in base_url:
        logger.warning(
            f"[trace_id={trace_id}] GitLab 仓库暂不支持自动创建 MR，跳过: {base_url}"
        )
        return ""

    if "github.com" not in base_url:
        logger.warning(
            f"[trace_id={trace_id}] 非 GitHub/GitLab 仓库，跳过自动创建 PR: {base_url}"
        )
        return ""

    if not token:
        logger.warning(
            f"[trace_id={trace_id}] 仓库未配置 token，跳过 GitHub PR 自动创建: {base_url}"
        )
        return ""

    # 从 URL 解析 owner / repo
    path = base_url.split("github.com/", 1)[-1].strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        logger.error(
            f"[trace_id={trace_id}] 无法从 URL 解析 owner/repo，跳过 PR 创建: {base_url}"
        )
        return ""
    owner, repo_name = parts[0], parts[1]

    api_base = f"https://api.github.com/repos/{owner}/{repo_name}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # 查询是否已有 open PR
    try:
        resp = _requests.get(
            api_base,
            headers=headers,
            params={
                "state": "open",
                "head": f"{owner}:{head_branch}",
                "base": base_branch,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            prs = resp.json()
            if prs:
                pr_url = prs[0]["html_url"]
                logger.info(
                    f"[trace_id={trace_id}] GitHub PR 已存在，直接复用: {pr_url}"
                )
                return pr_url
        else:
            logger.warning(
                f"[trace_id={trace_id}] 查询 GitHub PR 失败 (status={resp.status_code}): {resp.text[:200]}"
            )
            return ""
    except Exception as e:
        logger.error(f"[trace_id={trace_id}] 查询 GitHub PR 异常: {e}")
        return ""

    # 创建新 PR
    try:
        resp = _requests.post(
            api_base,
            headers=headers,
            json={
                "title": pr_title,
                "head": head_branch,
                "base": base_branch,
                "body": pr_body,
            },
            timeout=30,
        )
        if resp.status_code == 201:
            pr_url = resp.json()["html_url"]
            logger.info(f"[trace_id={trace_id}] GitHub PR 创建成功: {pr_url}")
            return pr_url
        else:
            logger.warning(
                f"[trace_id={trace_id}] 创建 GitHub PR 失败 (status={resp.status_code}): {resp.text[:200]}"
            )
            return ""
    except Exception as e:
        logger.error(f"[trace_id={trace_id}] 创建 GitHub PR 异常: {e}")
        return ""


def find_git_repos_in_dir(parent_dir: str) -> list:
    """
    查找指定目录下一层子目录中的所有 git 仓库路径。

    Args:
        parent_dir: 父目录

    Returns:
        git 仓库路径列表
    """
    repo_dirs = []
    if not os.path.isdir(parent_dir):
        return repo_dirs
    for name in os.listdir(parent_dir):
        repo_dir = os.path.join(parent_dir, name)
        if not os.path.isdir(repo_dir):
            continue
        if os.path.isdir(os.path.join(repo_dir, ".git")):
            repo_dirs.append(repo_dir)
    return repo_dirs


def commit_and_push_all_repos(
    work_dir: str,
    commit_message: str = "default-commit-msg",
    trace_id: Optional[str] = None,
) -> None:
    """
    遍历工作目录下所有 git 仓库，自动提交未暂存修改并推送。

    Args:
        work_dir: 包含多个 git 仓库的工作目录
        commit_message: 提交信息
        trace_id: 追踪 ID
    """
    repo_dirs = find_git_repos_in_dir(parent_dir=work_dir)
    if not repo_dirs:
        logger.warning(f"[trace_id={trace_id}] 工作目录下未发现 git 仓库: {work_dir}")
        return

    for repo_dir in repo_dirs:
        status = run_git_command_or_raise(args=["status", "--porcelain"], cwd=repo_dir, trace_id=trace_id)
        if status:
            logger.info(f"[trace_id={trace_id}] 检测到未提交修改，自动提交: {repo_dir}")
            run_git_command_or_raise(args=["add", "-A"], cwd=repo_dir, trace_id=trace_id)
            run_git_command_or_raise(args=["commit", "-m", commit_message], cwd=repo_dir, trace_id=trace_id)
        logger.info(f"[trace_id={trace_id}] 自动执行 git push: {repo_dir}")
        push_result = _run_git_command(cmd=["git", "push"], cwd=repo_dir, trace_id=trace_id)
        if not push_result.success:
            # 远端分支不存在时，用 push -u origin <branch> 创建并关联
            branch = get_current_branch(repo_dir=repo_dir, trace_id=trace_id)
            if branch:
                logger.info(f"[trace_id={trace_id}] git push 失败，尝试 push -u origin {branch}: {repo_dir}")
                run_git_command_or_raise(args=["push", "-u", "origin", branch], cwd=repo_dir, trace_id=trace_id)
            else:
                raise Exception(f"[{repo_dir}] git push 失败且无法获取当前分支: {push_result.message}")
