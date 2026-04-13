#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OSS 工具模块 - 腾讯云 COS 对象存储操作（客户端侧）
支持 STS 临时凭证（带 session_token）
"""

import logging

from config.config_model import OssConfig

logger = logging.getLogger(__name__)


def download_image_to_file(config: OssConfig, oss_path: str, local_path: str):
    """
    通过 COS SDK 直接下载图片到本地文件。
    支持 STS 临时凭证（自动传入 Token）。

    Args:
        config: OSS 配置（包含 secret_id, secret_key, session_token, region, bucket）
        oss_path: COS 上的对象 Key
        local_path: 本地保存路径
    """
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise RuntimeError("请安装 cos-python-sdk-v5: pip install cos-python-sdk-v5")

    cos_config_kwargs = {
        'Region': config.region,
        'SecretId': config.secret_id,
        'SecretKey': config.secret_key,
    }
    # STS 临时凭证需要传入 Token
    if config.session_token:
        cos_config_kwargs['Token'] = config.session_token

    cos_config = CosConfig(**cos_config_kwargs)
    client = CosS3Client(cos_config)
    response = client.get_object(
        Bucket=config.bucket,
        Key=oss_path,
    )
    file_content = response['Body'].get_raw_stream().read()
    with open(local_path, 'wb') as f:
        f.write(file_content)
