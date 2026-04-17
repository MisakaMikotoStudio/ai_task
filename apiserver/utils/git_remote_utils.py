#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
远端 Git 操作工具：在 SSH 通道上执行 clone / fetch / checkout，
包含 per-command 快速失败环境变量、指数退避重试、认证失败识别。

仅依赖 utils.ssh_utils.SshClient，不依赖 service 层，方便复用。
业务异常由调用方捕获 GitRemoteError 并转换。
"""

import logging
import time

logger = logging.getLogger(__name__)


# clone/fetch 单次命令 SSH 层 channel 超时（秒）
# 作为兜底上限，实际生效的更短时限是下面的 GIT_ATTEMPT_HARD_TIMEOUT_SEC。
GIT_CLONE_TIMEOUT = 120
# 网络类失败重试次数
GIT_CLONE_MAX_RETRIES = 3
# 重试退避基数：第 N 次失败 sleep BASE * 2^(N-1) 秒
GIT_RETRY_BACKOFF_BASE_SEC = 1

# 单次 git 调用的硬时限（秒），通过 coreutils `timeout` 包裹。
# 目的是避免跨境 TCP connect 阶段一直卡到内核默认 ~130s 才失败。
# 低于该时限时 git 自身的 LOW_SPEED 或 connectTimeout 会先触发失败；
# 超过该时限则由 `timeout` 以 SIGTERM 强制终止并以 exit=124 返回。
GIT_ATTEMPT_HARD_TIMEOUT_SEC = 45

# libcurl 连接阶段超时（秒），通过 `git -c http.connectTimeout=...` 注入。
# git >= 2.30 映射到 CURLOPT_CONNECTTIMEOUT，覆盖 LOW_SPEED 不生效的 TCP 握手阶段。
GIT_HTTP_CONNECT_TIMEOUT_SEC = 10

# 每条 git 命令前置 per-command 环境变量实现快速失败，避免跨境链路 stall
# 到 TCP 超时（~130s）才失败；不写入 ~/.gitconfig，不影响服务器其他 git 使用。
# - GIT_HTTP_LOW_SPEED_LIMIT=1000: 低于 1KB/s
# - GIT_HTTP_LOW_SPEED_TIME=20:   持续 20 秒 → 判定失败
GIT_HTTP_FAIL_FAST_ENV = 'GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=20'

# 包裹在每条 git 子命令前的"硬截断 + 连接超时"前缀
# 使用时放在 `git ...` 前即可，注意 shell 顺序：
#   timeout 45 git -c http.connectTimeout=10 <subcmd>
_GIT_ATTEMPT_PREFIX = (
    f'timeout {GIT_ATTEMPT_HARD_TIMEOUT_SEC} '
    f'git -c http.connectTimeout={GIT_HTTP_CONNECT_TIMEOUT_SEC}'
)


class GitRemoteError(Exception):
    """远端 git 操作失败（clone/fetch/checkout 等）"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def build_auth_url(url: str, token: str) -> str:
    """将 https://github.com 替换为带 x-access-token 认证的形式。"""
    if not url:
        return url
    return url.replace('https://github.com', f'https://x-access-token:{token}@github.com')


def is_git_auth_error(message: str) -> bool:
    """根据 stderr/stdout 文本判断是否为 git 认证失败。"""
    lower_msg = (message or '').lower()
    return (
        'authentication failed' in lower_msg
        or 'invalid username or token' in lower_msg
        or 'password authentication is not supported' in lower_msg
    )


def clone_repo_with_retry(
    ssh,
    auth_url: str,
    branch: str,
    repo_dir: str,
    repo_name: str,
    trace_id: str,
) -> None:
    """clone 仓库（浅克隆 + 重试 + 指数退避），失败时清理残留目录。"""
    target_path = f'{repo_dir}/{repo_name}'
    # --no-tags 减少拉取量；per-command 低速超时 + connectTimeout + 外层 timeout 三重兜底，
    # 确保单次 attempt 最长不超过 GIT_ATTEMPT_HARD_TIMEOUT_SEC 秒
    clone_cmd = (
        f'cd {repo_dir} && {GIT_HTTP_FAIL_FAST_ENV} {_GIT_ATTEMPT_PREFIX} '
        f'clone --depth 1 --single-branch --no-tags --branch {branch} {auth_url} {repo_name}'
    )

    last_err = None
    for attempt in range(1, GIT_CLONE_MAX_RETRIES + 1):
        logger.info(
            "[trace_id=%s] Cloning repo (attempt %d/%d): %s -> %s",
            trace_id, attempt, GIT_CLONE_MAX_RETRIES, repo_name, target_path,
        )
        try:
            ssh.execute(command=clone_cmd, timeout=GIT_CLONE_TIMEOUT)
            return
        except Exception as e:
            msg = getattr(e, 'message', None) or str(e)
            last_err = GitRemoteError(msg)
            logger.warning(
                "[trace_id=%s] Clone attempt %d failed, repo=%s, detail=%s",
                trace_id, attempt, repo_name, msg,
            )
            ssh.execute_ignore_error(command=f'rm -rf {target_path}')
            if attempt < GIT_CLONE_MAX_RETRIES and not is_git_auth_error(msg):
                backoff = GIT_RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.info(
                    "[trace_id=%s] Clone backoff %ds before retry, repo=%s",
                    trace_id, backoff, repo_name,
                )
                time.sleep(backoff)
            elif is_git_auth_error(msg):
                break

    raise GitRemoteError(
        f"仓库 {repo_name} 克隆失败（已重试{GIT_CLONE_MAX_RETRIES}次）："
        f"{last_err.message if last_err else 'unknown'}"
    )


