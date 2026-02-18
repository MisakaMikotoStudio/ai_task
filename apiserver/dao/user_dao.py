#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户数据访问对象 - SQLAlchemy ORM 版本
"""

import secrets
from datetime import datetime
from typing import Optional, List

from .connection import get_db_session
from .models import User, UserSecret


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
            User.last_access_at: datetime.now()
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


# ========== 秘钥管理 ==========

def get_user_secrets(user_id: int) -> List[UserSecret]:
    """获取用户的秘钥列表"""
    with get_db_session() as session:
        secrets_list = session.query(UserSecret).filter(
            UserSecret.user_id == user_id
        ).order_by(UserSecret.created_at.desc()).all()
        return secrets_list


def create_user_secret(user_id: int, name: str) -> UserSecret:
    """创建新秘钥（随机生成64位字符串）"""
    with get_db_session() as session:
        # 生成64位随机字符串
        secret_value = secrets.token_hex(32)  # 32 bytes = 64 hex chars
        user_secret = UserSecret(
            user_id=user_id,
            name=name,
            secret=secret_value
        )
        session.add(user_secret)
        session.flush()
        return user_secret


def delete_user_secret(secret_id: int, user_id: int) -> bool:
    """删除秘钥"""
    with get_db_session() as session:
        affected = session.query(UserSecret).filter(
            UserSecret.id == secret_id,
            UserSecret.user_id == user_id
        ).delete()
        return affected > 0


def count_user_secrets(user_id: int) -> int:
    """统计用户当前的秘钥数量"""
    with get_db_session() as session:
        return session.query(UserSecret).filter(
            UserSecret.user_id == user_id
        ).count()


def get_cloud_secret_for_user(user_id: int) -> Optional[UserSecret]:
    """获取用户名为'云客户端专用(请勿删除)'的秘钥"""
    with get_db_session() as session:
        return session.query(UserSecret).filter(
            UserSecret.user_id == user_id,
            UserSecret.name == '云客户端专用(请勿删除)'
        ).first()


def get_user_by_secret(secret: str) -> Optional[User]:
    """通过秘钥获取用户"""
    with get_db_session() as session:
        user_secret = session.query(UserSecret).filter(
            UserSecret.secret == secret
        ).first()
        if not user_secret:
            return None
        user = session.query(User).filter(User.id == user_secret.user_id).first()
        return user
