#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OSS 服务 - 向后兼容入口，实际逻辑统一在 oss_utils
"""

from service.oss_utils import (  # noqa: F401
    upload_image,
    upload_chat_image,
    download_chat_image,
    download_image_to_file,
)
