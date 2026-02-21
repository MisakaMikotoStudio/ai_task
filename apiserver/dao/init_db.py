#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库初始化 - 使用 SQLAlchemy ORM 创建表
"""

from sqlalchemy import inspect

from config_model import DatabaseConfig
from .connection import init_connection, get_engine
from .models import Base, User, Client, Task


def init_database(config: DatabaseConfig):
    """
    初始化数据库
    1. 初始化连接配置
    2. 检查表是否存在
    3. 不存在则创建
    """
    # 初始化连接
    init_connection(config)
    
    engine = get_engine()
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    # 需要创建的表
    required_tables = ['ai_task_users', 'ai_task_clients', 'ai_task_tasks', 'ai_task_user_sessions']
    
    print("Checking database tables...")
    
    for table_name in required_tables:
        if table_name in existing_tables:
            print(f"  ✓ Table '{table_name}' already exists")
        else:
            print(f"  → Table '{table_name}' will be created")
    
    # 创建所有不存在的表
    Base.metadata.create_all(engine)
    
    # 再次检查确认
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    
    init_failed = False
    for table_name in required_tables:
        if table_name in existing_tables:
            print(f"  ✓ Table '{table_name}' ready")
        else:
            init_failed = True
            print(f"  ✗ Table '{table_name}' creation failed!")
    if init_failed:
        raise RuntimeError("Database initialization failed.")
    print("Database initialization completed.")
