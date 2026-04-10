#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
资源数据访问对象 - 管理员专用资源表 CRUD
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from .connection import get_db_session
from .models import Resource

logger = logging.getLogger(__name__)


def create_resource(
    type: str,
    source: str,
    envs: List[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Resource:
    """
    创建资源

    Args:
        type: 资源类型（如 mysql）
        source: 资源来源（如 aliyun）
        envs: 可用环境列表（如 ["test", "prod"]）
        extra: 补充详细信息

    Returns:
        新创建的 Resource 对象
    """
    with get_db_session() as session:
        resource = Resource(
            type=type,
            source=source,
            envs=envs,
            extra=extra,
        )
        session.add(resource)
        session.flush()
        return resource


def get_resource_by_id(resource_id: int) -> Optional[Resource]:
    """
    根据 ID 获取资源（不限上下架状态）

    Args:
        resource_id: 资源 ID

    Returns:
        Resource 对象或 None
    """
    with get_db_session() as session:
        return session.query(Resource).filter(
            Resource.id == resource_id,
        ).first()


def list_resources(include_offline: bool = True) -> List[Resource]:
    """
    获取资源列表

    Args:
        include_offline: 是否包含已下架资源

    Returns:
        资源列表
    """
    with get_db_session() as session:
        query = session.query(Resource)
        if not include_offline:
            query = query.filter(Resource.deleted_at.is_(None))
        return query.order_by(Resource.id.desc()).all()


def update_resource(
    resource_id: int,
    type: Optional[str] = None,
    source: Optional[str] = None,
    envs: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Resource]:
    """
    更新资源信息

    Args:
        resource_id: 资源 ID
        type: 资源类型
        source: 资源来源
        envs: 可用环境列表
        extra: 补充详细信息

    Returns:
        更新后的 Resource 对象，不存在则返回 None
    """
    with get_db_session() as session:
        resource = session.query(Resource).filter(
            Resource.id == resource_id,
        ).first()
        if not resource:
            return None
        if type is not None:
            resource.type = type
        if source is not None:
            resource.source = source
        if envs is not None:
            resource.envs = envs
        if extra is not None:
            resource.extra = extra
        return resource


def offline_resource(resource_id: int) -> bool:
    """
    下架资源（设置 deleted_at）

    Args:
        resource_id: 资源 ID

    Returns:
        是否成功
    """
    with get_db_session() as session:
        affected = session.query(Resource).filter(
            Resource.id == resource_id,
            Resource.deleted_at.is_(None),
        ).update({Resource.deleted_at: datetime.now(timezone.utc)})
        return affected > 0


def online_resource(resource_id: int) -> bool:
    """
    上架资源（清除 deleted_at）

    Args:
        resource_id: 资源 ID

    Returns:
        是否成功
    """
    with get_db_session() as session:
        affected = session.query(Resource).filter(
            Resource.id == resource_id,
            Resource.deleted_at.isnot(None),
        ).update({Resource.deleted_at: None})
        return affected > 0


def delete_resource(resource_id: int) -> bool:
    """
    硬删除资源（从数据库中移除）

    Args:
        resource_id: 资源 ID

    Returns:
        是否成功
    """
    with get_db_session() as session:
        affected = session.query(Resource).filter(
            Resource.id == resource_id,
        ).delete()
        return affected > 0


def get_online_resources_by_type_source(
    type: str,
    source: str,
    env: Optional[str] = None,
) -> List[Resource]:
    """
    获取指定类型和来源的上架资源

    Args:
        type: 资源类型
        source: 资源来源
        env: 可选环境过滤（仅返回 envs 中包含该环境的资源）

    Returns:
        资源列表
    """
    with get_db_session() as session:
        query = session.query(Resource).filter(
            Resource.type == type,
            Resource.source == source,
            Resource.deleted_at.is_(None),
        )
        resources = query.order_by(Resource.id.asc()).all()
        if env:
            resources = [r for r in resources if env in (r.envs or [])]
        return resources
