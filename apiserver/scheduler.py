#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
部署相关定时调度器

- 部署调度：每 15 秒轮询 prod/test 环境的待发布记录，自动执行部署。
- 测试容器清理：每 1 小时扫描所有测试环境服务器，清理超过 1 天的
  `task{taskid}chat{chatid}msg{msg}` 格式测试容器。

均作为 apiserver 的后台守护线程运行，随进程启动/退出。
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

DEPLOY_INTERVAL = 15  # 部署调度轮询间隔（秒）
MAX_DEPLOY_WORKERS = 8  # 每轮调度并发处理 client 任务上限
TEST_CLEANUP_INTERVAL = 60 * 60  # 测试容器清理轮询间隔（秒）

_started = False
_lock = threading.Lock()
_cleanup_started = False
_cleanup_lock = threading.Lock()


def start_deploy_scheduler():
    """
    启动部署调度器（幂等，仅启动一次）。

    内部启动一个守护线程，每 DEPLOY_INTERVAL 秒执行一次部署轮询。
    """
    global _started
    with _lock:
        if _started:
            return
        _started = True

    thread = threading.Thread(target=_deploy_loop, daemon=True, name='deploy-scheduler')
    thread.start()
    logger.info("Deploy scheduler started, interval=%ds", DEPLOY_INTERVAL)


def _deploy_loop():
    """调度主循环：轮询 → 执行 → 清理 session → 等待"""
    from dao.connection import remove_session
    from dao.deploy_dao import get_pending_deploy_client_ids
    from service.remote_deploy_service import process_pending_deploys_prod, process_pending_deploys_test

    def _run_one(client_id: int):
        try:
            process_pending_deploys_prod(client_id=client_id)
            process_pending_deploys_test(client_id=client_id)
        except Exception:
            logger.exception("Deploy task failed: client_id=%s", client_id)
        finally:
            try:
                remove_session()
            except Exception:
                pass

    while True:
        try:
            client_ids = get_pending_deploy_client_ids()
            if client_ids:
                max_workers = max(1, min(len(client_ids), MAX_DEPLOY_WORKERS))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(_run_one, client_id) for client_id in client_ids]
                    for fut in as_completed(futures):
                        fut.result()
        except Exception:
            logger.exception("Deploy scheduler iteration error")
        finally:
            try:
                remove_session()
            except Exception:
                pass
        time.sleep(DEPLOY_INTERVAL)


def start_test_cleanup_scheduler():
    """
    启动测试容器清理调度器（幂等，仅启动一次）。

    内部启动一个守护线程，每 TEST_CLEANUP_INTERVAL 秒清理一次
    超过 1 天的测试环境容器（task{taskid}chat{chatid}msg{msg} 格式）。
    """
    global _cleanup_started
    with _cleanup_lock:
        if _cleanup_started:
            return
        _cleanup_started = True

    thread = threading.Thread(target=_test_cleanup_loop, daemon=True, name='test-cleanup-scheduler')
    thread.start()
    logger.info("Test cleanup scheduler started, interval=%ds", TEST_CLEANUP_INTERVAL)


def _test_cleanup_loop():
    """测试容器清理主循环：执行 → 清理 session → 等待"""
    from dao.connection import remove_session
    from service.remote_deploy_service import process_cleanup_expired_test_containers

    while True:
        try:
            process_cleanup_expired_test_containers()
        except Exception:
            logger.exception("Test cleanup scheduler iteration error")
        finally:
            try:
                remove_session()
            except Exception:
                pass
        time.sleep(TEST_CLEANUP_INTERVAL)
