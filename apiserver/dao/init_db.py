#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库初始化 - 使用 SQLAlchemy ORM 创建表
"""

from sqlalchemy import inspect
from sqlalchemy import text

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
    
    # ai_task_clients 增加 version 字段（兼容已存在的库：create_all 不会自动做列迁移）
    inspector = inspect(engine)
    try:
        client_columns = [c.get('name') for c in inspector.get_columns('ai_task_clients')]
        if 'version' not in client_columns:
            print("  → Column 'version' missing, ALTER TABLE will be executed")
            with engine.begin() as conn:
                # MySQL: allow adding NOT NULL with default
                conn.execute(text("ALTER TABLE ai_task_clients ADD COLUMN version INT NOT NULL DEFAULT 1"))
            print("  ✓ Column 'version' added")
    except Exception as e:
        raise RuntimeError(f"Failed to ensure column 'version' exists on ai_task_clients: {e}")

    # ai_task_tasks 增加 extra 字段（兼容已存在的库：create_all 不会自动做列迁移）
    inspector = inspect(engine)
    try:
        task_columns = [c.get('name') for c in inspector.get_columns('ai_task_tasks')]
        if 'extra' not in task_columns:
            print("  → Column 'extra' missing, ALTER TABLE will be executed")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE ai_task_tasks ADD COLUMN extra TEXT NULL"))
            print("  ✓ Column 'extra' added")
    except Exception as e:
        raise RuntimeError(f"Failed to ensure column 'extra' exists on ai_task_tasks: {e}")

    # ai_task_tasks 增加 deleted 字段（兼容已存在的库：create_all 不会自动做列迁移）
    inspector = inspect(engine)
    try:
        task_columns = [c.get('name') for c in inspector.get_columns('ai_task_tasks')]
        if 'deleted' not in task_columns:
            print("  → Column 'deleted' missing, ALTER TABLE will be executed")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE ai_task_tasks ADD COLUMN deleted INT NOT NULL DEFAULT 0"))
            print("  ✓ Column 'deleted' added")
    except Exception as e:
        raise RuntimeError(f"Failed to ensure column 'deleted' exists on ai_task_tasks: {e}")

    # ai_task_user_secrets 增加 last_used_at 字段（兼容已存在的库：create_all 不会自动做列迁移）
    inspector = inspect(engine)
    try:
        user_secret_columns = [c.get('name') for c in inspector.get_columns('ai_task_user_secrets')]
        if 'last_used_at' not in user_secret_columns:
            print("  → Column 'last_used_at' missing, ALTER TABLE will be executed")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE ai_task_user_secrets ADD COLUMN last_used_at DATETIME NULL"))
            print("  ✓ Column 'last_used_at' added")
    except Exception as e:
        raise RuntimeError(f"Failed to ensure column 'last_used_at' exists on ai_task_user_secrets: {e}")

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
