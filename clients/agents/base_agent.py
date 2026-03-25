#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BaseAgent - 所有 Agent 的基类
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Union

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Agent 基类，定义所有 Agent 的通用接口"""
    
    def __init__(self, name: str):
        """
        初始化 Agent
        
        Args:
            name: Agent 名称
        """
        self.name = name
    
    @abstractmethod
    def run_prompt(
        self, 
        trace_id: str,
        cwd: str,
        prompt: str, 
        session_id: Optional[str] = None,
        timeout: Optional[int] = 1800,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        实际执行 prompt 的抽象方法，子类需实现
        
        Args:
            trace_id: 追踪标识，用于日志关联
            cwd: 工作目录
            prompt: 要执行的 prompt
            timeout: 超时时间（秒）
            session_id: 可选会话 ID，用于续接上下文
            
        Returns:
            Tuple[Optional[str], Optional[str]]: 
            - Optional[str]: agent 执行结果
            - Optional[str]: agent 的执行 session_id（如果底层能获取），否则为 None
        """
        pass


