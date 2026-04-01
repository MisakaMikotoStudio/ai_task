#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基础节点类 - 所有执行节点的基类
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Callable
from agents.base_agent import BaseAgent
from rpc.apiserver_rpc import ApiServerRpc
from config.config_model import ClientConfig
import threading

logger = logging.getLogger(__name__)


class BaseWorker(threading.Thread):
    """执行节点基类"""
    
    # 节点名称，子类需要重写
    worker_name: str = "base"
    worker_key: str = "base"
    
    def __init__(self, task: dict, client_config: ClientConfig):
        super().__init__(name=task['key'], daemon=True)
        # `trace_id` is exposed as a read-only @property below.
        # Do not assign to it here (it has no setter); rely on `self.task`.
        self.task = task
        self.client_config = client_config

    # ==================== Properties ====================
    @property
    def trace_id(self) -> str:
        return self.task['key']
    
    @property
    def workspace(self) -> str:
        return self.client_config.workspace
    
    @property
    def agent(self) -> BaseAgent:
        return self.client_config.agent
    
    @property
    def apiserver_rpc(self) -> str:
        return self.client_config.apiserver_rpc
    # ==================== Abstract Methods ====================

    @abstractmethod
    def execute(self):
        """执行节点核心逻辑"""
        pass

    @abstractmethod
    def before_execute(self):
        """准备执行节点逻辑 - 准备执行节点所需的环境和数据"""
        pass

    @abstractmethod
    def after_execute(self):
        """执行后处理逻辑 - 执行后处理逻辑，如保存执行信息到文件、更新任务状态等"""
        pass
    
    @abstractmethod
    def exception_handler(self, e: Exception):
        """异常处理逻辑 - 异常处理逻辑，如保存异常信息到文件、更新任务状态等"""
        pass

    # ==================== Public Methods ====================

    def run(self):
        try:
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 开始执行")
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 开始执行: 执行环境准备")
            self.before_execute()
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 开始执行: 执行主逻辑")
            self.execute()
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 开始执行: 执行后续逻辑")
            self.after_execute()
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 执行 完成")
        except Exception as e:
            logger.error(f"[{self.trace_id}] 节点 {self.worker_name} 执行失败: {e}")
            self.exception_handler(e)

    def stop(self):
        """停止节点执行"""
        pass