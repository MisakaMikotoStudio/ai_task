#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ClaudeCodeCliAgent - 通过命令行子进程调用 claude code cli 的 Agent 封装

使用 `claude -p --output-format stream-json --verbose` 以流式 JSON 获取
agent 每一步的思考、工具调用及工具返回结果，最终从 result 事件提取
session_id 和完整回复。
"""

import json
import logging
import subprocess
import threading
from typing import Optional, Tuple

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

MAX_LOG_LEN = 500


def _truncate(text: str, max_len: int = MAX_LOG_LEN) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... ({len(text)} chars total)"


def _format_usage(usage: dict | None) -> str:
    if not usage:
        return ""
    parts = []
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        if key in usage:
            short = key.replace("_tokens", "").replace("input", "in").replace("output", "out")
            parts.append(f"{short}={usage[key]}")
    return ", ".join(parts)


class ClaudeCodeCliAgent(BaseAgent):
    """Claude Code CLI Agent，通过子进程调用 `claude -p` 命令行"""

    def __init__(self, name: str = "claude cli"):
        super().__init__(name=name)

    def run_prompt(
        self,
        trace_id: str,
        cwd: str,
        prompt: str,
        timeout: Optional[int] = 1800,
        session_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        cmd = [
            "claude", "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if session_id:
            cmd.extend(["--resume", session_id])

        logger.info(
            f"[{trace_id}] [{self.name}] 启动 CLI: claude -p --output-format stream-json --verbose"
            + (f" --resume {session_id}" if session_id else "")
        )

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # prompt 通过 stdin 传入，避免命令行长度限制
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        finally:
            process.kill()
            process.wait()

        # 后台线程读取 stderr，防止管道缓冲区满导致死锁
        stderr_lines: list[str] = []
        stderr_thread = threading.Thread(
            target=lambda: stderr_lines.extend(process.stderr.readlines()),
            daemon=True,
        )
        stderr_thread.start()

        # 超时守护
        timed_out = threading.Event()

        def _on_timeout():
            timed_out.set()
            process.kill()

        timer = threading.Timer(timeout, _on_timeout)
        timer.start()

        result_session_id: Optional[str] = None
        result_text: Optional[str] = None
        is_error = False
        turn_count = 0

        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"[{trace_id}] [{self.name}] 非JSON: {_truncate(line)}")
                    continue

                event_type = event.get("type")

                if event_type == "result":
                    result_session_id = event.get("session_id")
                    is_error = event.get("is_error", False) or event.get("subtype") != "success"
                    result_text = event.get("result")
                    self._log_result(trace_id, event)

                elif event_type == "assistant":
                    turn_count += 1
                    self._log_assistant(trace_id, turn_count, event)

                elif event_type == "user":
                    self._log_user(trace_id, event)

                elif event_type == "system":
                    self._log_system(trace_id, event)

                else:
                    logger.debug(f"[{trace_id}] [{self.name}] 未知事件类型: {event_type} | 原始事件: {_truncate(json.dumps(event, ensure_ascii=False))}")

            process.wait()
            stderr_thread.join(timeout=5)
        finally:
            process.kill()
            process.wait()
            timer.cancel()

        if timed_out.is_set():
            error_msg = f"[{trace_id}] [{self.name}] Agent 执行超时 (timeout={timeout}s)"
            logger.error(error_msg)
            return error_msg, result_session_id

        if process.returncode != 0 and not result_session_id:
            stderr_output = "".join(stderr_lines).strip()
            error_msg = f"[{trace_id}] [{self.name}] CLI 进程异常退出 (code={process.returncode})"
            if stderr_output:
                error_msg += f"\nstderr: {_truncate(stderr_output, 1000)}"
            logger.error(error_msg)
            return error_msg, None

        final_output = (result_text or "").strip()
        logger.info(f"[{trace_id}] [{self.name}] 调用完毕, session_id: {result_session_id},"
                    f"reply:\n***************\n{final_output}\n***************\n")
        return final_output, result_session_id

    # ==================== 日志方法 ====================

    def _log_assistant(self, trace_id: str, turn: int, event: dict) -> None:
        message = event.get("message", {})
        model = message.get("model", "unknown")
        usage_str = _format_usage(message.get("usage") or event.get("usage"))

        header = f"[{trace_id}] [Turn {turn}] model={model}"
        if usage_str:
            header += f" | {usage_str}"
        logger.info(header)

        for block in message.get("content", []):
            btype = block.get("type")
            if btype == "thinking":
                logger.info(f"[{trace_id}]   [思考] {_truncate(block.get('thinking', ''))}")
            elif btype == "text":
                logger.info(f"[{trace_id}]   [文本] {_truncate(block.get('text', ''))}")
            elif btype == "tool_use":
                try:
                    input_str = json.dumps(block.get("input", {}), ensure_ascii=False)
                except Exception:
                    input_str = str(block.get("input"))
                resolved_tool = self._resolve_tool_name(block.get("name"), block.get("input"))
                tool_id = block.get("id")
                tool_meta = f"{resolved_tool}(id={tool_id})" if tool_id else resolved_tool
                logger.info(
                    f"[{trace_id}]   [工具调用] {tool_meta} "
                    f"| 参数: {_truncate(input_str)}"
                )
            else:
                logger.info(f"[{trace_id}]   [{btype}] {_truncate(json.dumps(block, ensure_ascii=False))}")

    def _log_user(self, trace_id: str, event: dict) -> None:
        message = event.get("message", {})
        for block in message.get("content", []):
            btype = block.get("type")
            if btype == "tool_result":
                is_err = " [ERROR]" if block.get("is_error") else ""
                content = block.get("content", "(empty)")
                if isinstance(content, list):
                    content = " | ".join(
                        item.get("text", str(item)) if isinstance(item, dict) else str(item)
                        for item in content
                    )
                elif content is None:
                    content = "(empty)"
                logger.info(
                    f"[{trace_id}]   [工具结果]{is_err} id={block.get('tool_use_id')} "
                    f"| {_truncate(str(content))}"
                )
            else:
                logger.info(f"[{trace_id}]   [{btype}] {_truncate(json.dumps(block, ensure_ascii=False))}")

    def _log_system(self, trace_id: str, event: dict) -> None:
        subtype = event.get("subtype", "")
        if subtype == "api_retry":
            logger.warning(
                f"[{trace_id}] [API重试] attempt={event.get('attempt')}/{event.get('max_retries')} "
                f"| error={event.get('error')} | status={event.get('error_status')} "
                f"| retry_in={event.get('retry_delay_ms')}ms"
            )
        else:
            logger.info(f"[{trace_id}] [系统] {subtype}")

    def _log_result(self, trace_id: str, event: dict) -> None:
        status = "成功" if event.get("subtype") == "success" else "失败"
        cost_val = event.get("total_cost_usd")
        cost = f"${cost_val:.6f}" if cost_val is not None else "N/A"
        duration_ms = event.get("duration_ms")
        duration = f"{duration_ms / 1000:.1f}s" if duration_ms else "N/A"
        usage_str = _format_usage(event.get("usage")) or "N/A"

        logger.info(
            f"[{trace_id}] [执行完成] 状态={status} | session_id={event.get('session_id')} | "
            f"轮次={event.get('num_turns', 'N/A')} | 费用={cost} | 耗时={duration} | "
            f"tokens=[{usage_str}] | stop_reason={event.get('stop_reason', 'N/A')}"
        )

    @staticmethod
    def _resolve_tool_name(tool_name: object, tool_input: object) -> str:
        """将通用工具名补充为更具体的工具标识（如 Agent/Explore）。"""
        name = str(tool_name) if tool_name else "unknown"
        if not isinstance(tool_input, dict):
            return name

        subagent_type = (
            tool_input.get("subagent_type")
            or tool_input.get("subagentType")
            or tool_input.get("agent_type")
        )
        if subagent_type:
            return f"{name}/{subagent_type}"
        return name
