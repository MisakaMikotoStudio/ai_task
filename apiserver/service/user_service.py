#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户业务逻辑服务层
"""

from typing import Optional

from dao.models import User
from dao.user_dao import (
    create_user, get_user_by_name, get_user_by_id,
    update_last_access, check_user_exists
)
from dao.secret_dao import get_user_id_by_secret
from dao.session_dao import create_session, get_session_by_token

class UserInfo:
    def __init__(self, id: int, name: str, token: str):
        self.id = id
        self.name = name
        self.token = token  
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'token': self.token,
        }


RESERVED_USERNAMES = {'admin'}

def register_user(name: str, password_hash: str) -> dict:
    """
    用户注册业务逻辑
    
    Args:
        name: 用户名，不能为空，长度不超过32个字符
        password_hash: 密码哈希值，不能为空
        
    Returns:
        用户信息字典 {'id': int, 'name': str, 'token': str}
        
    Raises:
        Exception: 参数校验失败或用户名已存在时抛出
    """
    name = (name or '').strip()
    password_hash = (password_hash or '').strip()
    
    if not name:
        raise Exception('用户名不能为空')
    
    if not password_hash:
        raise Exception('密码不能为空')
    
    if len(name) > 32:
        raise Exception('用户名长度不能超过32个字符')
    
    if name.lower() in RESERVED_USERNAMES:
        raise Exception('该用户名为系统保留账号，不允许注册')
    
    if check_user_exists(name):
        raise Exception('用户名已存在')
    
    user_id = create_user(name, password_hash)
    token = create_session(user_id).token    
    return UserInfo(user_id, name, token)


def login_user(name: str, password_hash: str) -> UserInfo:
    """
    用户登录业务逻辑
    
    Args:
        name: 用户名
        password_hash: 密码哈希值
        
    Returns:
        登录结果字典:
        {
            'token': str,           # JWT认证token
            'user': {
                'id': int,          # 用户ID
                'name': str         # 用户名
            }
        }
        
    Raises:
        Exception: 参数为空或用户名密码错误时抛出
    """
    name = (name or '').strip()
    password_hash = (password_hash or '').strip()
    
    if not name or not password_hash:
        raise Exception('用户名和密码不能为空')
    
    user = get_user_by_name(name)
    
    if not user or user.password_hash != password_hash:
        raise Exception('用户名或密码错误')
    
    update_last_access(user.id)
    token = create_session(user.id).token
    
    return UserInfo(user.id, user.name, token)


def get_user_info(token: str) -> UserInfo:
    """
    获取用户信息
    
    Args:
        token: 用户Token
        
    Returns:
        用户信息字典（通过 User.to_dict() 转换）:
        {
            'id': int,
            'name': str,
            'created_at': str,
            'last_access_at': str
        }
        
    Raises:
        Exception: 用户不存在时抛出
    """
    user_id = get_session_by_token(token).user_id
    user = get_user_by_id(user_id)
    
    if not user:
        raise Exception('用户不存在或Token无效')
    
    return UserInfo(user.id, user.name, token)


def get_user_by_secret(secret: str) -> Optional[User]:
    """通过秘钥获取用户（secret_dao 只负责查 user_id，由此处完成完整用户查询）"""
    user_id = get_user_id_by_secret(secret)
    if user_id is None:
        return None
    return get_user_by_id(user_id)
