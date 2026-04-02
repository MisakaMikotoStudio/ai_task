#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户数据访问对象 - SQLAlchemy ORM 版本
"""

from datetime import datetime, timezone
from typing import Optional

from .connection import get_db_session
from .models import User


def create_user(name: str, password_hash: str) -> int:
    """
    创建用户
    
    Args:
        name: 用户名
        password_hash: 密码哈希
        
    Returns:
        新创建的用户ID
    """
    with get_db_session() as session:
        user = User(name=name, password_hash=password_hash)
        session.add(user)
        session.flush()  # 获取自增ID
        return user.id


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


def get_user_by_id(user_id: int) -> Optional[User]:
    """
    根据ID获取用户
    
    Args:
        user_id: 用户ID
        
    Returns:
        User对象或None
    """
    with get_db_session() as session:
        user = session.query(User).filter(User.id == user_id).first()
        return user


def update_last_access(user_id: int):
    """
    更新用户最后访问时间
    
    Args:
        user_id: 用户ID
    """
    with get_db_session() as session:
        session.query(User).filter(User.id == user_id).update({
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
