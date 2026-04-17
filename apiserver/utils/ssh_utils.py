#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SSH 工具：统一 SSH 连接、重试执行与上下文管理。
"""

import logging
import re
import socket
import time
import uuid

logger = logging.getLogger(__name__)


def _sanitize_command(command: str) -> str:
    """移除命令中的 token/密码等敏感信息，用于日志输出。"""
    return re.sub(r'x-access-token:[^@]+@', 'x-access-token:***@', command)


class SSHClientError(Exception):
    """SSH 执行失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RetryableSshError(SSHClientError):
    """可重试的 SSH 异常（网络抖动、连接中断等）"""


class SshClient:
    """支持 with 的 SSH 客户端封装。"""

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        connect_timeout: int = 10,
        keepalive: int = 30,
        retries: int = 3,
        retry_interval_sec: int = 1,
        trace_id: str | None = None,
    ):
        self.ip = ip
        self.username = username
        self.password = password
        self.connect_timeout = connect_timeout
        self.keepalive = keepalive
        self.retries = max(1, retries)
        self.retry_interval_sec = max(0, retry_interval_sec)
        self.trace_id = trace_id or ''
        self._client = None

    @property
    def _log_prefix(self) -> str:
        return f"[trace_id={self.trace_id}] " if self.trace_id else ""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def connect(self):
        import paramiko

        if self._client:
            return
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.ip,
            username=self.username,
            password=self.password,
            timeout=self.connect_timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(self.keepalive)
        self._client = client

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def exec_command(self, *args, **kwargs):
        self._ensure_connected()
        return self._client.exec_command(*args, **kwargs)

    def open_sftp(self):
        self._ensure_connected()
        return self._client.open_sftp()

    def execute(self, command: str, timeout: int | None = None, retries: int | None = None) -> str:
        """执行远程命令，网络类异常自动重试。

        成功/失败均记录日志；日志中的命令会脱敏 token/密码等敏感片段。
        失败时日志保留 stdout/stderr 尾部，docker build 等场景下的真实错误
        行不会被长篇 usage 文本挤出窗口。

        timeout 同时作为 paramiko exec_command 的 channel 超时，命令 hang 无输出时
        由底层抛 socket.timeout 触发重试。
        """
        max_retries = self.retries if retries is None else max(1, retries)
        safe_cmd = _sanitize_command(command=command)
        start = time.time()
        prefix = self._log_prefix
        logger.info("%sSSH exec start: ip=%s timeout=%s cmd=%s", prefix, self.ip, timeout, safe_cmd[:600])

        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                if timeout is not None:
                    stdin, stdout, stderr = self.exec_command(command, timeout=timeout)
                    stdout.channel.settimeout(timeout)
                else:
                    stdin, stdout, stderr = self.exec_command(command)
                try:
                    exit_status = stdout.channel.recv_exit_status()
                except Exception as e:
                    raise RetryableSshError(f"命令执行等待失败: {str(e)}")
                out = stdout.read().decode('utf-8', errors='replace').strip()
                if exit_status != 0:
                    err = stderr.read().decode('utf-8', errors='replace').strip()
                    raise SSHClientError(
                        f"命令失败(exit={exit_status}): {safe_cmd[:400]}  stdout: {out[-4000:]}  stderr: {err[-4000:]}"
                    )
                elapsed_ms = int((time.time() - start) * 1000)
                logger.info(
                    "%sSSH exec done: exit=0 elapsed_ms=%s cmd=%s stdout_tail=%s",
                    prefix, elapsed_ms, safe_cmd[:600], out[-500:],
                )
                return out
            except SSHClientError as e:
                if not isinstance(e, RetryableSshError):
                    elapsed_ms = int((time.time() - start) * 1000)
                    logger.error("%sSSH exec failed: elapsed_ms=%s cmd=%s err=%s", prefix, elapsed_ms, safe_cmd[:600], str(e))
                    raise
                last_err = e
            except Exception as e:
                if not self._is_retryable_exception(e):
                    elapsed_ms = int((time.time() - start) * 1000)
                    logger.error("%sSSH exec failed: elapsed_ms=%s cmd=%s err=%s", prefix, elapsed_ms, safe_cmd[:600], str(e))
                    raise SSHClientError(f"命令执行失败: {str(e)}")
                last_err = RetryableSshError(f"命令执行网络异常: {str(e)}")

            if attempt < max_retries:
                logger.warning(
                    "%sSSH execute retry: attempt=%s/%s ip=%s cmd=%s err=%s",
                    prefix, attempt, max_retries, self.ip, safe_cmd[:200], str(last_err),
                )
                self._reconnect()
                if self.retry_interval_sec > 0:
                    time.sleep(self.retry_interval_sec)

        elapsed_ms = int((time.time() - start) * 1000)
        final_err = SSHClientError(f"命令重试失败（已重试{max_retries}次）：{str(last_err)}")
        logger.error("%sSSH exec failed: elapsed_ms=%s cmd=%s err=%s", prefix, elapsed_ms, safe_cmd[:600], str(final_err))
        raise final_err

    def execute_ignore_error(self, command: str) -> str:
        """执行命令并忽略非零退出码，只返回 stdout。"""
        stdin, stdout, stderr = self.exec_command(command)
        stdout.channel.recv_exit_status()
        return stdout.read().decode('utf-8', errors='replace').strip()

    def write_file(self, remote_dir: str, remote_path: str, content: str, retries: int = 2) -> None:
        """创建远程目录并写入文件，SFTP 异常自动重建连接重试。"""
        self.execute(command=f'mkdir -p {remote_dir}')
        max_retries = max(1, retries)
        last_err = None
        prefix = self._log_prefix
        for attempt in range(1, max_retries + 1):
            try:
                sftp = self.open_sftp()
                try:
                    with sftp.file(remote_path, 'w') as f:
                        f.write(content)
                    return
                finally:
                    sftp.close()
            except Exception as e:
                last_err = e
                if not self._is_retryable_exception(e) or attempt >= max_retries:
                    raise SSHClientError(f"SFTP 写文件失败: {remote_path} err={str(e)}")
                logger.warning(
                    "%sSFTP write retry: attempt=%s/%s path=%s err=%s",
                    prefix, attempt, max_retries, remote_path, str(e),
                )
                self._reconnect()
                if self.retry_interval_sec > 0:
                    time.sleep(self.retry_interval_sec)
        # 理论上不会到达（循环内已 return 或 raise），兜底抛出
        raise SSHClientError(f"SFTP 写文件失败: {remote_path} err={str(last_err)}")

    def write_root_owned_file(self, remote_path: str, content: str) -> None:
        """写入 root 拥有文件（先写 /tmp 再 sudo mv）。"""
        tmp = f'/tmp/_ai_task_deploy_{uuid.uuid4().hex}'
        self.write_file(remote_dir='/tmp', remote_path=tmp, content=content)
        self.execute(
            command=(
                f'sudo mv {tmp} {remote_path} && '
                f'sudo chmod 644 {remote_path} && sudo chown root:root {remote_path}'
            )
        )

    def _ensure_connected(self):
        if not self._client:
            self.connect()

    def _reconnect(self):
        self.close()
        self.connect()

    @staticmethod
    def _is_retryable_exception(exc: Exception) -> bool:
        """仅对网络/连接类异常判定为可重试。

        原先把整个 OSError 都纳入过宽，会把应用层磁盘/文件错误误判为可重试。
        """
        try:
            import paramiko
            retryable_types = (
                socket.timeout,
                TimeoutError,
                EOFError,
                ConnectionError,
                paramiko.SSHException,
            )
            return isinstance(exc, retryable_types)
        except Exception:
            return isinstance(exc, (socket.timeout, TimeoutError, EOFError, ConnectionError))

