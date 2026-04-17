#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发布记录数据访问对象
"""

import logging
from typing import List

from .connection import get_db_session
from .models import Client, DeployRecord

logger = logging.getLogger(__name__)


def create_deploy_record(user_id: int, client_id: int, env: str, description: str, status: str, detail: dict, msg_id: int = 0) -> int:
    """创建发布记录，返回记录 ID"""
    with get_db_session() as session:
        record = DeployRecord(
            user_id=user_id,
            client_id=client_id,
            msg_id=msg_id or 0,
            env=env,
            description=description,
            status=status,
            detail=detail,
        )
        session.add(record)
        session.flush()
        return record.id


def get_deploy_records_by_client(user_id: int, client_id: int, status: str = None, env: str = None, msg_id: int = None, page: int = 1, page_size: int = 20) -> dict:
    """获取指定客户端的发布记录列表（按创建时间倒序，支持分页、状态、环境、msg_id 筛选）"""
    return _query_deploy_records(
        user_id=user_id, client_id=client_id, status=status, env=env, msg_id=msg_id,
        page=page, page_size=page_size,
    )


def get_deploy_records_by_user(user_id: int, client_id: int = None, status: str = None, page: int = 1, page_size: int = 20) -> dict:
    """获取指定用户的发布记录列表（可选按 client_id/status 过滤，附带 client_name）"""
    return _query_deploy_records(user_id=user_id, client_id=client_id, status=status, page=page, page_size=page_size)


def _query_deploy_records(user_id: int, client_id: int = None, status: str = None, env: str = None, msg_id: int = None, page: int = 1, page_size: int = 20) -> dict:
    """发布记录通用查询：强制按 user_id 过滤，LEFT JOIN ai_task_clients 带出应用名称。"""
    with get_db_session() as session:
        query = session.query(DeployRecord, Client.name).outerjoin(
            Client, Client.id == DeployRecord.client_id,
        ).filter(
            DeployRecord.user_id == user_id,
            DeployRecord.deleted_at.is_(None),
        )
        if client_id is not None:
            query = query.filter(DeployRecord.client_id == client_id)
        if status:
            query = query.filter(DeployRecord.status == status)
        if env:
            query = query.filter(DeployRecord.env == env)
        if msg_id is not None:
            query = query.filter(DeployRecord.msg_id == msg_id)
        total = query.count()
        offset = (page - 1) * page_size
        rows = query.order_by(DeployRecord.created_at.desc()).offset(offset).limit(page_size).all()
        records = []
        for record, client_name in rows:
            record_dict = record.to_dict()
            record_dict['client_name'] = client_name or ''
            records.append(record_dict)
        return {'records': records, 'total': total, 'page': page, 'page_size': page_size}


def get_latest_deploy_records_by_msg_ids(user_id: int, client_id: int, msg_ids: list) -> dict:
    """
    批量查询指定 msg_id 列表下的最新发布记录。

    Returns:
        {str(msg_id): {env: record_dict, ...}, ...}
        对每个 (msg_id, env) 组合返回创建时间最新的一条记录。
    """
    valid_ids = [int(mid) for mid in msg_ids if mid and int(mid) > 0]
    if not valid_ids:
        return {}
    with get_db_session() as session:
        records = session.query(DeployRecord).filter(
            DeployRecord.user_id == user_id,
            DeployRecord.client_id == client_id,
            DeployRecord.msg_id.in_(valid_ids),
            DeployRecord.deleted_at.is_(None),
        ).order_by(DeployRecord.created_at.desc()).all()

    result = {}
    for record in records:
        key = str(record.msg_id)
        bucket = result.setdefault(key, {})
        # 创建时间倒序遍历，每个 env 只保留首次出现的（即最新）一条
        if record.env not in bucket:
            bucket[record.env] = record.to_dict()
    return result


def get_pending_deploy_records(client_id: int, env: str) -> List[DeployRecord]:
    """获取指定应用待发布和发布中的记录（跨用户，供调度器使用）"""
    with get_db_session() as session:
        records = session.query(DeployRecord).filter(
            DeployRecord.client_id == client_id,
            DeployRecord.env == env,
            DeployRecord.status.in_([DeployRecord.STATUS_PENDING, DeployRecord.STATUS_PUBLISHING]),
            DeployRecord.deleted_at.is_(None),
        ).order_by(DeployRecord.created_at.desc()).all()
        return records


def get_pending_deploy_client_ids() -> List[int]:
    """获取存在待发布/发布中记录的应用 ID 列表"""
    with get_db_session() as session:
        rows = session.query(DeployRecord.client_id).filter(
            DeployRecord.status.in_([DeployRecord.STATUS_PENDING, DeployRecord.STATUS_PUBLISHING]),
            DeployRecord.deleted_at.is_(None),
        ).distinct().all()
        return [row[0] for row in rows]


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
    """重试发布记录（失败或取消状态），将状态重置为 pending，返回是否成功"""
    with get_db_session() as session:
        record = session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
            DeployRecord.user_id == user_id,
            DeployRecord.deleted_at.is_(None),
        ).first()
        if not record:
            return False
        if record.status not in (DeployRecord.STATUS_FAILED, DeployRecord.STATUS_CANCEL):
            return False
        record.status = DeployRecord.STATUS_PENDING
        return True
