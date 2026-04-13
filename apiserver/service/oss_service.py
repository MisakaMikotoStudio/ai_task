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

    url = client.get_presigned_url(
        Method='GET',
        Bucket=config.bucket,
        Key=oss_path,
        Expired=expired,
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
        {
            'tmp_secret_id': str,
            'tmp_secret_key': str,
            'session_token': str,
            'expired_time': int,  # Unix 时间戳
            'region': str,
            'bucket': str,
            'allow_prefix': str,  # 允许访问的路径前缀
        }
    """
    try:
        from sts.sts import Sts
    except ImportError:
        raise RuntimeError("请安装 qcloud-python-sts: pip install qcloud-python-sts")

    allow_prefix = f'chat/images/{user_id}/*'

    # 从 bucket 名称中提取 appid（格式: bucketname-appid）
    bucket_parts = config.bucket.rsplit('-', 1)
    if len(bucket_parts) != 2:
        raise ValueError(f"Bucket 名称格式不正确，期望格式 bucketname-appid: {config.bucket}")
    appid = bucket_parts[1]

    resource = f'qcs::cos:{config.region}:uid/{appid}:{config.bucket}/{allow_prefix}'

    sts_config = {
        'secret_id': config.secret_id,
        'secret_key': config.secret_key,
        'duration_seconds': duration_seconds,
        'bucket': config.bucket,
        'region': config.region,
        'allow_prefix': allow_prefix,
        'policy': {
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
        },
    }

    sts = Sts(sts_config)
    response = sts.get_credential()

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
