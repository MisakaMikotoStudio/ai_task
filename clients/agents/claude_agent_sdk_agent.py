#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ClaudeCodeAgent - 基于 claude-agent-sdk 的 Agent 封装
"""

import json
import logging
from typing import Any, Callable, Optional, Tuple

import anyio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class ClaudeAgentSdkAgent(BaseAgent):
    """Claude Agent SDK Agent，通过 claude-agent-sdk 调用"""
    def __init__(self, name: str = "claude sdk"):
        super().__init__(name=name)

    def run_prompt(
        self,
        trace_id: str,
        cwd: str,
        prompt: str,
        timeout: Optional[int] = 1800,
        session_id: Optional[str] = None,
        popen_factory: Optional[Callable[..., Any]] = None,
        process_cleanup: Optional[Callable[[Any], None]] = None,
        stop_event: Optional[Any] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError(f"[{trace_id}] [{self.name}] 收到停止信号，取消执行")
        return anyio.run(self._async_execute, trace_id, cwd, prompt, timeout, session_id)

    async def _async_execute(
        self,
        trace_id: str,
        cwd: str,
        prompt: str,
        timeout: int,
        session_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        stderr_lines: list[str] = []

        def _stderr_callback(line: str) -> None:
            stderr_lines.append(line)
            logger.debug(f"[{trace_id}] [CLI stderr] {line}")

        options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode="bypassPermissions",
            stderr=_stderr_callback,
        )
        if session_id:
            options.resume = session_id

        output_parts = []
        with anyio.fail_after(timeout):
            logger.info(
                f"[{trace_id}] [{self.name}] query_kwargs="
                f"{json.dumps({'prompt': prompt, 'options': self._safe_options_for_log(options)}, ensure_ascii=False, default=str)}"
            )
            try:
                session_id = await self._consume_query(
                    trace_id, prompt, options, output_parts, session_id,
                )
            except Exception as exc:
                if session_id and options.resume:
                    logger.warning(
                        f"[{trace_id}] query with resume={session_id} failed ({exc}), "
                        f"stderr={''.join(stderr_lines)!r}. Retrying without resume..."
                    )
                    stderr_lines.clear()
                    options.resume = None
                    session_id = await self._consume_query(
                        trace_id, prompt, options, output_parts, session_id=None,
                    )
                else:
                    logger.error(
                        f"[{trace_id}] query failed: {exc}, stderr={''.join(stderr_lines)!r}"
                    )
                    raise

        final_output = "\n".join(output_parts).strip()
        logger.info(f"[{trace_id}] {self.name} 调用完毕, session_id: {session_id},"
                    f"reply:\n***************\n{final_output}\n***************\n")
        return final_output, session_id

    async def _consume_query(
        self,
        trace_id: str,
        prompt: str,
        options: ClaudeAgentOptions,
        output_parts: list,
        session_id: Optional[str],
    ) -> Optional[str]:
        """Run query() and consume all messages; return the session_id from ResultMessage."""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                self._log_assistant_message(trace_id, message, output_parts)
            elif isinstance(message, UserMessage):
                self._log_user_message(trace_id, message)
            elif isinstance(message, SystemMessage):
                logger.info(f"[{trace_id}] [系统] {message.data}")
            elif isinstance(message, ResultMessage):
                self._log_result_message(trace_id, message)
                session_id = message.session_id
            else:
                logger.info(
                    f"[{trace_id}] [未知消息类型] {type(message).__name__}: {message!r}"
                )
        return session_id

    def _log_assistant_message(self, trace_id: str, message: AssistantMessage, output_parts: list) -> None:
        for block in message.content:
            if isinstance(block, TextBlock):
                logger.info(f"[{trace_id}] [文本] {block.text}")
                output_parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                try:
                    input_str = json.dumps(block.input, ensure_ascii=False)
                except Exception:
                    input_str = str(block.input)
                resolved_tool = self._resolve_tool_name(block.name, block.input)
                tool_id = getattr(block, "id", None)
                tool_meta = f"{resolved_tool}(id={tool_id})" if tool_id else resolved_tool
                logger.info(f"[{trace_id}] [工具调用] {tool_meta} | 参数: {input_str}")
            else:
                # ThinkingBlock 或其他未知 block 类型
                block_type = type(block).__name__
                block_repr = getattr(block, "thinking", None) or getattr(block, "text", None) or str(block)
                logger.info(f"[{trace_id}] [{block_type}] {block_repr}")

    def _log_user_message(self, trace_id: str, message: UserMessage) -> None:
        content = message.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    result_text = block.content
                    if isinstance(result_text, list):
                        result_text = " | ".join(
                            b.text if isinstance(b, TextBlock) else str(b)
                            for b in result_text
                        )
                    logger.info(f"[{trace_id}] [工具结果] id={block.tool_use_id} | {result_text}")
                elif isinstance(block, TextBlock):
                    logger.info(f"[{trace_id}] [用户消息] {block.text}")
                else:
                    logger.info(f"[{trace_id}] [用户消息-其他block] {block!r}")
        elif isinstance(content, str):
            logger.info(f"[{trace_id}] [用户消息] {content}")
        else:
            logger.info(
                f"[{trace_id}] [用户消息-未知类型] type={type(content).__name__}, value={content!r}"
            )

    def _log_result_message(self, trace_id: str, message: ResultMessage) -> None:
        status = "失败" if message.is_error else "成功"
        cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "N/A"
        duration = f"{message.duration_ms}ms" if message.duration_ms else "N/A"
        logger.info(f"[{trace_id}] [执行完成] 状态={status} | 费用={cost} | 耗时={duration}")

    @staticmethod
    def _safe_options_for_log(options: ClaudeAgentOptions) -> object:
        """
        ClaudeAgentOptions 可能不是 JSON 可序列化对象。
        这里尽量提取可序列化字段用于日志，失败则降级为字符串。
        """
        try:
            # Most SDK options objects are simple dataclasses / have __dict__
            if hasattr(options, "__dict__"):
                return vars(options)
        except Exception:
            pass
        try:
            # Best-effort: some SDKs provide dict/serialize helpers.
            if hasattr(options, "to_dict"):
                return options.to_dict()  # type: ignore[attr-defined]
        except Exception:
            pass
        return str(options)

    @staticmethod
    def _resolve_tool_name(tool_name: str, tool_input: object) -> str:
        """将通用工具名补充为更具体的工具标识（如 Agent/Explore）。"""
        if not isinstance(tool_input, dict):
            return tool_name

        subagent_type = (
            tool_input.get("subagent_type")
            or tool_input.get("subagentType")
            or tool_input.get("agent_type")
        )
        if subagent_type:
            return f"{tool_name}/{subagent_type}"
        return tool_name