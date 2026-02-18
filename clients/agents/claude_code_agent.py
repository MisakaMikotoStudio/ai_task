#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ClaudeCodeAgent - 基于 claude-agent-sdk 的 Agent 封装
"""

import json
import logging
from typing import Tuple

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


class ClaudeCodeAgent(BaseAgent):
    """Claude Code Agent，通过 claude-agent-sdk 调用"""

    def __init__(self):
        super().__init__(name="Claude Code", timeout=1800)

    def _execute_prompt(self, trace_id: str, cwd: str, prompt: str, timeout: int) -> Tuple[bool, str]:
        return anyio.run(self._async_execute, trace_id, cwd, prompt, timeout)

    async def _async_execute(self, trace_id: str, cwd: str, prompt: str, timeout: int) -> Tuple[bool, str]:
        options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode="bypassPermissions",
        )

        output_parts = []
        is_error = False

        try:
            with anyio.fail_after(timeout):
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        self._log_assistant_message(trace_id, message, output_parts)
                    elif isinstance(message, UserMessage):
                        self._log_user_message(trace_id, message)
                    elif isinstance(message, SystemMessage):
                        logger.info(f"[{trace_id}] [系统] {message.data}")
                    elif isinstance(message, ResultMessage):
                        self._log_result_message(trace_id, message)
                        is_error = message.is_error
        except TimeoutError:
            error_msg = f"[{trace_id}] Agent 执行超时 (timeout={timeout}s)"
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"[{trace_id}] Agent 执行异常: {type(e).__name__}: {e}"
            logger.error(error_msg)
            return False, error_msg

        final_output = "\n".join(output_parts).strip()
        if is_error:
            return False, final_output or "Agent 执行失败"
        return True, final_output

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
                logger.info(f"[{trace_id}] [工具调用] {block.name} | 参数: {input_str}")
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
        elif isinstance(content, str):
            logger.info(f"[{trace_id}] [用户消息] {content}")

    def _log_result_message(self, trace_id: str, message: ResultMessage) -> None:
        status = "失败" if message.is_error else "成功"
        cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "N/A"
        duration = f"{message.duration_ms}ms" if message.duration_ms else "N/A"
        logger.info(f"[{trace_id}] [执行完成] 状态={status} | 费用={cost} | 耗时={duration}")
