#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库初始化 - 使用 SQLAlchemy ORM 创建表
"""

from config_model import DatabaseConfig
from .connection import init_connection, get_engine
from .models import Base, User, Client, Task
from sqlalchemy import text


def init_database(config: DatabaseConfig):
    """
    初始化数据库
    1. 初始化连接配置
    2. 使用 SQLAlchemy ORM 创建表
    """
    # 初始化连接
    init_connection(config)
    
    engine = get_engine()
    # 创建表（基于 ORM 定义；不会做增量迁移）
    Base.metadata.create_all(engine)
    print("Database initialization completed.")
