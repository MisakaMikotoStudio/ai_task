#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API 服务器连接检查器
"""

import logging

from requests.exceptions import ConnectionError, Timeout

from .base_checker import BaseChecker

logger = logging.getLogger(__name__)


class ApiServerChecker(BaseChecker):
    """API 服务器连接检查器"""
    
    def check(self) -> bool:
        """
        检查后端 API 服务器是否联通
        
        Returns:
            是否联通
        """
        url = self.config.apiserver_rpc.base_url
        try:
            logger.info(f"检查 API 服务器健康状态: {url}")
            self.config.apiserver_rpc.check_health()
            logger.info(f"✓ API 服务器联通: {url}")
            return True
        except ConnectionError:
            self.print_error_message(f"无法连接到 API 服务器: {url}")
            return False
        except Timeout:
            self.print_error_message(f"连接 API 服务器超时: {url}")
            return False
        except Exception as e:
            self.print_error_message(f"API 服务器检查异常: {e}")
            return False
