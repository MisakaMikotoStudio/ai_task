#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
基础节点类 - 所有执行节点的基类
"""

import logging
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Optional

from agents.base_agent import BaseAgent
from config.config_model import ClientConfig
from worker.execute_marker import write_last_execute_marker

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
        self._stop_event = threading.Event()
        self._process_lock = threading.Lock()
        self._managed_processes: dict[int, tuple[subprocess.Popen, str]] = {}

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
    def stop_event(self) -> threading.Event:
        return self._stop_event

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()
    
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
            if self.stop_requested:
                logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 收到停止信号，跳过执行")
                return
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 开始执行")
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 开始执行: 执行环境准备")
            self.before_execute()
            if self.stop_requested:
                logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 在环境准备后收到停止信号")
                return
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 开始执行: 执行主逻辑")
            self.execute()
            if self.stop_requested:
                logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 在主逻辑后收到停止信号")
                return
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 开始执行: 执行后续逻辑")
            self.after_execute()
            logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 执行 完成")
        except Exception as e:
            if self.stop_requested:
                logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 停止完成: {e}")
                return
            logger.error(f"[{self.trace_id}] 节点 {self.worker_name} 执行失败: {e}")
            self.exception_handler(e)
        finally:
            work_dir = getattr(self, "work_dir", None)
            if isinstance(work_dir, str) and work_dir:
                write_last_execute_marker(work_dir)
            self._cleanup_managed_processes()

    def stop(self):
        """停止节点执行，并终止 worker 托管的所有子进程"""
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        logger.info(f"[{self.trace_id}] 节点 {self.worker_name} 收到停止请求")
        self._terminate_managed_processes()

    def run_agent_prompt(
        self,
        prompt: str,
        cwd: str,
        session_id: Optional[str] = None,
        timeout: Optional[int] = 1800,
    ):
        """通过统一托管入口调用 agent，确保底层子进程可被 stop() 终止。"""
        if self.stop_requested:
            raise RuntimeError(f"[{self.trace_id}] 节点 {self.worker_name} 已停止，拒绝继续调用 agent")
        result = self.agent.run_prompt(
            trace_id=self.trace_id,
            cwd=cwd,
            prompt=prompt,
            session_id=session_id,
            timeout=timeout,
            popen_factory=self.create_managed_popen,
            process_cleanup=self.unregister_process,
            stop_event=self.stop_event,
        )
        if self.stop_requested:
            raise RuntimeError(f"[{self.trace_id}] 节点 {self.worker_name} 已停止，丢弃 agent 输出")
        return result

    def create_managed_popen(
        self,
        *args,
        process_name: Optional[str] = None,
        **kwargs,
    ) -> subprocess.Popen:
        """创建并登记子进程，便于 stop() 时统一回收。"""
        process = subprocess.Popen(*args, **kwargs)
        self.register_process(process, process_name=process_name)
        return process

    def register_process(
        self,
        process: subprocess.Popen,
        process_name: Optional[str] = None,
    ) -> None:
        name = process_name or getattr(process, "args", None) or "subprocess"
        with self._process_lock:
            self._managed_processes[id(process)] = (process, str(name))

    def unregister_process(self, process: object) -> None:
        with self._process_lock:
            self._managed_processes.pop(id(process), None)

    def _snapshot_managed_processes(self) -> list[tuple[subprocess.Popen, str]]:
        with self._process_lock:
            return list(self._managed_processes.values())

    def _terminate_managed_processes(self) -> None:
        for process, process_name in self._snapshot_managed_processes():
            if process.poll() is not None:
                self.unregister_process(process)
                continue
            try:
                logger.info(f"[{self.trace_id}] 终止子进程: {process_name} pid={process.pid}")
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f"[{self.trace_id}] 子进程未及时退出，强制 kill: {process_name} pid={process.pid}")
                process.kill()
                process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"[{self.trace_id}] 终止子进程失败: {process_name} pid={process.pid}, error={e}")
            finally:
                self.unregister_process(process)

    def _cleanup_managed_processes(self) -> None:
        for process, _process_name in self._snapshot_managed_processes():
            if process.poll() is not None:
                self.unregister_process(process)