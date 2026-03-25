#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Agents 模块 - 各类 AI Agent 的抽象封装
"""

from .base_agent import BaseAgent
from .claude_agent_sdk_agent import ClaudeAgentSdkAgent
from .claude_code_cli_agent import ClaudeCodeCliAgent
from .open_code_agent import OpenCodeAgent

# 向后兼容历史导出名
ClaudeCodeAgent = ClaudeCodeCliAgent

__all__ = [
    'BaseAgent',
    'ClaudeCodeAgent',
    'ClaudeCodeCliAgent',
    'ClaudeAgentSdkAgent',
    'OpenCodeAgent',
    'get_agent_by_name',
    'AGENT_REGISTRY',
]

# Agent 注册表：名称 -> Agent 类
AGENT_REGISTRY = {
    'claude sdk': ClaudeAgentSdkAgent,
    'claude cli': ClaudeCodeCliAgent,
    'Open Code': OpenCodeAgent,
}


def get_agent_by_name(agent_name: str) -> BaseAgent:
    """
    根据 Agent 名称获取对应的 Agent 实例
    
    Args:
        agent_name: Agent 名称
        
    Returns:
        Agent 实例
        
    Raises:
        ValueError: 如果 Agent 名称无效
    """
    agent_class = AGENT_REGISTRY.get(agent_name)
    if agent_class is None:
        # 大小写不敏感兜底匹配
        agent_name_lower = agent_name.lower()
        for key, cls in AGENT_REGISTRY.items():
            if key.lower() == agent_name_lower:
                agent_class = cls
                break
    if agent_class is None:
        available = ', '.join(AGENT_REGISTRY.keys())
        raise ValueError(f"未知的 Agent 类型: {agent_name}，可用类型: {available}")
    return agent_class()
