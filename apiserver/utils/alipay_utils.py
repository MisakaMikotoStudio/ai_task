#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
支付宝底层工具 —— RSA2 签名/验签、AES 加解密、OpenAPI 通用调用

纯第三方协议操作，不依赖业务模型（Order、Product 等）。
"""

import base64
import json
import logging
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Dict

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding

from config_model import AlipayConfig

logger = logging.getLogger(__name__)

# 支付宝沙箱网关
SANDBOX_GATEWAY = 'https://openapi-sandbox.dl.alipaydev.com/gateway.do'

# 支付宝 AES 加密固定 IV（全零）
AES_IV = b'\x00' * 16


# ──────────────────────────────────────────────────────
#  RSA 密钥加载
# ──────────────────────────────────────────────────────

def load_private_key(pem_content: str) -> RSAPrivateKey:
    """加载 RSA 私钥（自动兼容 PKCS8 / PKCS1，支持 PEM 或纯 Base64）"""
    pem_content = pem_content.strip()

    if pem_content.startswith('-----'):
        return serialization.load_pem_private_key(
            pem_content.encode('utf-8'), password=None, backend=default_backend()
        )

    der_bytes = base64.b64decode(pem_content)
    return serialization.load_der_private_key(
        der_bytes, password=None, backend=default_backend()
    )


def load_public_key(pem_content: str) -> RSAPublicKey:
    """加载 RSA 公钥（支付宝公钥，支持带/不带 PEM 头尾）"""
    pem_content = pem_content.strip()
    if not pem_content.startswith('-----'):
        pem_content = (
            '-----BEGIN PUBLIC KEY-----\n'
            + '\n'.join(pem_content[i:i+64] for i in range(0, len(pem_content), 64))
            + '\n-----END PUBLIC KEY-----'
        )
    return serialization.load_pem_public_key(pem_content.encode('utf-8'), backend=default_backend())


# ──────────────────────────────────────────────────────
#  AES 加解密
# ──────────────────────────────────────────────────────

def aes_encrypt(plaintext: str, key_b64: str) -> str:
    """AES-128-CBC/PKCS5Padding 加密，返回 Base64 密文"""
    key = base64.b64decode(key_b64)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode('utf-8')) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(AES_IV), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode('utf-8')


def aes_decrypt(ciphertext_b64: str, key_b64: str) -> str:
    """AES-128-CBC/PKCS5Padding 解密，返回明文字符串"""
    key = base64.b64decode(key_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    cipher = Cipher(algorithms.AES(key), modes.CBC(AES_IV), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
    return plaintext.decode('utf-8')


# ──────────────────────────────────────────────────────
#  签名 / 验签
# ──────────────────────────────────────────────────────

def build_sign_string(params: Dict, *, exclude_sign_type: bool = False) -> str:
    """构建签名原串：按 key 字母排序，过滤 sign/空值，按需过滤 sign_type。"""
    excluded_keys = {'sign'}
    if exclude_sign_type:
        excluded_keys.add('sign_type')

    items = sorted(
        ((k, v) for k, v in params.items() if k not in excluded_keys and v is not None and v != ''),
        key=lambda x: x[0]
    )
    return '&'.join(f'{k}={v}' for k, v in items)


def rsa2_sign(sign_string: str, private_key: RSAPrivateKey) -> str:
    """RSA2（SHA256withRSA）签名，返回 base64 字符串"""
    signature = private_key.sign(
        sign_string.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode('utf-8')


def rsa2_verify(sign_string: str, signature_b64: str, public_key: RSAPublicKey) -> bool:
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


# ──────────────────────────────────────────────────────
#  OpenAPI 通用调用
# ──────────────────────────────────────────────────────

def call_openapi(config: AlipayConfig, method: str, biz_content: Dict) -> Dict:
    """
    调用支付宝 OpenAPI 通用方法并返回解包后的 response 节点。

    当配置了 app_encrypt_key 时，会对请求 biz_content 加密，并尝试解密响应内容。

    Args:
        config: 支付宝配置
        method: API 方法名（如 'alipay.trade.refund'）
        biz_content: 业务参数字典

    Returns:
        支付宝响应节点 dict

    Raises:
        RuntimeError: 调用失败
    """
    gateway = SANDBOX_GATEWAY if config.sandbox else config.gateway
    biz_content_json = json.dumps(biz_content, ensure_ascii=False, separators=(',', ':'))

    params = {
        'app_id': config.app_id,
        'method': method,
        'format': 'JSON',
        'charset': 'utf-8',
        'sign_type': 'RSA2',
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'version': '1.0',
    }

    if config.app_encrypt_key:
        params['biz_content'] = aes_encrypt(biz_content_json, config.app_encrypt_key)
        params['encrypt_type'] = 'AES'
    else:
        params['biz_content'] = biz_content_json

    private_key = load_private_key(config.app_private_key)
    sign_string = build_sign_string(params)
    params['sign'] = rsa2_sign(sign_string, private_key)

    body = urllib.parse.urlencode(params).encode('utf-8')
    req = urllib.request.Request(
        gateway,
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_text = resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace') if hasattr(e, 'read') else str(e)
        raise RuntimeError(f"支付宝请求失败(HTTP {e.code}): {detail}") from e
    except Exception as e:
        raise RuntimeError(f"支付宝请求失败: {e}") from e

    try:
        payload = json.loads(resp_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"支付宝响应非 JSON: {resp_text}") from e

    response_key = f"{method.replace('.', '_')}_response"
    response_node = payload.get(response_key)
    if response_node is None:
        raise RuntimeError(f"支付宝响应缺少 {response_key}: {payload}")

    if isinstance(response_node, str):
        if not config.app_encrypt_key:
            raise RuntimeError("支付宝响应为加密字符串，但未配置 app_encrypt_key")
        response_node = json.loads(
            aes_decrypt(ciphertext_b64=response_node, key_b64=config.app_encrypt_key)
        )

    if not isinstance(response_node, dict):
        raise RuntimeError(f"支付宝响应格式异常: {response_node}")

    code = str(response_node.get('code', ''))
    if code != '10000':
        sub_code = response_node.get('sub_code', '')
        sub_msg = response_node.get('sub_msg', '')
        msg = response_node.get('msg', '')
        raise RuntimeError(
            f"支付宝接口调用失败: code={code}, msg={msg}, sub_code={sub_code}, sub_msg={sub_msg}"
        )

    return response_node
