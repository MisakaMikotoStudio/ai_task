#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OSS 服务层 —— 业务逻辑（路径规则、用户隔离、STS 策略构建）

底层 COS SDK 操作委托给 utils.oss_utils
"""

import logging
import uuid

from config_model import OssConfig
from utils.oss_utils import (
    download_object,
    generate_presigned_url_raw,
    get_extension,
    get_sts_credentials_raw,
    upload_object,
)

logger = logging.getLogger(__name__)


def upload_image(config: OssConfig, file_storage) -> str:
    """
    上传产品图标到 COS，返回公开访问 URL
    file_storage: Flask FileStorage 对象
    """
    ext = get_extension(
        filename=file_storage.filename or '',
        content_type=file_storage.content_type,
    )
    object_key = f'product/icon/{uuid.uuid4().hex}{ext}'

    file_data = file_storage.read()
    upload_object(
        config=config,
        object_key=object_key,
        file_data=file_data,
        content_type=file_storage.content_type,
        acl='public-read',
    )

    url = f'{config.base_url.rstrip("/")}/{object_key}'
    logger.info("OSS 上传成功: %s", url)
    return url


def upload_chat_image(config: OssConfig, file_storage, user_id: int) -> dict:
    """
    上传聊天图片到 COS（私有读写），返回 oss_path 和原始文件名。
    file_storage: Flask FileStorage 对象
    user_id: 当前用户 ID（用于构建存储路径，防止越权）
    """
    original_filename = file_storage.filename or 'image'
    ext = get_extension(
        filename=original_filename,
        content_type=file_storage.content_type,
    )
    oss_path = f'chat/images/{user_id}/{uuid.uuid4().hex}{ext}'

    file_data = file_storage.read()
    upload_object(
        config=config,
        object_key=oss_path,
        file_data=file_data,
        content_type=file_storage.content_type,
    )

    logger.info("OSS 聊天图片上传成功: %s (user_id=%s)", oss_path, user_id)
    return {
        'oss_path': oss_path,
        'filename': original_filename,
    }


def download_image_to_file(config: OssConfig, oss_path: str, local_path: str):
    """从 COS 下载文件到本地路径。"""
    download_object(
        config=config,
        object_key=oss_path,
        local_path=local_path,
    )


def generate_presigned_url(config: OssConfig, oss_path: str, expired: int = 600) -> str:
    """
    生成 COS 对象的预签名下载 URL。

    Args:
        config: OSS 配置
        oss_path: COS 上的对象 Key
        expired: URL 有效期（秒），默认 600（10 分钟）

    Returns:
        预签名下载 URL
    """
    url = generate_presigned_url_raw(
        config=config,
        object_key=oss_path,
        expired=expired,
    )
    logger.info("生成预签名 URL: path=%s, expired=%ds", oss_path, expired)
    return url


def get_sts_temp_credentials(config: OssConfig, user_id: int, duration_seconds: int = 1800) -> dict:
    """
    为指定用户生成 STS 临时凭证，限定只能访问 chat/images/{user_id}/* 路径。

    Args:
        config: OSS 配置
        user_id: 用户 ID，用于限定访问路径
        duration_seconds: 凭证有效期（秒），默认 1800（30 分钟）

    Returns:
        包含临时凭证、region、bucket、allow_prefix 的字典
    """
    allow_prefix = f'chat/images/{user_id}/*'

    # 从 bucket 名称中提取 appid（格式: bucketname-appid）
    bucket_parts = config.bucket.rsplit('-', 1)
    if len(bucket_parts) != 2:
        raise ValueError(f"Bucket 名称格式不正确，期望格式 bucketname-appid: {config.bucket}")
    appid = bucket_parts[1]

    resource = f'qcs::cos:{config.region}:uid/{appid}:{config.bucket}/{allow_prefix}'

    policy = {
        'version': '2.0',
        'statement': [
            {
                'action': [
                    'name/cos:GetObject',
                ],
                'effect': 'allow',
                'resource': [resource],
            },
        ],
    }

    response = get_sts_credentials_raw(
        config=config,
        policy=policy,
        duration_seconds=duration_seconds,
    )

    credentials = response.get('credentials', {})
    return {
        'tmp_secret_id': credentials.get('tmpSecretId', ''),
        'tmp_secret_key': credentials.get('tmpSecretKey', ''),
        'session_token': credentials.get('sessionToken', ''),
        'expired_time': int(response.get('expiredTime', 0)),
        'region': config.region,
        'bucket': config.bucket,
        'allow_prefix': f'chat/images/{user_id}/',
    }
