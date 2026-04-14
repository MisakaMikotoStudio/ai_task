#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Git 仓库检查器
"""

import logging

from .base_checker import BaseChecker

logger = logging.getLogger(__name__)

_AUTH_FAILURE_KEYWORDS = (
    "authentication failed",
    "invalid username or token",
    "could not read username",
    "403",
    "401",
)


def _is_auth_error(message: str) -> bool:
    """判断 git 错误是否为认证失败（token 过期或无效）。"""
    lower = message.lower()
    return any(kw in lower for kw in _AUTH_FAILURE_KEYWORDS)


class GitRepoChecker(BaseChecker):
    """Git 仓库访问检查器"""

    def check(self) -> bool:
        """
        检查单个 Git 仓库是否可访问。
        当检测到认证失败时，自动向 apiserver 请求刷新 GitHub App token 并重试一次。

        Returns:
            是否可访问
        """
        from utils.git_utils import check_repo_accessible

        if not self.config.code_git:
            self.print_error_message("未配置任何代码仓库（repos 为空），无法启动客户端")
            return False

        for repo in self.config.code_git:
            logger.info(f"检查 Git 仓库可访问: {repo.name} ({repo.url})")

            result = check_repo_accessible(auth_url=repo.auth_url, timeout=30)

            if not result.success:
                if _is_auth_error(result.message):
                    logger.warning(
                        f"Git 仓库认证失败，尝试刷新 token: {repo.name} ({repo.url})"
                    )
                    refreshed = self.config.refresh_repo_token(repo)
                    if refreshed:
                        result = check_repo_accessible(auth_url=repo.auth_url, timeout=30)

                if not result.success:
                    self.print_error_message(
                        f"Git 仓库无法访问: {repo.name} ({repo.url}), 错误: {result.message}"
                    )
                    return False

            logger.info(f"✓ Git 仓库可访问: {repo.name} ({repo.url})")
        return True
