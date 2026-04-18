#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发布记录数据访问对象
"""

import logging
from typing import List, Optional, Tuple

from .connection import get_db_session
from .models import Client, DeployRecord

logger = logging.getLogger(__name__)


def create_deploy_record(
    user_id: int,
    client_id: int,
    env: str,
    description: str,
    status: str,
    detail: dict,
    msg_id: int = 0,
    task_id: int = 0,
    chat_id: int = 0,
) -> int:
    """创建发布记录，返回记录 ID"""
    with get_db_session() as session:
        record = DeployRecord(
            user_id=user_id,
            client_id=client_id,
            msg_id=msg_id or 0,
            task_id=task_id or 0,
            chat_id=chat_id or 0,
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


def get_deploy_record_by_id(user_id: int, record_id: int) -> "DeployRecord | None":
    """按 ID 获取发布记录（带 user_id 校验、未软删除）。"""
    if not record_id:
        return None
    with get_db_session() as session:
        return session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
            DeployRecord.user_id == user_id,
            DeployRecord.deleted_at.is_(None),
        ).first()


def get_latest_deploy_record_by_msg_env(user_id: int, client_id: int, msg_id: int, env: str) -> "DeployRecord | None":
    """获取指定 (user, client, msg_id, env) 下最新一条未删除的发布记录。"""
    if not msg_id:
        return None
    with get_db_session() as session:
        return session.query(DeployRecord).filter(
            DeployRecord.user_id == user_id,
            DeployRecord.client_id == client_id,
            DeployRecord.msg_id == msg_id,
            DeployRecord.env == env,
            DeployRecord.deleted_at.is_(None),
        ).order_by(DeployRecord.created_at.desc()).first()


def upsert_auto_test_deploy_record(
    user_id: int,
    client_id: int,
    task_id: int,
    chat_id: int,
    msg_id: int,
    description: str,
    detail: dict,
) -> Tuple[int, str]:
    """
    自动测试环境发布记录的 upsert。

    按 (user_id, client_id, task_id, chat_id, msg_id, env='test', 未软删除) 定位现有记录：
    - 不存在：创建一条 pending 记录
    - 已存在且 status == publishing：保持不动（避免打断正在进行的部署）
    - 已存在且其它状态：status 重置为 pending，并覆盖 description/detail

    Returns:
        (record_id, action)，action ∈ {'created', 'reset', 'publishing'}
    """
    with get_db_session() as session:
        existing = session.query(DeployRecord).filter(
            DeployRecord.user_id == user_id,
            DeployRecord.client_id == client_id,
            DeployRecord.task_id == (task_id or 0),
            DeployRecord.chat_id == (chat_id or 0),
            DeployRecord.msg_id == (msg_id or 0),
            DeployRecord.env == 'test',
            DeployRecord.deleted_at.is_(None),
        ).order_by(DeployRecord.created_at.desc()).first()

        if existing is None:
            record = DeployRecord(
                user_id=user_id,
                client_id=client_id,
                msg_id=msg_id or 0,
                task_id=task_id or 0,
                chat_id=chat_id or 0,
                env='test',
                description=description,
                status=DeployRecord.STATUS_PENDING,
                detail=detail or {},
            )
            session.add(record)
            session.flush()
            return record.id, 'created'

        if existing.status == DeployRecord.STATUS_PUBLISHING:
            return existing.id, 'publishing'

        existing.status = DeployRecord.STATUS_PENDING
        existing.description = description
        existing.detail = detail or {}
        return existing.id, 'reset'


def reset_deploy_record_to_pending(user_id: int, record_id: int) -> bool:
    """将发布记录重置为 pending（状态不是 publishing 时生效）。

    与 retry_deploy_record 不同：此处不限定必须是 failed/cancel，
    只要不是 publishing（防止打断进行中的部署）即可重置为 pending。
    已经是 pending 时也视为成功（幂等）。
    """
    with get_db_session() as session:
        record = session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
            DeployRecord.user_id == user_id,
            DeployRecord.deleted_at.is_(None),
        ).first()
        if not record:
            return False
        if record.status == DeployRecord.STATUS_PUBLISHING:
            return False
        if record.status == DeployRecord.STATUS_PENDING:
            return True
        record.status = DeployRecord.STATUS_PENDING
        return True


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


def update_deploy_record_status(
    record_id: int,
    status: str,
    detail: dict = None,
    detail_patch: dict = None,
) -> bool:
    """更新发布记录的状态和详情（供调度器使用，不校验 user_id）

    Args:
        record_id: 记录 ID
        status: 新状态
        detail: 整体覆盖 detail（与 detail_patch 二选一）
        detail_patch: 仅合并指定字段到现有 detail（读-改-写语义），
            用于只想追加/更新少量字段（如 deploy_log）而不丢失
            调度过程已写入的 trace_id/host_key/commits 等。
    """
    with get_db_session() as session:
        if detail_patch is not None:
            record = session.query(DeployRecord).filter(
                DeployRecord.id == record_id,
            ).first()
            if not record:
                return False
            merged = dict(record.detail or {})
            merged.update(detail_patch)
            record.status = status
            record.detail = merged
            return True

        update_data = {DeployRecord.status: status}
        if detail is not None:
            update_data[DeployRecord.detail] = detail
        affected = session.query(DeployRecord).filter(
            DeployRecord.id == record_id,
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
