#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务处理线程 - 总入口
"""

import logging
import threading
import time

from config.config_model import ClientConfig
from worker import CodeDevelopWorker

logger = logging.getLogger(__name__)


class TaskWorker(threading.Thread):
    """任务处理线程"""
    
    def __init__(self, task: dict, client_config: ClientConfig):
        super().__init__(name=task['key'], daemon=True)
        self.task = task
        self.client_config = client_config

    def run(self):
        """执行任务处理逻辑"""
        chat_message_preview = self.task['chat_messages'][-1]['input'][:10]
        logger.info(
            f"[{self.task['key']}] 开始处理任务，"
            f"task_title:{self.task.get('task_title','')}, "
            f"chat_title:{self.task.get('chat_title','')}, "
            f"chat_message:{chat_message_preview}..."
        )
        CodeDevelopWorker(task=self.task, client_config=self.client_config).run()

    def stop(self):
        """请求停止任务处理。"""
        pass