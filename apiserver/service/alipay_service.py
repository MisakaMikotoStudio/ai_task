#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
支付宝服务层 —— 业务逻辑（支付链接生成、通知验签、退款）

底层签名/加密/OpenAPI 调用委托给 utils.alipay_utils
"""

import json
import logging
import time
import urllib.parse
from typing import Dict

from config_model import AlipayConfig
from dao.models import Order, Product
from utils.alipay_utils import (
    SANDBOX_GATEWAY,
    aes_decrypt,
    aes_encrypt,
    build_sign_string,
    call_openapi,
    load_private_key,
    load_public_key,
    rsa2_sign,
    rsa2_verify,
)

logger = logging.getLogger(__name__)


def build_pay_url(config: AlipayConfig, product: Product, order: Order, device: str) -> str:
    """
    生成支付宝支付 URL
    - device='pc': alipay.trade.page.pay（电脑网站支付）
    - device='mobile': alipay.trade.wap.pay（手机网站支付）
    返回可直接在浏览器打开的 URL
    """
    if device == 'mobile':
        method = 'alipay.trade.wap.pay'
        biz_content = {
            'out_trade_no': order.out_trade_no,
            'subject': product.title,
            'total_amount': f'{float(order.amount):.2f}',
            'product_code': 'QUICK_WAP_WAY',
        }
    else:
        method = 'alipay.trade.page.pay'
        biz_content = {
            'out_trade_no': order.out_trade_no,
            'subject': product.title,
            'total_amount': f'{float(order.amount):.2f}',
            'product_code': 'FAST_INSTANT_TRADE_PAY',
        }

    gateway = SANDBOX_GATEWAY if config.sandbox else config.gateway

    biz_content_json = json.dumps(biz_content, ensure_ascii=False)

    params = {
        'app_id': config.app_id,
        'method': method,
        'format': 'JSON',
        'charset': 'utf-8',
        'sign_type': 'RSA2',
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'version': '1.0',
        'notify_url': config.notify_url,
    }
    if config.return_url:
        params['return_url'] = config.return_url

    # 若配置了 AES 加密密钥，则对 biz_content 进行加密
    if config.app_encrypt_key:
        params['biz_content'] = aes_encrypt(biz_content_json, config.app_encrypt_key)
        params['encrypt_type'] = 'AES'
        logger.debug("biz_content 已加密（encrypt_type=AES）")
    else:
        params['biz_content'] = biz_content_json

    private_key = load_private_key(config.app_private_key)
    sign_string = build_sign_string(params)
    params['sign'] = rsa2_sign(sign_string, private_key)

    return f"{gateway}?{urllib.parse.urlencode(params)}"


def verify_notify(config: AlipayConfig, post_data: Dict) -> bool:
    """
    验证支付宝异步通知签名
    post_data 为支付宝 POST 过来的所有参数
    """
    sign = post_data.get('sign', '')
    if not sign:
        return False

    sign_string = build_sign_string(post_data, exclude_sign_type=True)

    try:
        public_key = load_public_key(config.alipay_public_key)
        return rsa2_verify(sign_string, sign, public_key)
    except Exception as e:
        logger.exception("支付宝验签异常: %s", e)
        return False


def decrypt_response_content(config: AlipayConfig, encrypted_content: str) -> Dict:
    """
    解密支付宝接口加密响应内容

    :param config: 支付宝配置（需包含 app_encrypt_key）
    :param encrypted_content: 支付宝响应中的加密字符串（Base64）
    :return: 解密后的 JSON 数据（dict）
    :raises ValueError: 未配置加密密钥时抛出
    """
    if not config.app_encrypt_key:
        raise ValueError("app_encrypt_key 未配置，无法解密支付宝响应内容")

    try:
        plaintext = aes_decrypt(
            ciphertext_b64=encrypted_content,
            key_b64=config.app_encrypt_key,
        )
        logger.debug("支付宝响应内容解密成功，长度=%d", len(plaintext))
        return json.loads(plaintext)
    except Exception as e:
        logger.exception("支付宝响应内容解密失败: %s", e)
        raise


def refund(config: AlipayConfig, out_trade_no: str, amount: float, reason: str = "") -> Dict:
    """
    调用 alipay.trade.refund 执行原路退款。
    返回支付宝接口的退款响应节点。
    """
    if not out_trade_no:
        raise ValueError("out_trade_no 不能为空")

    if amount <= 0:
        raise ValueError("refund_amount 必须大于 0")

    biz_content = {
        'out_trade_no': out_trade_no,
        'refund_amount': f'{float(amount):.2f}',
    }
    if reason:
        biz_content['refund_reason'] = reason

    return call_openapi(
        config=config,
        method='alipay.trade.refund',
        biz_content=biz_content,
    )
