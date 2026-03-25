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
    获取用户的有效秘钥列表（deleted=0）。
    若无任何有效秘钥，则检查用户是否完全没有秘钥记录（含已删除）；
    如果为空则自动为用户创建 type=cloud 的秘钥。
    """
    with get_db_session() as session:
        active_secrets = session.query(UserSecret).filter(
            UserSecret.user_id == user_id,
            UserSecret.deleted == 0
        ).order_by(UserSecret.created_at.asc()).all()

        if active_secrets:
            return active_secrets

        total_count = session.query(UserSecret).filter(
            UserSecret.user_id == user_id
        ).count()

        if total_count == 0:
            cloud_secret = UserSecret(
                user_id=user_id,
                name=CLOUD_SECRET_NAME,
                secret=secrets_module.token_hex(32),
                type=UserSecret.TYPE_CLOUD,
                deleted=0
            )
            session.add(cloud_secret)
            session.flush()
            return [cloud_secret]

        return []


def create_user_secret(user_id: int, name: str, secret_type: str = 'personal') -> UserSecret:
    """创建新秘钥（随机生成64位字符串）"""
    with get_db_session() as session:
        user_secret = UserSecret(
            user_id=user_id,
            name=name,
            secret=secrets_module.token_hex(32),
            type=secret_type,
            deleted=0
        )
        session.add(user_secret)
        session.flush()
        return user_secret

def delete_user_secret(secret_id: int, user_id: int) -> bool:
    """软删除秘钥（将 deleted 置为 1）"""
    with get_db_session() as session:
        affected = session.query(UserSecret).filter(
            UserSecret.id == secret_id,
            UserSecret.user_id == user_id,
            UserSecret.deleted == 0
        ).update({UserSecret.deleted: 1})
        return affected > 0

def get_user_id_by_secret(secret: str) -> Optional[int]:
    """通过秘钥查询对应的 user_id（仅查询有效秘钥，deleted=0）"""
    with get_db_session() as session:
        user_secret = session.query(UserSecret).filter(
            UserSecret.secret == secret,
            UserSecret.deleted == 0
        ).first()
        return user_secret.user_id if user_secret else None


def update_secret_last_used_at(secret: str) -> bool:
    """更新秘钥最近使用时间"""
    with get_db_session() as session:
        affected = session.query(UserSecret).filter(
            UserSecret.secret == secret,
            UserSecret.deleted == 0
        ).update({
            UserSecret.last_used_at: datetime.now(timezone.utc)
        })
        return affected > 0
