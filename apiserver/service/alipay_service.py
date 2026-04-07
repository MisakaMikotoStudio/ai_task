#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
支付宝服务 - 封装 RSA2 签名/验签 和支付链接生成
支持 alipay.trade.page.pay（PC端）和 alipay.trade.wap.pay（移动端）
支持 AES 接口内容加密（获取会员手机号等敏感能力需开启）
"""

import base64
import json
import logging
import time
import urllib.parse
from typing import Dict

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding

from config_model import AlipayConfig
from dao.models import Order, Product

logger = logging.getLogger(__name__)

# 支付宝沙箱网关
_SANDBOX_GATEWAY = 'https://openapi-sandbox.dl.alipaydev.com/gateway.do'

# 支付宝 AES 加密固定 IV（全零）
_AES_IV = b'\x00' * 16


def _load_private_key(pem_content: str) -> RSAPrivateKey:
    """加载 RSA 私钥（自动兼容 PKCS8 / PKCS1，支持 PEM 或纯 Base64）"""
    pem_content = pem_content.strip()

    # 已带 PEM 头尾，直接加载（可兼容 BEGIN PRIVATE KEY / BEGIN RSA PRIVATE KEY）
    if pem_content.startswith('-----'):
        return serialization.load_pem_private_key(
            pem_content.encode('utf-8'), password=None, backend=default_backend()
        )

    # 无 PEM 头尾时，先按 DER 解码后自动识别（PKCS8 / PKCS1）
    der_bytes = base64.b64decode(pem_content)
    return serialization.load_der_private_key(
        der_bytes, password=None, backend=default_backend()
    )


def _load_public_key(pem_content: str) -> RSAPublicKey:
    """加载 RSA 公钥（支付宝公钥，支持带/不带 PEM 头尾）"""
    pem_content = pem_content.strip()
    if not pem_content.startswith('-----'):
        pem_content = (
            '-----BEGIN PUBLIC KEY-----\n'
            + '\n'.join(pem_content[i:i+64] for i in range(0, len(pem_content), 64))
            + '\n-----END PUBLIC KEY-----'
        )
    return serialization.load_pem_public_key(pem_content.encode('utf-8'), backend=default_backend())


def _aes_encrypt(plaintext: str, key_b64: str) -> str:
    """AES-128-CBC/PKCS5Padding 加密，返回 Base64 密文"""
    key = base64.b64decode(key_b64)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode('utf-8')) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(_AES_IV), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode('utf-8')


def _aes_decrypt(ciphertext_b64: str, key_b64: str) -> str:
    """AES-128-CBC/PKCS5Padding 解密，返回明文字符串"""
    key = base64.b64decode(key_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    cipher = Cipher(algorithms.AES(key), modes.CBC(_AES_IV), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
    return plaintext.decode('utf-8')


def _build_sign_string(params: Dict, *, exclude_sign_type: bool = False) -> str:
    """构建签名原串：按 key 字母排序，过滤 sign/空值，按需过滤 sign_type。"""
    excluded_keys = {'sign'}
    if exclude_sign_type:
        excluded_keys.add('sign_type')

    items = sorted(
        ((k, v) for k, v in params.items() if k not in excluded_keys and v is not None and v != ''),
        key=lambda x: x[0]
    )
    return '&'.join(f'{k}={v}' for k, v in items)


def _rsa2_sign(sign_string: str, private_key: RSAPrivateKey) -> str:
    """RSA2（SHA256withRSA）签名，返回 base64 字符串"""
    signature = private_key.sign(
        sign_string.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode('utf-8')


def _rsa2_verify(sign_string: str, signature_b64: str, public_key: RSAPublicKey) -> bool:
    """RSA2 验签"""
    try:
        signature = base64.b64decode(signature_b64)
        public_key.verify(
            signature,
            sign_string.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        return True
    except Exception as e:
        logger.warning("RSA2 verify failed: %s", e)
        return False


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

    gateway = _SANDBOX_GATEWAY if config.sandbox else config.gateway

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
        params['biz_content'] = _aes_encrypt(biz_content_json, config.app_encrypt_key)
        params['encrypt_type'] = 'AES'
        logger.debug("biz_content 已加密（encrypt_type=AES）")
    else:
        params['biz_content'] = biz_content_json

    private_key = _load_private_key(config.app_private_key)
    sign_string = _build_sign_string(params)
    params['sign'] = _rsa2_sign(sign_string, private_key)

    return f"{gateway}?{urllib.parse.urlencode(params)}"


def verify_notify(config: AlipayConfig, post_data: Dict) -> bool:
    """
    验证支付宝异步通知签名
    post_data 为支付宝 POST 过来的所有参数
    """
    sign = post_data.get('sign', '')
    if not sign:
        return False

    # 构建待验证字符串（排除 sign 和 sign_type）
    sign_string = _build_sign_string(post_data, exclude_sign_type=True)

    try:
        public_key = _load_public_key(config.alipay_public_key)
        return _rsa2_verify(sign_string, sign, public_key)
    except Exception as e:
        logger.exception("支付宝验签异常: %s", e)
        return False


def decrypt_response_content(config: AlipayConfig, encrypted_content: str) -> Dict:
    """
    解密支付宝接口加密响应内容（如获取会员手机号等场景）

    支付宝在响应体中返回 AES 加密的密文字符串，调用此函数将其解密为原始 JSON dict。
    必须在 config.app_encrypt_key 已配置的情况下使用。

    :param config: 支付宝配置（需包含 app_encrypt_key）
    :param encrypted_content: 支付宝响应中的加密字符串（Base64）
    :return: 解密后的 JSON 数据（dict）
    :raises ValueError: 未配置加密密钥时抛出
    :raises Exception: 解密或 JSON 解析失败时抛出
    """
    if not config.app_encrypt_key:
        raise ValueError("app_encrypt_key 未配置，无法解密支付宝响应内容")

    try:
        plaintext = _aes_decrypt(
            ciphertext_b64=encrypted_content,
            key_b64=config.app_encrypt_key,
        )
        logger.debug("支付宝响应内容解密成功，长度=%d", len(plaintext))
        return json.loads(plaintext)
    except Exception as e:
        logger.exception("支付宝响应内容解密失败: %s", e)
        raise
