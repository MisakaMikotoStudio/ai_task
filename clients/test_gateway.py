#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网关连通性测试脚本

测试 Claude Agent SDK 能否通过网关正常发起请求。
可在宿主机或容器内运行。

用法：
    python test_gateway.py --gateway http://localhost:8080 --key vk-xxxx
    python test_gateway.py --gateway http://ai-task-gateway:8080 --key vk-xxxx  # 容器内
"""

import argparse
import asyncio
import json
import logging
import sys

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

PASS = "✓"
FAIL = "✗"


# ─────────────────────────────────────────────────────────────
# 测试 1：网关健康检查
# ─────────────────────────────────────────────────────────────

def test_health(gateway_url: str) -> bool:
    """GET /health 应返回 {"status":"ok"}"""
    url = gateway_url.rstrip('/') + '/health'
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200 and resp.json().get('status') == 'ok':
            logger.info(f"{PASS} 健康检查通过  ({url})")
            return True
        logger.error(f"{FAIL} 健康检查失败: status={resp.status_code} body={resp.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"{FAIL} 健康检查异常: {e}")
        logger.error("    请确认网关容器已启动，且地址正确")
        return False


# ─────────────────────────────────────────────────────────────
# 测试 2：虚拟秘钥鉴权（无效秘钥应返回 401）
# ─────────────────────────────────────────────────────────────

def test_invalid_key(gateway_url: str) -> bool:
    """无效秘钥应被拒绝（401），说明鉴权逻辑正常工作"""
    url = gateway_url.rstrip('/') + '/v1/messages'
    try:
        resp = requests.post(
            url,
            headers={'x-api-key': 'vk-invalid-key-for-test', 'content-type': 'application/json'},
            json={'model': 'claude-3-haiku-20240307', 'max_tokens': 10, 'messages': [{'role': 'user', 'content': 'hi'}]},
            timeout=5,
        )
        if resp.status_code == 401:
            logger.info(f"{PASS} 无效秘钥正确返回 401")
            return True
        logger.error(f"{FAIL} 无效秘钥未被拒绝: status={resp.status_code}")
        return False
    except Exception as e:
        logger.error(f"{FAIL} 鉴权测试异常: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# 测试 3：非流式请求（requests 直接发）
# ─────────────────────────────────────────────────────────────

def test_non_streaming(gateway_url: str, virtual_key: str) -> bool:
    """用虚拟秘钥发一条非流式消息，验证代理转发和响应解析"""
    url = gateway_url.rstrip('/') + '/v1/messages'
    payload = {
        'model': 'claude-3-haiku-20240307',
        'max_tokens': 32,
        'messages': [{'role': 'user', 'content': '用一句话回答：1+1=?'}],
    }
    try:
        resp = requests.post(
            url,
            headers={'x-api-key': virtual_key, 'content-type': 'application/json',
                     'anthropic-version': '2023-06-01'},
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"{FAIL} 非流式请求失败: status={resp.status_code} body={resp.text[:400]}")
            return False
        data = resp.json()
        content = data.get('content', [{}])[0].get('text', '')
        usage = data.get('usage', {})
        logger.info(f"{PASS} 非流式请求成功")
        logger.info(f"    回复: {content!r}")
        logger.info(f"    Usage: input={usage.get('input_tokens')} output={usage.get('output_tokens')}")
        return True
    except Exception as e:
        logger.error(f"{FAIL} 非流式请求异常: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# 测试 4：流式请求（SSE）
# ─────────────────────────────────────────────────────────────

def test_streaming(gateway_url: str, virtual_key: str) -> bool:
    """发一条流式消息，验证 SSE 正确转发并且网关能收集到 usage"""
    url = gateway_url.rstrip('/') + '/v1/messages'
    payload = {
        'model': 'claude-3-haiku-20240307',
        'max_tokens': 32,
        'stream': True,
        'messages': [{'role': 'user', 'content': '用一句话回答：天空是什么颜色？'}],
    }
    try:
        collected = []
        input_tokens = output_tokens = 0

        with requests.post(
            url,
            headers={'x-api-key': virtual_key, 'content-type': 'application/json',
                     'anthropic-version': '2023-06-01'},
            json=payload,
            stream=True,
            timeout=60,
        ) as resp:
            if resp.status_code != 200:
                logger.error(f"{FAIL} 流式请求失败: status={resp.status_code} body={resp.text[:400]}")
                return False

            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode('utf-8') if isinstance(raw_line, bytes) else raw_line
                if not line.startswith('data: '):
                    continue
                data_str = line[6:]
                if data_str == '[DONE]':
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                etype = event.get('type', '')
                if etype == 'content_block_delta':
                    collected.append(event.get('delta', {}).get('text', ''))
                elif etype == 'message_start':
                    usage = event.get('message', {}).get('usage', {})
                    input_tokens = usage.get('input_tokens', 0)
                elif etype == 'message_delta':
                    output_tokens = event.get('usage', {}).get('output_tokens', 0)

        reply = ''.join(collected)
        logger.info(f"{PASS} 流式请求成功")
        logger.info(f"    回复: {reply!r}")
        logger.info(f"    Usage: input={input_tokens} output={output_tokens} (网关将异步写入日志)")
        return True
    except Exception as e:
        logger.error(f"{FAIL} 流式请求异常: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# 测试 5：Claude Agent SDK 端到端
# ─────────────────────────────────────────────────────────────

async def test_agent_sdk(gateway_url: str, virtual_key: str) -> bool:
    """用 Claude Agent SDK 通过网关发起一次完整 agent 调用"""
    try:
        import os
        # SDK 通过环境变量获取 API Key 和 Base URL
        os.environ['ANTHROPIC_API_KEY'] = virtual_key
        os.environ['ANTHROPIC_BASE_URL'] = gateway_url

        from claude_agent_sdk import query, ClaudeAgentOptions
    except ImportError:
        logger.warning("⚠ claude_agent_sdk 未安装，跳过 SDK 测试（pip install claude-agent-sdk）")
        return True  # 不视为失败

    try:
        result_text = None
        async for message in query(
            prompt="请用一句话说明你是谁，不要使用任何工具。",
            options=ClaudeAgentOptions(
                allowed_tools=[],          # 不给工具，纯对话
                permission_mode="bypassPermissions",
            ),
        ):
            if hasattr(message, 'result'):
                result_text = message.result
        logger.info(f"{PASS} Claude Agent SDK 端到端测试成功")
        logger.info(f"    结果: {result_text!r}")
        return True
    except Exception as e:
        logger.error(f"{FAIL} Claude Agent SDK 测试失败: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='网关连通性测试')
    parser.add_argument('--gateway', '-g', default='http://localhost:8080',
                        help='网关地址（容器内使用 http://ai-task-gateway:8080）')
    parser.add_argument('--key', '-k', default='',
                        help='虚拟 API Key（vk-xxx），不填则跳过需要秘钥的测试')
    parser.add_argument('--skip-sdk', action='store_true',
                        help='跳过 Claude Agent SDK 端到端测试')
    args = parser.parse_args()

    gateway = args.gateway.rstrip('/')
    virtual_key = args.key

    logger.info(f"{'='*55}")
    logger.info(f"  网关地址: {gateway}")
    logger.info(f"  虚拟秘钥: {virtual_key or '(未提供，跳过代理测试)'}")
    logger.info(f"{'='*55}")

    results = {}

    # 测试 1：健康检查（必须通过）
    results['health'] = test_health(gateway)
    if not results['health']:
        logger.error("健康检查失败，请先确认网关已启动，后续测试中止")
        sys.exit(1)

    # 测试 2：无效秘钥鉴权
    results['auth'] = test_invalid_key(gateway)

    if virtual_key:
        # 测试 3：非流式
        results['non_streaming'] = test_non_streaming(gateway, virtual_key)
        # 测试 4：流式
        results['streaming'] = test_streaming(gateway, virtual_key)
        # 测试 5：Agent SDK
        if not args.skip_sdk:
            results['agent_sdk'] = asyncio.run(test_agent_sdk(gateway, virtual_key))
    else:
        logger.warning("未提供 --key，跳过代理转发测试（测试 3/4/5）")

    # 汇总
    logger.info(f"\n{'='*55}")
    logger.info("测试汇总：")
    all_pass = True
    for name, ok in results.items():
        status = PASS if ok else FAIL
        logger.info(f"  {status} {name}")
        if not ok:
            all_pass = False
    logger.info(f"{'='*55}")

    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
