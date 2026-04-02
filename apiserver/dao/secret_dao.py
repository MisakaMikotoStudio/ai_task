#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户秘钥数据访问对象
"""

import secrets as secrets_module
from datetime import datetime, timezone
from typing import Optional, List

from .connection import get_db_session
from .models import UserSecret

CLOUD_SECRET_NAME = '官方客户端cloud专用'


def get_user_secrets(user_id: int) -> List[UserSecret]:
    """
    获取用户的有效秘钥列表（deleted_at 为空）。
    若有效秘钥中不存在 type=cloud，则自动补建一条 cloud 秘钥并返回。
    """
    with get_db_session() as session:
        active_secrets = session.query(UserSecret).filter(
            UserSecret.user_id == user_id,
            UserSecret.deleted_at.is_(None)
        ).order_by(UserSecret.created_at.asc()).all()

        has_cloud_secret = any(secret.type == UserSecret.TYPE_CLOUD for secret in active_secrets)
        if not has_cloud_secret:
            cloud_secret = UserSecret(
                user_id=user_id,
                name=CLOUD_SECRET_NAME,
                secret=secrets_module.token_hex(32),
                type=UserSecret.TYPE_CLOUD,
                deleted_at=None
            )
            session.add(cloud_secret)
            session.flush()
            active_secrets.append(cloud_secret)

        return active_secrets


def create_user_secret(user_id: int, name: str, secret_type: str = 'personal') -> UserSecret:
    """创建新秘钥（随机生成64位字符串）"""
    with get_db_session() as session:
        user_secret = UserSecret(
            user_id=user_id,
            name=name,
            secret=secrets_module.token_hex(32),
            type=secret_type,
            deleted_at=None
        )
        session.add(user_secret)
        session.flush()
        return user_secret

def delete_user_secret(secret_id: int, user_id: int) -> bool:
    """软删除秘钥（设置 deleted_at）"""
    with get_db_session() as session:
        affected = session.query(UserSecret).filter(
            UserSecret.id == secret_id,
            UserSecret.user_id == user_id,
            UserSecret.deleted_at.is_(None)
        ).update({UserSecret.deleted_at: datetime.now(timezone.utc)})
        return affected > 0

def get_user_id_by_secret(secret: str) -> Optional[int]:
    """通过秘钥查询对应的 user_id（仅查询有效秘钥，deleted_at 为空）"""
    with get_db_session() as session:
        user_secret = session.query(UserSecret).filter(
            UserSecret.secret == secret,
            UserSecret.deleted_at.is_(None)
        ).first()
        return user_secret.user_id if user_secret else None


def update_secret_last_used_at(secret: str) -> bool:
    """更新秘钥最近使用时间"""
    with get_db_session() as session:
        affected = session.query(UserSecret).filter(
            UserSecret.secret == secret,
            UserSecret.deleted_at.is_(None)
        ).update({
            UserSecret.last_used_at: datetime.now(timezone.utc)
        })
        return affected > 0
