#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OSS 服务 - 腾讯云 COS 对象存储统一操作
"""

import logging
import uuid
import os

from config_model import OssConfig

logger = logging.getLogger(__name__)


def upload_image(config: OssConfig, file_storage) -> str:
    """
    上传图片到腾讯云 COS，返回公开访问 URL
    file_storage: Flask FileStorage 对象
    """
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise RuntimeError("请安装 cos-python-sdk-v5: pip install cos-python-sdk-v5")

    ext = _get_extension(
        filename=file_storage.filename or '',
        content_type=file_storage.content_type,
    )
    object_key = f'product/icon/{uuid.uuid4().hex}{ext}'

    cos_config = CosConfig(
        Region=config.region,
        SecretId=config.secret_id,
        SecretKey=config.secret_key,
    )
    client = CosS3Client(cos_config)

    file_data = file_storage.read()
    client.put_object(
        Bucket=config.bucket,
        Body=file_data,
        Key=object_key,
        ContentType=file_storage.content_type,
        ACL='public-read',
    )
    client.put_object_acl(
        Bucket=config.bucket,
        Key=object_key,
        ACL='public-read',
    )

    url = f'{config.base_url.rstrip("/")}/{object_key}'
    logger.info("OSS 上传成功: %s", url)
    return url


def upload_chat_image(config: OssConfig, file_storage, user_id: int) -> dict:
    """
    上传聊天图片到腾讯云 COS（私有读写），返回 oss_path 和原始文件名。
    file_storage: Flask FileStorage 对象
    user_id: 当前用户 ID（用于构建存储路径，防止越权）
    """
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise RuntimeError("请安装 cos-python-sdk-v5: pip install cos-python-sdk-v5")

    original_filename = file_storage.filename or 'image'
    ext = _get_extension(
        filename=original_filename,
        content_type=file_storage.content_type,
    )
    oss_path = f'chat/images/{user_id}/{uuid.uuid4().hex}{ext}'

    cos_config = CosConfig(
        Region=config.region,
        SecretId=config.secret_id,
        SecretKey=config.secret_key,
    )
    client = CosS3Client(cos_config)

    file_data = file_storage.read()
    # 私有读写：不设置 ACL，继承桶默认权限（私有）
    client.put_object(
        Bucket=config.bucket,
        Body=file_data,
        Key=oss_path,
        ContentType=file_storage.content_type,
    )

    logger.info("OSS 聊天图片上传成功: %s (user_id=%s)", oss_path, user_id)
    return {
        'oss_path': oss_path,
        'filename': original_filename,
    }


def download_chat_image(config: OssConfig, oss_path: str) -> tuple:
    """
    从腾讯云 COS 下载聊天图片，返回 (file_bytes, content_type)。
    """
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise RuntimeError("请安装 cos-python-sdk-v5: pip install cos-python-sdk-v5")

    cos_config = CosConfig(
        Region=config.region,
        SecretId=config.secret_id,
        SecretKey=config.secret_key,
    )
    client = CosS3Client(cos_config)

    response = client.get_object(
        Bucket=config.bucket,
        Key=oss_path,
    )
    file_content = response['Body'].get_raw_stream().read()

    # 根据路径后缀推断 content_type
    content_type = 'application/octet-stream'
    lower_path = oss_path.lower()
    if lower_path.endswith('.jpg') or lower_path.endswith('.jpeg'):
        content_type = 'image/jpeg'
    elif lower_path.endswith('.png'):
        content_type = 'image/png'
    elif lower_path.endswith('.gif'):
        content_type = 'image/gif'
    elif lower_path.endswith('.webp'):
        content_type = 'image/webp'

    logger.info("OSS 聊天图片下载成功: %s, size=%d bytes", oss_path, len(file_content))
    return file_content, content_type


def download_image_to_file(config: OssConfig, oss_path: str, local_path: str):
    """
    从腾讯云 COS 下载文件到本地路径。
    """
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise RuntimeError("请安装 cos-python-sdk-v5: pip install cos-python-sdk-v5")

    cos_config = CosConfig(
        Region=config.region,
        SecretId=config.secret_id,
        SecretKey=config.secret_key,
    )
    client = CosS3Client(cos_config)
    response = client.get_object(
        Bucket=config.bucket,
        Key=oss_path,
    )
    file_content = response['Body'].get_raw_stream().read()
    with open(local_path, 'wb') as f:
        f.write(file_content)


def _get_extension(filename: str, content_type: str) -> str:
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
