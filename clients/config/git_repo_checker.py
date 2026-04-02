#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Git 仓库检查器
"""

import logging
import subprocess

from .base_checker import BaseChecker

logger = logging.getLogger(__name__)


class GitRepoChecker(BaseChecker):
    """Git 仓库访问检查器"""
    
    def check(self) -> bool:
        """
        检查单个 Git 仓库是否可访问
        如果仓库没有配置默认主分支，则自动获取并更新到 apiserver
        
        Args:
            repo: Git 仓库配置
            
        Returns:
            是否可访问
        """
        if not self.config.code_git:
            self.print_error_message("未配置任何代码仓库（repos 为空），无法启动客户端")
            return False

        for repo in self.config.code_git:
            url = repo.auth_url            
            try:
                logger.info(f"检查 Git 仓库可访问: {repo.name} ({repo.url})")
                result = subprocess.run(
                    ['git', 'ls-remote', '--exit-code', url],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode != 0:
                    self.print_error_message(f"Git 仓库无法访问: {repo.name} ({repo.url}), 错误: {result.stderr.strip()}")
                    return False
                logger.info(f"✓ Git 仓库可访问: {repo.name} ({repo.url})")
                continue
            except subprocess.TimeoutExpired:
                self.print_error_message(f"Git 仓库连接超时: {repo.name} ({repo.url})")
                return False
            except FileNotFoundError:
                self.print_error_message("Git 命令未找到，请确保已安装 Git")
                return False
            except Exception as e:
                self.print_error_message(f"Git 仓库检查异常: {repo.name} ({repo.url}), 错误: {e}")
                return False
        return True

