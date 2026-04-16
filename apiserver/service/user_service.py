#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户业务逻辑服务层
"""

from typing import Optional

from dao.models import User
from dao.user_dao import (
    create_user, get_user_by_name, get_user_by_id, get_user_by_public_user_id,
    update_last_access, check_user_exists
)
from dao.secret_dao import get_user_id_by_secret
from dao.session_dao import create_session, get_session_by_token


class UserServiceError(Exception):
    """用户服务业务逻辑错误（校验失败、认证失败等）"""
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


class UserInfo:
    def __init__(self, user_id: int, name: str, token: str):
        self.user_id = user_id
        self.name = name
        self.token = token

    def to_dict(self):
        return {
            'user_id': self.user_id,
            'name': self.name,
            'token': self.token,
        }


RESERVED_USERNAMES = {'admin'}

def register_user(name: str, password_hash: str) -> UserInfo:
    """
    用户注册业务逻辑

    Returns:
        UserInfo（对外 user_id、用户名、token）
    """
    name = (name or '').strip()
    password_hash = (password_hash or '').strip()

    if not name:
        raise UserServiceError('用户名不能为空')

    if not password_hash:
        raise UserServiceError('密码不能为空')

    if len(name) > 32:
        raise UserServiceError('用户名长度不能超过32个字符')

    if name.lower() in RESERVED_USERNAMES:
        raise UserServiceError('该用户名为系统保留账号，不允许注册')

    if check_user_exists(name):
        raise UserServiceError('用户名已存在', 409)

    internal_id, public_uid = create_user(name, password_hash)
    token = create_session(internal_id).token
    return UserInfo(public_uid, name, token)


def login_user(name: str, password_hash: str) -> UserInfo:
    """
    用户登录业务逻辑
    """
    name = (name or '').strip()
    password_hash = (password_hash or '').strip()

    if not name or not password_hash:
        raise UserServiceError('用户名和密码不能为空')

    user = get_user_by_name(name)

    if not user or user.password_hash != password_hash:
        raise UserServiceError('用户名或密码错误', 401)

    update_last_access(user.id)
    token = create_session(user.id).token

    return UserInfo(user.user_id, user.name, token)


def get_user_info(token: str) -> UserInfo:
    """
    获取用户信息（登录/注册响应用）
    """
    user_session = get_session_by_token(token)
    if not user_session:
        raise UserServiceError('用户不存在或Token无效', 401)

    user = get_user_by_id(user_session.user_id)

    if not user:
        raise UserServiceError('用户不存在或Token无效', 401)

    return UserInfo(user.user_id, user.name, token)


def get_user_by_secret(secret: str) -> Optional[User]:
    """通过秘钥获取用户（秘钥表存对外 user_id）"""
    public_uid = get_user_id_by_secret(secret)
    if public_uid is None:
        return None
    return get_user_by_public_user_id(public_uid)
