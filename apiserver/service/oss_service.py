#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OSS 服务 - 腾讯云 COS 对象存储
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
    # 官方：PUT Object 请求头 x-cos-acl: public-read = 公有读私有写；不设或为 default 则对象继承桶权限（控制台常显示「继承权限」）
    # SDK 将 ACL= 映射为 x-cos-acl（cos-python-sdk-v5 cos_comm.maplist）
    client.put_object(
        Bucket=config.bucket,
        Body=file_data,
        Key=object_key,
        ContentType=file_storage.content_type,
        ACL='public-read',
    )
    # 再调 PutObjectACL，避免部分环境下仅上传头未落对象 ACL；若桶开启「阻止通过 ACL 将对象设为公有读」此处会报错，需在控制台关闭对应项或改用桶策略
    client.put_object_acl(
        Bucket=config.bucket,
        Key=object_key,
        ACL='public-read',
    )

    url = f'{config.base_url.rstrip("/")}/{object_key}'
    logger.info("OSS 上传成功: %s", url)
    return url


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
