#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户会话数据访问对象
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import secrets

from .connection import get_db_session
from .models import UserSession


def generate_session_token() -> str:
    """
    生成随机 token
    
    Returns:
        64字符的随机 token
    """
    return secrets.token_hex(32)


def create_session(user_id: int, expire_days: int = 7) -> UserSession:
    """
    创建用户会话
    
    Args:
        user_id: 用户ID
        expire_days: 过期天数，默认7天
        
    Returns:
        新创建的会话对象
    """
    token = generate_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=expire_days)

    with get_db_session() as session:
        user_session = UserSession(user_id=user_id, token=token, expires_at=expires_at)
        session.add(user_session)
    
    return get_session_by_token(token)


def get_session_by_token(token: str) -> Optional[UserSession]:
    """
    根据 token 获取会话信息
    
    Args:
        token: 会话 token
        
    Returns:
        UserSession 对象或 None
    """
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        user_session = session.query(UserSession).filter(
            UserSession.token == token,
            UserSession.expires_at > now
        ).first()
        return user_session
