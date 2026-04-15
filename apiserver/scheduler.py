#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生产环境部署定时调度器

每 15 秒轮询数据库中 prod 环境的待发布记录，自动执行部署。
作为 apiserver 的后台守护线程运行，随进程启动/退出。
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

DEPLOY_INTERVAL = 15  # 轮询间隔（秒）

_started = False
_lock = threading.Lock()


def start_deploy_scheduler():
    """
    启动部署调度器（幂等，仅启动一次）。

    内部启动一个守护线程，每 DEPLOY_INTERVAL 秒执行一次
    process_pending_prod_deploys()，自动处理 prod 环境的待发布记录。
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

    while True:
        try:
            from service.remote_deploy_service import process_pending_prod_deploys
            process_pending_prod_deploys()
        except Exception:
            logger.exception("Deploy scheduler iteration error")
        finally:
            try:
                remove_session()
            except Exception:
                pass
        time.sleep(DEPLOY_INTERVAL)
