#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务数据访问对象 - SQLAlchemy ORM 版本
"""

import random
import string
import json
from typing import Optional, Dict, List, Any

from .connection import get_db_session
from .models import Task


def generate_task_key() -> str:
    """生成8位随机任务key（大小写字母）"""
    letters = string.ascii_letters
    return ''.join(random.choice(letters) for _ in range(8))


def create_task(user_id: int, title: str, task_type: str, client_id: Optional[int] = None,
                desc: Optional[str] = None, status: Optional[str] = None) -> Task:
    """
    创建任务

    Args:
        user_id: 用户ID
        title: 任务标题
        task_type: 任务类型
        client_id: 客户端ID（可选，不设置时任务可被任意client处理）
        desc: 任务描述（可选）
        status: 任务状态（可选，默认pending）

    Returns:
        Task对象
    """
    with get_db_session() as session:
        task = Task(
            user_id=user_id,
            key=generate_task_key(),
            title=title,
            desc=desc,
            status=status or Task.STATUS_PENDING,
            client_id=client_id,  # 可为 None
            type=task_type,
            flow={},
            flow_status='pending'
        )
        session.add(task)
        session.flush()
        return task


def get_tasks_by_user(user_id: int, status: Optional[str] = None, client_id: Optional[int] = None) -> List[Dict]:
    """
    获取用户的任务（含客户端名称）

    Args:
        user_id: 用户ID
        status: 任务状态过滤（可选）
        client_id: 客户端ID过滤（可选，0 表示未分配客户端的任务）

    Returns:
        任务字典列表
    """
    from .models import Client
    with get_db_session() as session:
        query = session.query(Task, Client.name).outerjoin(
            Client, Task.client_id == Client.id
        ).filter(
            Task.user_id == user_id,
            Task.deleted == 0
        )
        
        # 添加状态过滤
        if status:
            query = query.filter(Task.status == status)
        
        # 添加客户端过滤
        if client_id is not None:
            query = query.filter(Task.client_id == client_id)
        
        tasks = query.order_by(Task.created_at.desc()).all()

        result = []
        for task, client_name in tasks:
            task_dict = task.to_dict()
            task_dict['client_name'] = client_name
            result.append(task_dict)
        return result


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
            Task.deleted == 0
        ).first()
        return task


def update_task_status(task_id: int, user_id: int, status: str) -> bool:
    """
    更新任务状态
    
    Args:
        task_id: 任务ID
        user_id: 用户ID
        status: 新状态
        
    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        affected = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted == 0
        ).update({
            Task.status: status
        })
        return affected > 0


def update_task_flow(task_id: int, user_id: int, flow: Optional[Dict] = None, flow_status: Optional[str] = None) -> bool:
    """
    更新任务流程（只更新非 None 的字段）

    Args:
        task_id: 任务ID
        user_id: 用户ID
        flow: 流程数据（可选）
        flow_status: 流程状态（可选）

    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        update_data = {}
        if flow is not None:
            update_data[Task.flow] = flow
        if flow_status is not None:
            update_data[Task.flow_status] = flow_status
        
        if not update_data:
            return True  # 没有需要更新的字段，直接返回成功
        
        affected = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted == 0
        ).update(update_data)
        return affected > 0


def update_task_desc(task_id: int, user_id: int, desc: str, status: Optional[str] = None) -> bool:
    """
    更新任务描述

    Args:
        task_id: 任务ID
        user_id: 用户ID
        desc: 新的任务描述
        status: 任务状态（可选）

    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        update_data = {Task.desc: desc}
        if status is not None:
            update_data[Task.status] = status

        affected = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted == 0
        ).update(update_data)
        return affected > 0


def delete_task(task_id: int, user_id: int) -> bool:
    """
    软删除任务（deleted=1）

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
            Task.deleted == 0
        ).update({
            Task.deleted: 1
        })
        return affected > 0


def update_task_client(task_id: int, user_id: int, client_id: int) -> bool:
    """
    更新任务关联的客户端

    Args:
        task_id: 任务ID
        user_id: 用户ID
        client_id: 新的客户端ID

    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        affected = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted == 0
        ).update({Task.client_id: client_id})
        return affected > 0


def update_task_extra(task_id: int, user_id: int, extra: Dict[str, Any]) -> bool:
    """
    更新任务的 extra 字段（存储 JSON 字符串）。
    """
    extra_json = json.dumps(extra or {}, ensure_ascii=False)
    with get_db_session() as session:
        affected = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.deleted == 0
        ).update({Task.extra: extra_json})
        return affected > 0