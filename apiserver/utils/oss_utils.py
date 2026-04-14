#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OSS 底层工具 —— 腾讯云 COS 对象存储纯 SDK 操作，不依赖业务模型

功能：
- COS 客户端构建
- 对象上传 / 下载
- 预签名 URL 生成
- STS 临时凭证获取
- 文件扩展名解析
"""

import logging
import os
from typing import Dict

from config_model import OssConfig

logger = logging.getLogger(__name__)


def build_cos_client(config: OssConfig):
    """
    构建腾讯云 COS 客户端（复用配置，避免在业务层重复创建）

    Args:
        config: OSS 配置

    Returns:
        CosS3Client 实例
    """
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise RuntimeError("请安装 cos-python-sdk-v5: pip install cos-python-sdk-v5")

    cos_config = CosConfig(Region=config.region, SecretId=config.secret_id, SecretKey=config.secret_key)
    return CosS3Client(cos_config)


def upload_object(
    config: OssConfig,
    object_key: str,
    file_data: bytes,
    content_type: str,
    acl: str = '',
) -> None:
    """
    上传对象到 COS

    Args:
        config: OSS 配置
        object_key: COS 对象 Key
        file_data: 文件二进制数据
        content_type: MIME 类型
        acl: 访问控制（'public-read' / 空字符串继承桶默认）
    """
    client = build_cos_client(config=config)

    put_kwargs = {
        'Bucket': config.bucket,
        'Body': file_data,
        'Key': object_key,
        'ContentType': content_type,
    }
    if acl:
        put_kwargs['ACL'] = acl

    client.put_object(**put_kwargs)

    if acl:
        client.put_object_acl(
            Bucket=config.bucket,
            Key=object_key,
            ACL=acl,
        )


def download_object(config: OssConfig, object_key: str, local_path: str) -> None:
    """
    从 COS 下载对象到本地

    Args:
        config: OSS 配置
        object_key: COS 对象 Key
        local_path: 本地保存路径
    """
    client = build_cos_client(config=config)
    response = client.get_object(Bucket=config.bucket, Key=object_key)
    file_content = response['Body'].get_raw_stream().read()
    with open(local_path, 'wb') as f:
        f.write(file_content)


def generate_presigned_url_raw(config: OssConfig, object_key: str, expired: int = 600) -> str:
    """
    生成 COS 对象的预签名下载 URL

    Args:
        config: OSS 配置
        object_key: COS 对象 Key
        expired: URL 有效期（秒）

    Returns:
        预签名下载 URL
    """
    client = build_cos_client(config=config)
    return client.get_presigned_url(
        Method='GET',
        Bucket=config.bucket,
        Key=object_key,
        Expired=expired,
    )


def get_sts_credentials_raw(config: OssConfig, policy: Dict, duration_seconds: int = 1800) -> Dict:
    """
    获取 STS 临时凭证（纯 SDK 调用）

    Args:
        config: OSS 配置
        policy: IAM 策略字典
        duration_seconds: 凭证有效期（秒）

    Returns:
        STS 响应原始数据
    """
    try:
        from sts.sts import Sts
    except ImportError:
        raise RuntimeError("请安装 qcloud-python-sts: pip install qcloud-python-sts")

    sts_config = {
        'secret_id': config.secret_id,
        'secret_key': config.secret_key,
        'duration_seconds': duration_seconds,
        'bucket': config.bucket,
        'region': config.region,
        'allow_prefix': '*',
        'policy': policy,
    }

    sts = Sts(sts_config)
    return sts.get_credential()


def get_extension(filename: str, content_type: str) -> str:
    """根据文件名或 content_type 获取扩展名"""
    ext_map = {
        'image/jpeg': '.jpg',
        'image/png': '.png',
        'image/gif': '.gif',
        'image/webp': '.webp',
    }
    _, ext = os.path.splitext(filename)
    if ext and ext.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        return ext.lower()
    return ext_map.get(content_type, '.jpg')
