#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端心跳记录数据访问对象
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

from .connection import get_db_session
from .models import ClientHeartbeat


def _ensure_utc_aware(dt: datetime) -> datetime:
    """将数据库读取的时间统一为 UTC aware，避免 naive/aware 混算异常。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def update_heartbeat(
    user_id: int,
    client_id: int,
    instance_uuid: str
) -> Tuple[bool, str]:
    """
    更新心跳记录
 
    规则：
    - 首次心跳：直接创建记录
    - 相同实例 UUID：正常更新心跳时间
    - 不同实例 UUID：
        - 若距离上次心跳不足 3 秒，则认为是并发启动，拒绝接管
        - 若距离上次心跳超过等于 3 秒，则允许新实例接管
 
    Args:
        user_id: 用户ID
        client_id: 客户端ID
        instance_uuid: 客户端实例UUID
    Returns:
        (是否成功, 错误信息)
        - 成功: (True, "")
        - 失败: (False, 错误原因)
    """
    with get_db_session() as session:
        heartbeat = session.query(ClientHeartbeat).filter(
            ClientHeartbeat.user_id == user_id,
            ClientHeartbeat.client_id == client_id
        ).first()
 
        now = datetime.now(timezone.utc)
 
        if not heartbeat:
            # 首次心跳，创建记录
            heartbeat = ClientHeartbeat(
                user_id=user_id,
                client_id=client_id,
                instance_uuid=instance_uuid,
                last_sync_at=now
            )
            session.add(heartbeat)
            return True, ""
 
        # UUID相同，直接更新时间
        if heartbeat.instance_uuid == instance_uuid:
            heartbeat.last_sync_at = now
            return True, ""
 
        # UUID 不同，判断是否满足 3 秒冷却时间
        # 历史脏数据可能出现空时间，直接按可接管处理
        if heartbeat.last_sync_at is None:
            heartbeat.instance_uuid = instance_uuid
            heartbeat.last_sync_at = now
            return True, ""

        # 兼容历史数据里可能存在的 naive datetime
        last_sync_at = _ensure_utc_aware(heartbeat.last_sync_at)
        time_diff = (now - last_sync_at).total_seconds()
        if time_diff < 3:
            # 冷却时间未到，拒绝新实例接管，防止同一客户端短时间内启动多个实例
            return False, "不同实例启动的客户端需要间隔三秒，确保不会同时启动多个实例"
 
        # 冷却时间已过，允许新实例接管
        heartbeat.instance_uuid = instance_uuid
        heartbeat.last_sync_at = now
        return True, ""


def get_heartbeat(user_id: int, client_id: int) -> Optional[ClientHeartbeat]:
    """
    获取心跳记录

    Args:
        user_id: 用户ID
        client_id: 客户端ID

    Returns:
        ClientHeartbeat对象或None
    """
    with get_db_session() as session:
        return session.query(ClientHeartbeat).filter(
            ClientHeartbeat.user_id == user_id,
            ClientHeartbeat.client_id == client_id
        ).first()


def get_latest_instance_uuid(user_id: int, client_id: int) -> Optional[str]:
    """
    获取最新的实例UUID

    Args:
        user_id: 用户ID
        client_id: 客户端ID

    Returns:
        实例UUID或None
    """
    with get_db_session() as session:
        heartbeat = session.query(ClientHeartbeat).filter(
            ClientHeartbeat.user_id == user_id,
            ClientHeartbeat.client_id == client_id
        ).first()
        return heartbeat.instance_uuid if heartbeat else None


def check_instance_uuid_valid(user_id: int, client_id: int, instance_uuid: str) -> bool:
    """
    检查实例UUID是否有效（不再做冷却拦截）

    Args:
        user_id: 用户ID
        client_id: 客户端ID
        instance_uuid: 要检查的实例UUID
    Returns:
        是否有效
    """
    with get_db_session() as session:
        heartbeat = session.query(ClientHeartbeat).filter(
            ClientHeartbeat.user_id == user_id,
            ClientHeartbeat.client_id == client_id
        ).first()

        if heartbeat is None:
            # 没有记录，允许
            return True

        # 去掉冷却逻辑后，统一放行
        return True


def get_heartbeats_by_user(user_id: int) -> list:
    """
    获取用户所有客户端的心跳记录

    Args:
        user_id: 用户ID

    Returns:
        心跳记录列表
    """
    with get_db_session() as session:
        heartbeats = session.query(ClientHeartbeat).filter(
            ClientHeartbeat.user_id == user_id
        ).all()
        return [hb.to_dict() for hb in heartbeats]
