#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
待办事项数据访问对象
"""

from typing import List, Optional
from datetime import datetime, timezone

from .connection import get_db_session
from .models import TodoItem


def create_todo(user_id: int, content: str) -> TodoItem:
    """创建待办事项"""
    with get_db_session() as session:
        # 获取当前最大 sort_order
        max_order = session.query(TodoItem.sort_order).filter(
            TodoItem.user_id == user_id,
            TodoItem.deleted_at.is_(None)
        ).order_by(TodoItem.sort_order.desc()).first()
        next_order = (max_order[0] + 1) if max_order else 0

        todo = TodoItem(user_id=user_id, content=content, sort_order=next_order)
        session.add(todo)
        session.flush()
        return todo


def get_todos_by_user(user_id: int) -> List[TodoItem]:
    """获取用户的所有待办事项"""
    with get_db_session() as session:
        todos = session.query(TodoItem).filter(
            TodoItem.user_id == user_id,
            TodoItem.deleted_at.is_(None)
        ).order_by(TodoItem.sort_order.asc()).all()
        return todos


def get_todo_by_id(todo_id: int, user_id: int) -> Optional[TodoItem]:
    """根据ID获取待办事项"""
    with get_db_session() as session:
        return session.query(TodoItem).filter(
            TodoItem.id == todo_id,
            TodoItem.user_id == user_id,
            TodoItem.deleted_at.is_(None)
        ).first()


def update_todo(todo_id: int, user_id: int, content: str = None, completed: bool = None) -> Optional[TodoItem]:
    """更新待办事项"""
    with get_db_session() as session:
        todo = session.query(TodoItem).filter(
            TodoItem.id == todo_id,
            TodoItem.user_id == user_id,
            TodoItem.deleted_at.is_(None)
        ).first()
        if not todo:
            return None
        if content is not None:
            todo.content = content
        if completed is not None:
            todo.completed = completed
        session.flush()
        return todo


def delete_todo(todo_id: int, user_id: int) -> bool:
    """删除待办事项"""
    with get_db_session() as session:
        now = datetime.now(timezone.utc)
        affected = session.query(TodoItem).filter(
            TodoItem.id == todo_id,
            TodoItem.user_id == user_id,
            TodoItem.deleted_at.is_(None)
        ).update({TodoItem.deleted_at: now}, synchronize_session=False)
        return affected > 0
