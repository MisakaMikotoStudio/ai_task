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


def get_deploy_records_by_client(user_id: int, client_id: int, status: str = None, page: int = 1, page_size: int = 20) -> dict:
    """获取指定客户端的发布记录列表（按创建时间倒序，支持分页和状态筛选）"""
    with get_db_session() as session:
        query = session.query(DeployRecord).filter(
            DeployRecord.user_id == user_id,
            DeployRecord.client_id == client_id,
            DeployRecord.deleted_at.is_(None),
        )
        if status:
            query = query.filter(DeployRecord.status == status)
        total = query.count()
        offset = (page - 1) * page_size
        records = query.order_by(DeployRecord.created_at.desc()).offset(offset).limit(page_size).all()
        return {'records': [r.to_dict() for r in records], 'total': total, 'page': page, 'page_size': page_size}


def get_pending_prod_deploy_records() -> List[DeployRecord]:
    """获取所有生产环境待发布和发布中的记录（跨用户，供调度器使用）"""
    with get_db_session() as session:
        records = session.query(DeployRecord).filter(
            DeployRecord.env == 'prod',
            DeployRecord.status.in_([DeployRecord.STATUS_PENDING, DeployRecord.STATUS_PUBLISHING]),
            DeployRecord.deleted_at.is_(None),
        ).order_by(DeployRecord.client_id.asc(), DeployRecord.created_at.desc()).all()
        return records


def update_deploy_record_status(record_id: int, status: str, detail: dict = None) -> bool:
    """更新发布记录的状态和详情（供调度器使用，不校验 user_id）"""
    with get_db_session() as session:
        update_data = {DeployRecord.status: status}
        if detail is not None:
            update_data[DeployRecord.detail] = detail
        affected = session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
            DeployRecord.deleted_at.is_(None),
        ).update(update_data)
        return affected > 0


def batch_cancel_deploy_records(record_ids: list) -> int:
    """批量将 pending 状态的发布记录标记为 cancel"""
    if not record_ids:
        return 0
    with get_db_session() as session:
        affected = session.query(DeployRecord).filter(
            DeployRecord.id.in_(record_ids),
            DeployRecord.status == DeployRecord.STATUS_PENDING,
            DeployRecord.deleted_at.is_(None),
        ).update({DeployRecord.status: DeployRecord.STATUS_CANCEL}, synchronize_session=False)
        return affected


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


def retry_deploy_record(user_id: int, record_id: int) -> bool:
    """重试发布记录（仅限失败状态），将状态重置为 pending，返回是否成功"""
    with get_db_session() as session:
        record = session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
            DeployRecord.user_id == user_id,
            DeployRecord.deleted_at.is_(None),
        ).first()
        if not record:
            return False
        if record.status != DeployRecord.STATUS_FAILED:
            return False
        record.status = DeployRecord.STATUS_PENDING
        return True
