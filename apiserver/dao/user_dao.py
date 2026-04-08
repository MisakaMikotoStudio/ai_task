#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户数据访问对象 - SQLAlchemy ORM 版本
"""

import random
from datetime import datetime, timezone
from typing import Optional

from .connection import get_db_session
from .models import User


def _random_public_user_id() -> int:
    """6 位整数，首位 1–9。"""
    return random.randint(100000, 999999)


def _allocate_unique_public_user_id(session) -> int:
    for _ in range(300):
        uid = _random_public_user_id()
        if session.query(User).filter(User.user_id == uid).count() == 0:
            return uid
    raise RuntimeError('无法生成唯一的用户编号，请稍后重试')


def create_user(name: str, password_hash: str) -> tuple:
    """
    创建用户（自动分配对外 user_id）

    Returns:
        (internal_id, public_user_id)
    """
    with get_db_session() as session:
        uid = _allocate_unique_public_user_id(session)
        user = User(name=name, password_hash=password_hash, user_id=uid)
        session.add(user)
        session.flush()
        return user.id, user.user_id


def get_user_by_name(name: str) -> Optional[User]:
    """
    根据用户名获取用户

    Args:
        name: 用户名

    Returns:
        User对象或None
    """
    with get_db_session() as session:
        user = session.query(User).filter(User.name == name).first()
        return user


def get_user_by_id(internal_id: int) -> Optional[User]:
    """
    根据内部主键 id 获取用户（会话、鉴权内部使用）
    """
    with get_db_session() as session:
        user = session.query(User).filter(User.id == internal_id).first()
        return user


def get_user_by_public_user_id(public_user_id: int) -> Optional[User]:
    """根据对外 user_id（6 位）获取用户。"""
    with get_db_session() as session:
        return session.query(User).filter(User.user_id == public_user_id).first()


def update_last_access(internal_id: int):
    """
    更新用户最后访问时间

    Args:
        internal_id: 用户内部主键 id
    """
    with get_db_session() as session:
        session.query(User).filter(User.id == internal_id).update({
            User.last_access_at: datetime.now(timezone.utc)
        })


def check_user_exists(name: str) -> bool:
    """
    检查用户名是否已存在

    Args:
        name: 用户名

    Returns:
        是否存在
    """
    with get_db_session() as session:
        count = session.query(User).filter(User.name == name).count()
        return count > 0
