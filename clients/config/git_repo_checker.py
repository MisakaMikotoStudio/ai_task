#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Git 仓库检查器
"""

import logging

from .base_checker import BaseChecker

logger = logging.getLogger(__name__)


class GitRepoChecker(BaseChecker):
    """Git 仓库访问检查器"""

    def check(self) -> bool:
        """
        检查单个 Git 仓库是否可访问
        如果仓库没有配置默认主分支，则自动获取并更新到 apiserver

        Returns:
            是否可访问
        """
        from utils.git_utils import check_repo_accessible

        if not self.config.code_git:
            self.print_error_message("未配置任何代码仓库（repos 为空），无法启动客户端")
            return False

        for repo in self.config.code_git:
            url = repo.auth_url
            logger.info(f"检查 Git 仓库可访问: {repo.name} ({repo.url})")

            result = check_repo_accessible(auth_url=url, timeout=30)

            if not result.success:
                self.print_error_message(
                    f"Git 仓库无法访问: {repo.name} ({repo.url}), 错误: {result.message}"
                )
                return False

            logger.info(f"✓ Git 仓库可访问: {repo.name} ({repo.url})")
        return True
