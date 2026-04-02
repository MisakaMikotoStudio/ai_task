#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端心跳记录数据访问对象
"""

from datetime import datetime, timezone
from typing import Optional

from .connection import get_db_session
from .models import ClientHeartbeat

def get_heartbeat(user_id: int, client_id: int) -> Optional[ClientHeartbeat]:
    """获取用户客户端的心跳记录"""
    with get_db_session() as session:
        return session.query(ClientHeartbeat).filter(
            ClientHeartbeat.user_id == user_id,
            ClientHeartbeat.client_id == client_id
        ).first()

def add_heartbeat(user_id: int, client_id: int, instance_uuid: str) -> bool:
    """添加用户客户端的心跳记录"""
    with get_db_session() as session:
        heartbeat = ClientHeartbeat(
            user_id=user_id,
            client_id=client_id,
            instance_uuid=instance_uuid,
            last_sync_at=datetime.now(timezone.utc)
        )
        session.add(heartbeat)
        session.flush()
        return heartbeat

def update_heartbeat(user_id: int, client_id: int, instance_uuid: str) -> bool:
    """更新用户客户端的心跳记录"""
    with get_db_session() as session:
        affected = session.query(ClientHeartbeat).filter(
            ClientHeartbeat.user_id == user_id,
            ClientHeartbeat.client_id == client_id
        ).update({
            ClientHeartbeat.instance_uuid: instance_uuid,
            ClientHeartbeat.last_sync_at: datetime.now(timezone.utc)
        })
        session.flush()
        return affected > 0

def get_heartbeats_by_user(user_id: int, client_id: Optional[int] = None) -> list:
    """
    获取用户客户端的心跳记录

    Args:
        user_id: 用户ID
        client_id: 若指定则只返回该客户端的心跳（0 或 1 条）；不传则返回该用户下全部

    Returns:
        心跳记录列表
    """
    with get_db_session() as session:
        q = session.query(ClientHeartbeat).filter(ClientHeartbeat.user_id == user_id)
        if client_id is not None:
            q = q.filter(ClientHeartbeat.client_id == client_id)
        heartbeats = q.all()
        return [hb.to_dict() for hb in heartbeats]
