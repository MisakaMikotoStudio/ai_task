#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI任务需求管理系统 - Client 客户端
"""

import argparse
import logging
import os
import time
from typing import Dict

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

from worker.task_worker import TaskWorker
from config.config_model import ClientConfig


class ClientRunner:
    """客户端运行器"""
    
    def __init__(self, config: ClientConfig, secret: str):
        """
        初始化客户端运行器
        
        Args:
            config: 客户端配置
            cache_dir: 缓存目录路径
            secret: 用户秘钥
        """
        self.config = config
        self.task_threads: Dict[str, TaskWorker] = {}
        self.running = True
        # 轮询间隔（秒）
        self.poll_interval = 1  # 心跳间隔1秒
    
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
                keys_to_remove.append(task_key)
        
        for key in keys_to_remove:
            del self.task_threads[key]
            logger.info(f"清理任务线程: {key}")
    
    def run(self):        
        while self.running:
            try:
                # 发送心跳
                self.config.apiserver_rpc.sync_client(client_id=self.config.client_id, instance_uuid=self.config.instance_uuid)
                # 获取运行中的任务
                tasks = self.config.apiserver_rpc.get_running_tasks(client_id=self.config.client_id)
                running_task_keys = {task.key for task in tasks}
                # 清理已结束的线程，以及不在 running tasks 中的任务
                self.cleanup_finished_threads(running_task_keys)
                # 创建新任务处理线程
                for task in tasks:
                    task_key = task.key
                    if task_key not in self.task_threads:
                        worker = TaskWorker(task=task, config=self.config)
                        self.task_threads[task_key] = worker
                        worker.start()
                        logger.info(f"创建任务处理线程: {task_key}")
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


def main():
    parser = argparse.ArgumentParser(description='AI Task Management Client')
    parser.add_argument('--apiserver', '-a', type=str, required=True, default=None,
                        help='API server URL')
    parser.add_argument('--secret', '-s', type=str, required=True, default=None,
                        help='User secret for authentication')
    parser.add_argument('--client-id', '-i', type=int, required=True, default=None,
                        help='Client ID for authentication')
    args = parser.parse_args()

    # 从云端加载客户端配置
    config = ClientConfig(apiserver_url=args.apiserver, client_id=args.client_id, secret=args.secret)
    if os.environ.get('CLOUD_AGENT') == '1':
        cache_dir = "/workspace"
    else:
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ai_task_cache")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        cache_dir = os.path.join(cache_dir, str(args.client_id))
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
    config.set_cache_dir(cache_dir)
    config.sync_config()
    # env_vars 在 sync_config() 后才从服务端拉取，cloud agent 在此注入环境变量
    if os.environ.get('CLOUD_AGENT') == '1' and config.env_vars:
        logger.info(f"检测到 cloud agent，注入 {len(config.env_vars)} 个环境变量")
        for key, value in config.env_vars.items():
            os.environ[key] = value
            logger.info(f"  设置环境变量: {key}")
    config.check_config()
    # 启动前先做一次心跳上报，失败则退出
    try:
        config.apiserver_rpc.sync_client(client_id=config.client_id, instance_uuid=config.instance_uuid)
        logger.info("初始心跳上报成功")
    except Exception as e:
        logger.error(f"初始心跳上报失败，客户端无法启动: {e}")
        return
    # 创建并运行客户端
    runner = ClientRunner(config=config, secret=args.secret)
    try:
        runner.run()
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止客户端...")
        runner.stop()


if __name__ == '__main__':
    main()
