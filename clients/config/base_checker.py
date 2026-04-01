#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基础检查器类
"""

import logging
from abc import ABC, abstractmethod
from os import name
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from config.config_model import ClientConfig

logger = logging.getLogger(__name__)


class BaseChecker(ABC):
    """检查器基类"""    
    def __init__(self, config: "ClientConfig"):
        self.config = config
    
    def print_error_message(self, message: str):
        """打印错误信息"""
        logger.error(f"✗ {self.__class__.__name__} - {message}")    

    def print_warning_message(self, message: str):
        """打印警告信息"""
        logger.warning(f"⚠ {self.name} - {message}")

    @abstractmethod
    def check(self) -> bool:
        """
        执行检查
        
        Returns:
            是否通过检查
        """
        pass
