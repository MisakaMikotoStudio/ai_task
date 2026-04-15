#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库初始化 - 使用 SQLAlchemy ORM 创建表
"""

import logging

from config_model import DatabaseConfig
from .connection import init_connection, get_engine
from .models import Base, User, Client, Task
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _run_migrations(engine):
    """执行增量迁移（新增字段等）"""
    migrations = [
        # ClientEnvVar 表增加 env 字段（已存在则忽略）
        """
        ALTER TABLE ai_task_client_env_vars
        ADD COLUMN IF NOT EXISTS `env` VARCHAR(16) NULL DEFAULT NULL
        COMMENT '环境标识：test/prod，NULL表示通用'
        """,
        # ClientDeploy 表增加 repo_id 字段
        """
        ALTER TABLE ai_task_client_deploys
        ADD COLUMN IF NOT EXISTS `repo_id` INT NULL
        COMMENT '关联仓库ID（ai_task_client_repos.id）'
        """,
        # ClientDeploy 表增加 work_dir 字段
        """
        ALTER TABLE ai_task_client_deploys
        ADD COLUMN IF NOT EXISTS `work_dir` VARCHAR(512) NULL DEFAULT ''
        COMMENT '工作目录路径，启动命令在此目录下运行'
        """,
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql.strip()))
                conn.commit()
            except Exception as e:
                # 字段已存在等情况忽略
                logger.debug("Migration skipped (may already exist): %s", str(e)[:100])


def init_database(config: DatabaseConfig):
    """
    初始化数据库
    1. 初始化连接配置
    2. 使用 SQLAlchemy ORM 创建表
    3. 执行增量字段迁移
    """
    # 初始化连接
    init_connection(config)

    engine = get_engine()
    # 创建表（基于 ORM 定义；不会做增量迁移）
    Base.metadata.create_all(engine)
    # 执行增量迁移（新增字段等）
    _run_migrations(engine)
    print("Database initialization completed.")
