#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发布记录数据访问对象
"""

import logging
from typing import List, Optional

from .connection import get_db_session
from .models import DeployRecord

logger = logging.getLogger(__name__)


def create_deploy_record(user_id: int, client_id: int, environment: str, description: str, detail: dict) -> DeployRecord:
    """创建发布记录"""
    with get_db_session() as session:
        record = DeployRecord(
            user_id=user_id,
            client_id=client_id,
            environment=environment,
            description=description,
            status=DeployRecord.STATUS_PENDING,
            detail=detail,
        )
        session.add(record)
        session.flush()
        return record


def get_deploy_records_by_client(user_id: int, client_id: int) -> List[dict]:
    """获取指定应用的发布记录列表（按创建时间倒序）"""
    with get_db_session() as session:
        records = session.query(DeployRecord).filter(
            DeployRecord.user_id == user_id,
            DeployRecord.client_id == client_id,
            DeployRecord.deleted_at.is_(None),
        ).order_by(DeployRecord.created_at.desc()).all()
        return [r.to_dict() for r in records]


def get_deploy_record_by_id(record_id: int, user_id: int) -> Optional[DeployRecord]:
    """根据 ID 获取发布记录"""
    with get_db_session() as session:
        return session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
            DeployRecord.user_id == user_id,
            DeployRecord.deleted_at.is_(None),
        ).first()


def cancel_deploy_record(record_id: int, user_id: int) -> bool:
    """取消发布记录（将状态设为 cancel）"""
    with get_db_session() as session:
        affected = session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
            DeployRecord.user_id == user_id,
            DeployRecord.deleted_at.is_(None),
        ).update({DeployRecord.status: DeployRecord.STATUS_CANCEL})
        return affected > 0
