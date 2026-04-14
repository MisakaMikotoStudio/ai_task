#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务数据访问对象 - SQLAlchemy ORM 版本
"""

import json
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

from sqlalchemy import case

from .connection import get_db_session
from .models import Task


def create_task(user_id: int, title: str, client_id: int,
                status: str = Task.STATUS_PENDING) -> Task:
    """
    创建任务

    Args:
        user_id: 用户ID
        title: 任务标题
        client_id: 客户端ID（必填，且必须为正整数）
        status: 任务状态（可选，默认pending）

    Returns:
        Task对象
    """
    with get_db_session() as session:
        task = Task(user_id=user_id, title=title, status=status, client_id=client_id)
        session.add(task)
        session.flush()
        return task


def get_tasks_by_user(
    user_id: int,
    statuses: Optional[List[str]] = None,
    page: int = 1,
    page_num: int = 20
) -> Dict[str, Any]:
    """
    获取用户的任务（含客户端名称）

    Args:
        user_id: 用户ID
        statuses: 任务状态过滤列表（可选）
        page: 页码，从 1 开始
        page_num: 每页条数

    Returns:
        分页后的任务字典列表和总数
    """
    from .models import Client
    with get_db_session() as session:
        query = session.query(Task, Client.name).outerjoin(
            Client, Task.client_id == Client.id
        ).filter(
            Task.user_id == user_id,
            Task.deleted_at.is_(None)
        )

        if statuses:
            query = query.filter(Task.status.in_(statuses))

        total = query.count()

        status_order = case(
            (Task.status == Task.STATUS_RUNNING, 0),
            (Task.status == Task.STATUS_PENDING, 1),
            (Task.status == Task.STATUS_SUSPENDED, 2),
            (Task.status == Task.STATUS_COMPLETED, 3),
            else_=99
        )

        tasks = query.order_by(
            status_order.asc(),
            Task.created_at.desc()
        ).offset(
            (page - 1) * page_num
        ).limit(
            page_num
        ).all()

        result = []
        for task, client_name in tasks:
            task_dict = task.to_dict()
            task_dict['client_name'] = client_name
            result.append(task_dict)
        return {'items': result, 'total': total}


def get_task_by_id(task_id: int, user_id: int) -> Optional[Task]:
    """
    获取指定任务
    
    Args:
        task_id: 任务ID
        user_id: 用户ID
        
    Returns:
        Task对象或None
    """
    with get_db_session() as session:
        task = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted_at.is_(None)
        ).first()
        return task


def update_task(
    task_id: int,
    user_id: int,
    status: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None
) -> bool:
    """
    更新任务字段（仅支持 status 和 extra）。
    传入哪些字段（非 None）就更新哪些字段。
    """
    update_data = {}

    if status is not None:
        update_data[Task.status] = status

    if extra is not None:
        update_data[Task.extra] = json.dumps(extra or {}, ensure_ascii=False)

    if not update_data:
        return False

    with get_db_session() as session:
        affected = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted_at.is_(None)
        ).update(update_data)
        return affected > 0


def delete_task(task_id: int, user_id: int) -> bool:
    """
    软删除任务（设置 deleted_at）

    Args:
        task_id: 任务ID
        user_id: 用户ID

    Returns:
        是否删除成功
    """
    with get_db_session() as session:
        affected = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted_at.is_(None)
        ).update({
            Task.deleted_at: datetime.now(timezone.utc)
        })
        return affected > 0

