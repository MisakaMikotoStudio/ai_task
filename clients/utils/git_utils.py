#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Git 仓库 Shell 工具
提供 Git 仓库的克隆、更新、同步等功能
"""

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from config.config_model import GitRepoConfig

logger = logging.getLogger(__name__)


@dataclass
class GitResult:
    """Git 操作结果"""
    success: bool
    message: str = ""
    default_branch: str = ""
    commit_id: str = ""
    branch_name: str = ""
    repo_name: str = ""
    merge_url: str = None


# ──────────────────────────────────────────────────────
#  URL 工具
# ──────────────────────────────────────────────────────

def get_repo_name_from_url(url: str) -> str:
    """从 Git URL 中提取仓库名称（不含 .git 后缀）。"""
    normalized = url[:-4] if url.endswith(".git") else url
    match = re.search(r"[:/]([^/:]+)$", normalized)
    if match:
        return match.group(1)
    raise ValueError(f"无法从 URL {normalized} 中提取仓库名称")


def get_auth_url(url: str, token: Optional[str] = None) -> str:
    """构造带认证信息的 URL，仅对 https URL 注入 token。"""
    if url.startswith("https://") and token:
        return url.replace("https://", f"https://x-access-token:{token}@")
    return url


def get_web_url(url: str) -> str:
    """
    将 Git 仓库 URL 统一转换为可访问的 Web URL（自动移除 .git 后缀）。
    支持: git@host:path, ssh://git@host/path, https://(含凭证), http://
    """
    u = url.strip()
    if u.startswith("git@"):
        u = u.replace(":", "/", 1).replace("git@", "https://")
    elif u.startswith("ssh://git@"):
        u = u.replace("ssh://git@", "https://", 1)
    elif u.startswith("https://") or u.startswith("http://"):
        protocol, rest = u.split("://", 1)
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        u = f"{protocol}://{rest}"
    else:
        raise ValueError(f"Git 仓库地址格式不正确: {url}")
    return u[:-4] if u.endswith(".git") else u


def get_path_prefix(url: str, branch: str) -> str:
    """根据仓库类型拼接文件浏览 URL 前缀。"""
    base_url = get_web_url(url)
    if "gitlab" in base_url:
        return f"{base_url}/-/blob/{branch}"
    return f"{base_url}/blob/{branch}"


# ──────────────────────────────────────────────────────
#  内部公共助手
# ──────────────────────────────────────────────────────

def _run_git_command(
    cmd: list,
    cwd: Optional[str] = None,
    timeout: int = 60,
    trace_id: Optional[str] = None,
) -> GitResult:
    """执行 Git 命令并返回结果。"""
    try:
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        if result.returncode == 0:
            return GitResult(success=True, message=result.stdout.strip() if result.stdout else "")
        error_msg = result.stderr.strip() if result.stderr else result.stdout.strip()
        return GitResult(success=False, message=error_msg)
    except subprocess.TimeoutExpired:
        return GitResult(success=False, message=f"命令超时: {' '.join(cmd)}")
    except Exception as e:
        return GitResult(success=False, message=f"执行命令异常: {str(e)}")


def _validate_repo_dir(repo_dir: str) -> Optional[GitResult]:
    """校验仓库目录是否有效。通过返回 None，失败返回包含错误信息的 GitResult。"""
    if not repo_dir:
        return GitResult(success=False, message="repo_dir 不能为空")
    if not os.path.exists(repo_dir):
        return GitResult(success=False, message=f"仓库目录不存在: {repo_dir}")
    if not os.path.exists(os.path.join(repo_dir, '.git')):
        return GitResult(success=False, message=f"目录不是有效的 Git 仓库: {repo_dir}")
    return None


def _fetch_all(
    repo_dir: str, timeout: int = 60, trace_id: Optional[str] = None,
) -> GitResult:
    """执行 git fetch --all --prune。"""
    return _run_git_command(
        ['git', 'fetch', '--all', '--prune'],
        cwd=repo_dir, timeout=timeout, trace_id=trace_id,
    )


def _check_remote_branch_exists(
    repo_dir: str, branch: str, timeout: int = 60, trace_id: Optional[str] = None,
) -> bool:
    """检查远端是否存在指定分支。"""
    result = _run_git_command(
        ['git', 'ls-remote', '--heads', 'origin', branch],
        cwd=repo_dir, timeout=timeout, trace_id=trace_id,
    )
    return result.success and branch in result.message


def _check_local_branch_exists(
    repo_dir: str, branch: str, timeout: int = 60, trace_id: Optional[str] = None,
) -> bool:
    """检查本地是否存在指定分支。"""
    result = _run_git_command(
        ['git', 'branch', '--list', branch],
        cwd=repo_dir, timeout=timeout, trace_id=trace_id,
    )
    return result.success and branch in result.message


def _get_remote_default_branch(
    repo_dir: str, timeout: int = 10, trace_id: Optional[str] = None,
) -> GitResult:
    """获取远端仓库的默认分支名称（三种策略逐一尝试）。"""
    def _strip_origin(ref: str) -> str:
        return ref[7:] if ref.startswith('origin/') else ref

    # 策略 1: symbolic-ref
    result = _run_git_command(
        ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD', '--short'],
        cwd=repo_dir, timeout=timeout, trace_id=trace_id,
    )
    if result.success and result.message:
        return GitResult(success=True, message=_strip_origin(result.message))

    # 策略 2: set-head --auto 后重试
    set_head = _run_git_command(
        ['git', 'remote', 'set-head', 'origin', '--auto'],
        cwd=repo_dir, timeout=timeout, trace_id=trace_id,
    )
    if set_head.success:
        result = _run_git_command(
            ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD', '--short'],
            cwd=repo_dir, timeout=timeout, trace_id=trace_id,
        )
        if result.success and result.message:
            return GitResult(success=True, message=_strip_origin(result.message))

    # 策略 3: 从远端分支列表中猜测 main / master
    branches_result = _run_git_command(
        ['git', 'branch', '-r'],
        cwd=repo_dir, timeout=timeout, trace_id=trace_id,
    )
    if branches_result.success:
        for preferred in ['origin/main', 'origin/master']:
            if preferred in branches_result.message:
                return GitResult(success=True, message=preferred.replace('origin/', ''))

    return GitResult(success=False, message="无法获取远端默认分支")


def _build_merge_request_url(repo_dir: str, branch: str, timeout_cmd: int = 60) -> str:
    """依据 origin URL 生成该分支对应的 MR/PR 查询链接。"""
    remote_result = _run_git_command(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_dir, timeout=timeout_cmd,
    )
    if not remote_result.success:
        return ""
    try:
        base_url = get_web_url(remote_result.message)
    except ValueError:
        return ""
    if "gitlab" in base_url:
        return f"{base_url}/-/merge_requests?scope=all&state=opened&source_branch={quote(branch, safe='')}"
    return f"{base_url}/pulls?q=is%3Apr+is%3Aopen+head%3A{quote(branch, safe='')}"


def ensure_git_identity_configured(
    default_name: str = "AI Task Bot",
    default_email: str = "ai-task-bot@example.com",
    timeout_cmd: int = 10,
    trace_id: Optional[str] = None,
) -> GitResult:
    """
    确保当前环境已配置 git user.name 和 user.email（全局配置）。

    说明：
    - 仅在未配置时写入默认值
    - 已存在配置时不覆盖
    """
    try:
        name_result = _run_git_command(
            ["git", "config", "--global", "--get", "user.name"],
            timeout=timeout_cmd, trace_id=trace_id,
        )
        email_result = _run_git_command(
            ["git", "config", "--global", "--get", "user.email"],
            timeout=timeout_cmd, trace_id=trace_id,
        )

        need_set_name = not (name_result.success and name_result.message.strip())
        need_set_email = not (email_result.success and email_result.message.strip())

        if need_set_name:
            set_name = _run_git_command(
                ["git", "config", "--global", "user.name", default_name],
                timeout=timeout_cmd, trace_id=trace_id,
            )
            if not set_name.success:
                return GitResult(success=False, message=f"设置 git user.name 失败: {set_name.message}")

        if need_set_email:
            set_email = _run_git_command(
                ["git", "config", "--global", "user.email", default_email],
                timeout=timeout_cmd, trace_id=trace_id,
            )
            if not set_email.success:
                return GitResult(success=False, message=f"设置 git user.email 失败: {set_email.message}")

        if need_set_name or need_set_email:
            logger.warning(
                f"[trace_id={trace_id}] Git 全局身份未完整配置，已自动补齐 "
                f"user.name={default_name}, user.email={default_email}"
            )
            return GitResult(success=True, message="Git identity configured")
        return GitResult(success=True, message="Git identity already configured")
    except Exception as e:
        return GitResult(success=False, message=f"检查 Git 身份配置异常: {str(e)}")


# ──────────────────────────────────────────────────────
#  对外业务函数
# ──────────────────────────────────────────────────────

def update_remote_auth_url(
    repo_dir: str,
    auth_url: str,
    timeout: int = 60,
    trace_id: Optional[str] = None,
) -> GitResult:
    """更新本地仓库的 remote origin URL，确保 token 轮换后仍可认证。"""
    return _run_git_command(
        ['git', 'remote', 'set-url', 'origin', auth_url],
        cwd=repo_dir, timeout=timeout, trace_id=trace_id,
    )


def clone_or_sync_repo(
    work_dir: str,
    repo_config: "GitRepoConfig",
    timeout_clone: int = 60,
    timeout_cmd: int = 10,
    trace_id: Optional[str] = None,
) -> GitResult:
    """
    克隆或同步 Git 仓库。

    流程:
    1. 如果仓库已存在则跳过克隆，否则执行 git clone
    2. 获取或确认默认主分支
    3. 更新 remote URL
    4. fetch 远端
    5. 丢弃本地所有修改
    6. 切换到默认主分支（若不存在则创建并推送）
    7. 强制同步远端
    """
    repo_name = repo_config.name
    repo_dir = os.path.join(work_dir, repo_name)
    auth_url = repo_config.auth_url

    try:
        os.makedirs(work_dir, exist_ok=True)

        # 1. 克隆
        if not os.path.exists(repo_dir):
            clone_result = _run_git_command(
                ['git', 'clone', auth_url, repo_dir],
                cwd=work_dir, timeout=timeout_clone, trace_id=trace_id,
            )
            if not clone_result.success:
                return GitResult(success=False, message=f"克隆仓库失败: {clone_result.message}")
            logger.info(f"[trace_id={trace_id}] [{repo_name}] 克隆仓库成功: {repo_dir}")

        # 2. 默认主分支
        default_branch = repo_config.default_branch
        if not default_branch:
            branch_result = _get_remote_default_branch(repo_dir, timeout_cmd, trace_id=trace_id)
            if not branch_result.success:
                return GitResult(success=False, message=f"获取远端默认分支失败: {branch_result.message}")
            default_branch = branch_result.message
            logger.info(f"[trace_id={trace_id}] [{repo_name}] 获取到远端默认分支: {default_branch}")

        # 3. 更新 remote URL
        set_url_result = update_remote_auth_url(repo_dir, auth_url, timeout_cmd, trace_id=trace_id)
        if not set_url_result.success:
            return GitResult(success=False, message=f"更新远端 URL 失败: {set_url_result.message}")

        # 4. fetch
        fetch_result = _fetch_all(repo_dir, timeout_cmd, trace_id=trace_id)
        if not fetch_result.success:
            return GitResult(success=False, message=f"fetch 远端失败: {fetch_result.message}")

        # 5. 丢弃本地所有修改
        _run_git_command(['git', 'restore', '.'], cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id)
        reset_local = _run_git_command(
            ['git', 'reset', '--hard'], cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
        )
        if not reset_local.success:
            return GitResult(success=False, message=f"重置本地修改失败: {reset_local.message}")
        _run_git_command(['git', 'clean', '-fd'], cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id)
        logger.info(f"[trace_id={trace_id}] [{repo_name}] 已丢弃本地所有修改")

        # 6. 切换到默认分支（若不存在则创建并推送）
        remote_exists = _check_remote_branch_exists(repo_dir, default_branch, timeout_cmd, trace_id=trace_id)
        local_exists = _check_local_branch_exists(repo_dir, default_branch, timeout_cmd, trace_id=trace_id)

        if not remote_exists and not local_exists:
            logger.warning(f"[trace_id={trace_id}] [{repo_name}] 默认分支不存在，将创建新分支并推送: {default_branch}")
            create = _run_git_command(
                ['git', 'checkout', '-b', default_branch],
                cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
            )
            if not create.success:
                return GitResult(success=False, message=f"创建默认分支 {default_branch} 失败: {create.message}")
            push = _run_git_command(
                ['git', 'push', '-u', 'origin', default_branch],
                cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
            )
            if not push.success:
                return GitResult(success=False, message=f"推送新默认分支 {default_branch} 失败: {push.message}")
            logger.info(f"[trace_id={trace_id}] [{repo_name}] 已创建并推送默认分支: origin/{default_branch}")
        else:
            checkout = _run_git_command(
                ['git', 'checkout', default_branch],
                cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
            )
            if not checkout.success:
                return GitResult(success=False, message=f"切换到分支 {default_branch} 失败: {checkout.message}")
            logger.info(f"[trace_id={trace_id}] [{repo_name}] 已切换到分支: {default_branch}")

        # 7. 强制同步远端
        reset_result = _run_git_command(
            ['git', 'reset', '--hard', f'origin/{default_branch}'],
            cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
        )
        if not reset_result.success:
            return GitResult(success=False, message=f"重置到远端分支失败: {reset_result.message}")

        logger.info(f"[trace_id={trace_id}] [{repo_name}] 仓库已同步到远端最新: origin/{default_branch}")
        return GitResult(success=True, message=f"仓库同步成功: {repo_dir}", default_branch=default_branch)

    except Exception as e:
        logger.error(f"[trace_id={trace_id}] [{repo_name}] 仓库操作异常: {e}", exc_info=True)
        return GitResult(success=False, message=f"仓库操作异常: {str(e)}")


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
            _run_git_command(
                ['git', 'rebase', '--abort'],
                cwd=repo_dir, timeout=timeout_cmd, trace_id=trace_id,
            )
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

        return GitResult(
            success=True,
            message=f"分支 {dev_branch} 已成功 rebase 并推送到云端",
        )

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
        no_diff = GitResult(
            success=True, message="no_diff",
            repo_name=repo_name, branch_name=dev_branch, merge_url='',
        )

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


# ──────────────────────────────────────────────────────
#  使用示例
# ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - L%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    config = GitRepoConfig(
        url="https://github.com/example/repo.git",
        token="your_token_here",
        default_branch=""
    )

    work_dir = "./test_repos"
    result = clone_or_sync_repo(work_dir, config)

    print(f"Success: {result.success}")
    print(f"Message: {result.message}")
    print(f"Default Branch: {result.default_branch}")

    sys.exit(0 if result.success else 1)
