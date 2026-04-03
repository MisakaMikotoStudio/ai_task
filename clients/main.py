#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI任务需求管理系统 - Client 客户端
"""

import argparse
import logging
from math import log
import os
import time
from typing import Dict
from worker.code_develop_woker import CodeDevelopWorker

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - L%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

from worker.task_worker import TaskWorker
from config.config_model import ClientConfig


def _configure_log_level(log_level: str) -> None:
    level_name = (log_level or "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        raise ValueError(f"Invalid log level: {log_level}")
    logging.getLogger().setLevel(level)
    logger.setLevel(level)


class ClientRunner:
    """客户端运行器"""
    
    def __init__(self, client_config: ClientConfig):
        """
        初始化客户端运行器
        
        Args:
            client_config: 客户端配置
            workspace: 任务执行目录路径
            secret: 用户秘钥
        """
        self.client_config = client_config
        self.task_threads: Dict[str, TaskWorker] = {}
        self.running = True
        # 轮询间隔（秒）
        self.poll_interval = 1  # 心跳间隔1秒
    
    @property
    def client_id(self) -> int:
        return self.client_config.client_id
    
    @property
    def instance_uuid(self) -> str:
        return self.client_config.instance_uuid
    
    def cleanup_finished_threads(self, running_task_keys: set):
        """清理已结束的线程，以及不在 running tasks 中的任务"""
        keys_to_remove = []
        for task_key, thread in self.task_threads.items():
            if not thread.is_alive():
                # 线程已结束，直接清理
                keys_to_remove.append(task_key)
            elif task_key not in running_task_keys:
                # 任务不在 running tasks 中，停止线程并清理
                logger.info(f"任务 {task_key} 已不在运行列表中，停止线程")
                thread.stop()
                thread.join(timeout=10)
                if thread.is_alive():
                    logger.warning(f"任务线程 {task_key} 停止超时，暂不移除，等待下轮清理")
                    continue
                keys_to_remove.append(task_key)
        
        for key in keys_to_remove:
            del self.task_threads[key]
            logger.info(f"清理任务线程: {key}")
    
    def run(self):
        while self.running:
            try:
                # 定期同步客户端配置
                self.client_config.sync_config()
                # 发送心跳
                self.client_config.apiserver_rpc.sync_client(client_id=self.client_id, instance_uuid=self.instance_uuid)
                # 获取运行中的任务
                running_chat_messages = self.client_config.apiserver_rpc.get_running_chat_message(client_id=self.client_id)
                for item in running_chat_messages:
                    if not item.get('chat_messages'):
                        logger.error(f"任务 task_id={item.get('task_id')} 的 chat_id={item.get('chat_id')} 没有消息，跳过处理")
                        continue
                    item["key"] = f"task_{item.get('task_id')}_chat_{item.get('chat_id')}"
                running_task_keys = {task['key'] for task in running_chat_messages}
                # 清理已结束的线程，以及不在 running tasks 中的任务
                self.cleanup_finished_threads(running_task_keys)
                # 创建新任务处理线程
                for task in running_chat_messages:
                    if task['key'] in self.task_threads:
                        continue
                    worker = CodeDevelopWorker(task=task, client_config=self.client_config)
                    self.task_threads[item['key']] = worker
                    worker.start()
                    logger.info(f"创建任务处理线程: {task['key']}")
            except Exception as e:
                logger.error(f"客户端运行异常: {e}", exc_info=True)
            # 等待下一次轮询
            time.sleep(self.poll_interval)
    
    def stop(self):
        """停止客户端"""
        self.running = False
        # 停止所有任务线程
        for task_key, thread in self.task_threads.items():
            logger.info(f"停止任务线程: {task_key}")
            thread.stop()
            thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description='AI Task Management Client')
    parser.add_argument('--apiserver', '-a', type=str, required=True, default=None,
                        help='API server URL')
    parser.add_argument('--secret', '-s', type=str, required=True, default=None,
                        help='User secret for authentication')
    parser.add_argument('--client-id', '-i', type=int, required=True, default=None,
                        help='Client ID for authentication')
    parser.add_argument('--workspace', '-w', type=str, required=False, default="/workspace",
                        help='工作目录路径，保存客户端执行过程中的缓存数据等文件，必须保证用户对该目录有写权限')
    parser.add_argument('--log-level', '-l', type=str, required=False, default='INFO',
                        help='日志等级，例如 DEBUG/INFO/WARNING/ERROR')
    args = parser.parse_args()
    _configure_log_level(args.log_level)

    # 从云端加载客户端配置
    config = ClientConfig(apiserver_url=args.apiserver, client_id=args.client_id, secret=args.secret, workspace=args.workspace)
    logger.info(f"客户端instance_uuid: {config.instance_uuid}")
    config.sync_config()
    logger.info(f"当前登录用户: {config.login_user_name or '-'}")
    if not config.check_config():
        logger.error("客户端配置检查失败，客户端无法启动")
        return
    # 启动前先做一次心跳上报，失败则退出
    try:
        config.apiserver_rpc.sync_client(client_id=config.client_id, instance_uuid=config.instance_uuid)
        logger.info("初始心跳上报成功")
    except Exception as e:
        logger.error(f"初始心跳上报失败，客户端无法启动: {e}")
        return
    # 创建并运行客户端
    runner = ClientRunner(client_config=config)
    try:
        runner.run()
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止客户端...")
        runner.stop()


if __name__ == '__main__':
    main()
