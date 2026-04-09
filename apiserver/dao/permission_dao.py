#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
权限配置 DAO - 纯数据库操作
"""

import logging
from typing import Optional, List

from .connection import get_session
from .models import PermissionConfig

logger = logging.getLogger(__name__)


def get_configs_by_key(key: str) -> List[PermissionConfig]:
    """按权限 key 查询所有未删除的配置"""
    session = get_session()
    return (session.query(PermissionConfig)
            .filter(
                PermissionConfig.key == key,
                PermissionConfig.deleted_at.is_(None),
            )
            .all())


def get_all_configs() -> List[PermissionConfig]:
    """获取所有未删除的权限配置"""
    session = get_session()
    return (session.query(PermissionConfig)
            .filter(PermissionConfig.deleted_at.is_(None))
            .order_by(PermissionConfig.key, PermissionConfig.product_key)
            .all())


def get_config_by_id(config_id: int) -> Optional[PermissionConfig]:
    """按 ID 查询未删除的权限配置"""
    session = get_session()
    return (session.query(PermissionConfig)
            .filter(
                PermissionConfig.id == config_id,
                PermissionConfig.deleted_at.is_(None),
            )
            .first())


def create_config(key: str, type: str, product_key: str,
                  config_detail: Optional[dict] = None) -> PermissionConfig:
    """创建权限配置"""
    session = get_session()
    config = PermissionConfig(
        key=key,
        type=type,
        product_key=product_key,
        config_detail=config_detail,
    )
    session.add(config)
    session.flush()
    return config


def update_config(config_id: int, key: str, type: str, product_key: str,
                  config_detail: Optional[dict] = None) -> Optional[PermissionConfig]:
    """更新权限配置"""
    session = get_session()
    config = (session.query(PermissionConfig)
              .filter(
                  PermissionConfig.id == config_id,
                  PermissionConfig.deleted_at.is_(None),
              )
              .first())
    if not config:
        return None
    config.key = key
    config.type = type
    config.product_key = product_key
    config.config_detail = config_detail
    session.flush()
    return config


def soft_delete_config(config_id: int) -> bool:
    """软删除权限配置"""
    session = get_session()
    from datetime import datetime, timezone
    rows = (session.query(PermissionConfig)
            .filter(
                PermissionConfig.id == config_id,
                PermissionConfig.deleted_at.is_(None),
            )
            .update({'deleted_at': datetime.now(timezone.utc)}))
    return rows > 0


def check_duplicate(key: str, product_key: str, exclude_id: Optional[int] = None) -> bool:
    """检查同一 key + product_key 是否已存在（排除指定 ID）"""
    session = get_session()
    query = (session.query(PermissionConfig)
             .filter(
                 PermissionConfig.key == key,
                 PermissionConfig.product_key == product_key,
                 PermissionConfig.deleted_at.is_(None),
             ))
    if exclude_id is not None:
        query = query.filter(PermissionConfig.id != exclude_id)
    return query.first() is not None
