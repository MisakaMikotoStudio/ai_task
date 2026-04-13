#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OpenCodeAgent - Open Code Agent 封装（待实现）
"""

from .base_agent import BaseAgent


from typing import Optional


class OpenCodeAgent(BaseAgent):
    """Open Code Agent（待实现）"""
    
    def _execute_prompt(self, trace_id: str, cwd: str, prompt: str, timeout: int, session_id: Optional[str] = None):
        """
        执行 prompt（待实现）
        
        Args:
            trace_id: 追踪标识，用于日志关联
            cwd: 工作目录
            timeout: 超时时间（秒）
            prompt: 要执行的 prompt
            session_id: 可选会话 ID（当前实现未使用）
            
        Raises:
            NotImplementedError: 该功能尚未实现
        """
        raise NotImplementedError("OpenCodeAgent 尚未实现")