def fetch_repo_with_retry(
    ssh,
    target_path: str,
    auth_url: str,
    repo_name: str,
    branch: str,
    trace_id: str,
) -> None:
    """更新已有仓库（浅 fetch 指定分支 + 重试 + 指数退避）。

    相比无参数的 `git fetch origin`，只拉取目标分支的最新 1 个 commit，
    数据量最小化，显著降低跨境链路 TLS 被中断的概率。
    """
    fetch_cmd = (
        f'cd {target_path} && git remote set-url origin {auth_url} && '
        f'{GIT_HTTP_FAIL_FAST_ENV} {_GIT_ATTEMPT_PREFIX} '
        f'fetch --depth 1 --no-tags origin {branch}'
    )

    last_err = None
    for attempt in range(1, GIT_CLONE_MAX_RETRIES + 1):
        logger.info(
            "[trace_id=%s] Fetching repo (attempt %d/%d): %s, branch=%s",
            trace_id, attempt, GIT_CLONE_MAX_RETRIES, repo_name, branch,
        )
        try:
            ssh.execute(command=fetch_cmd, timeout=GIT_CLONE_TIMEOUT)
            return
        except Exception as e:
            msg = getattr(e, 'message', None) or str(e)
            last_err = GitRemoteError(msg)
            logger.warning(
                "[trace_id=%s] Fetch attempt %d failed, repo=%s, detail=%s",
                trace_id, attempt, repo_name, msg,
            )
            # 认证错误不退避、不重试，立即抛给上层刷新 token
            if is_git_auth_error(msg):
                break
            if attempt < GIT_CLONE_MAX_RETRIES:
                backoff = GIT_RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))
                logger.info(
                    "[trace_id=%s] Fetch backoff %ds before retry, repo=%s",
                    trace_id, backoff, repo_name,
                )
                time.sleep(backoff)

    raise GitRemoteError(
        f"仓库 {repo_name} fetch 失败（已重试{GIT_CLONE_MAX_RETRIES}次）："
        f"{last_err.message if last_err else 'unknown'}"
    )


def fetch_or_reclone(
    ssh,
    target_path: str,
    repo_dir: str,
    repo_name: str,
    branch: str,
    auth_url: str,
    trace_id: str,
) -> None:
    """优先增量 fetch；fetch 持续失败（非认证错误）时删除本地目录重新浅 clone。

    认证错误保留原语义，抛给上层统一刷新 token 后重建。
    """
    try:
        fetch_repo_with_retry(
            ssh=ssh, target_path=target_path, auth_url=auth_url,
            repo_name=repo_name, branch=branch, trace_id=trace_id,
        )
    except GitRemoteError as fetch_err:
        if is_git_auth_error(fetch_err.message):
            raise
        logger.warning(
            "[trace_id=%s] Fetch persistently failed for %s, fallback to re-clone, detail=%s",
            trace_id, repo_name, fetch_err.message,
        )
        # 结构化指标日志：便于 ELK/Loki 等按 metric 关键字聚合统计跨境网络劣化频率。
        # 字段以 key=value 形式扁平化，避免污染主调用链 INFO 日志的可读性。
        logger.warning(
            "[trace_id=%s] metric=git_fetch_fallback_reclone repo=%s branch=%s retries=%d",
            trace_id, repo_name, branch, GIT_CLONE_MAX_RETRIES,
        )
        ssh.execute_ignore_error(command=f'rm -rf {target_path}')
        clone_repo_with_retry(
            ssh=ssh, auth_url=auth_url, branch=branch,
            repo_dir=repo_dir, repo_name=repo_name, trace_id=trace_id,
        )


def checkout_commit_with_auth_refresh(
    ssh,
    tmp_repo_dir: str,
    commit_id: str,
    url: str,
    token: str,
    repo_name: str,
    token_provider,
    trace_id: str,
) -> str:
    """在临时仓库目录中 checkout 指定 commit。

    优先直接 `git checkout`，本地无该 commit 时走 fetch 回退。
    若 fetch 遇到认证失败，则调用 `token_provider(url=, repo_name=, trace_id=)`
    拿到新 token 重试一次，返回最终使用的 token（方便调用方更新缓存）。
    """
    auth_url = build_auth_url(url=url, token=token)
    checkout_cmd = (
        f'cd {tmp_repo_dir} && '
        f'git checkout {commit_id} || '
        f'(git remote set-url origin {auth_url} && '
        f'{GIT_HTTP_FAIL_FAST_ENV} {_GIT_ATTEMPT_PREFIX} '
        f'fetch --depth 1 --no-tags origin {commit_id} && '
        f'git checkout {commit_id})'
    )
    try:
        ssh.execute(command=checkout_cmd, timeout=GIT_CLONE_TIMEOUT)
        return token
    except Exception as e:
        msg = getattr(e, 'message', None) or str(e)
        if not is_git_auth_error(msg):
            raise GitRemoteError(msg)

        logger.warning(
            "[trace_id=%s] Checkout auth failed, retry with refreshed token: repo=%s",
            trace_id, repo_name,
        )
        new_token = token_provider(url=url, repo_name=repo_name, trace_id=trace_id)
        new_auth_url = build_auth_url(url=url, token=new_token)
        retry_cmd = (
            f'cd {tmp_repo_dir} && '
            f'git remote set-url origin {new_auth_url} && '
            f'{GIT_HTTP_FAIL_FAST_ENV} {_GIT_ATTEMPT_PREFIX} '
            f'fetch --depth 1 --no-tags origin {commit_id} && '
            f'git checkout {commit_id}'
        )
        ssh.execute(command=retry_cmd, timeout=GIT_CLONE_TIMEOUT)
        return new_token
