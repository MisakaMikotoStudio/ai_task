#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发布记录数据访问对象
"""

import logging
from typing import List

from .connection import get_db_session
from .models import DeployRecord

logger = logging.getLogger(__name__)


def create_deploy_record(user_id: int, client_id: int, env: str, description: str, status: str, detail: dict) -> int:
    """创建发布记录，返回记录 ID"""
    with get_db_session() as session:
        record = DeployRecord(
            user_id=user_id,
            client_id=client_id,
            env=env,
            description=description,
            status=status,
            detail=detail,
        )
        session.add(record)
        session.flush()
        return record.id


def get_deploy_records_by_client(user_id: int, client_id: int) -> List[dict]:
    """获取指定客户端的发布记录列表（按创建时间倒序）"""
    with get_db_session() as session:
        records = session.query(DeployRecord).filter(
            DeployRecord.user_id == user_id,
            DeployRecord.client_id == client_id,
            DeployRecord.deleted_at.is_(None),
        ).order_by(DeployRecord.created_at.desc()).all()
        return [r.to_dict() for r in records]


def cancel_deploy_record(user_id: int, record_id: int) -> bool:
    """取消发布记录，返回是否成功"""
    with get_db_session() as session:
        record = session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
            DeployRecord.user_id == user_id,
            DeployRecord.deleted_at.is_(None),
        ).first()
        if not record:
            return False
        if record.status not in (DeployRecord.STATUS_PENDING, DeployRecord.STATUS_PUBLISHING):
            return False
        record.status = DeployRecord.STATUS_CANCEL
        return True
