#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CloudAgent - 云客户端使用的 Agent 封装
"""

from .base_agent import BaseAgent
from .claude_code_agent import ClaudeCodeAgent


class CloudAgent(ClaudeCodeAgent):
    """云客户端使用的 agent"""

    def __init__(self):
        BaseAgent.__init__(self, name="Cloud", timeout=1800)