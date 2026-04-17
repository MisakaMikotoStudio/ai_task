#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库初始化 - 使用 SQLAlchemy ORM 创建表
"""

import logging

from config_model import DatabaseConfig
from .connection import init_connection, get_engine
from .models import Base, User, Client, Task
from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


def _add_column_if_missing(engine, table: str, column: str, column_ddl: str) -> None:
    """
    为已存在的表安全增加列（兼容标准 MySQL：无 ADD COLUMN IF NOT EXISTS）。

    create_all 不会给已有表补新列，故升级库依赖此逻辑。
    """
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    existing = {c['name'] for c in insp.get_columns(table)}
    if column in existing:
        return
    sql = f'ALTER TABLE `{table}` ADD COLUMN {column_ddl}'
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    logger.info('Migration applied: %s.%s', table, column)


def _try_create_deploy_records_client_msg_index(engine) -> None:
    """补建 (client_id, msg_id) 索引；已存在或不可创建时忽略。"""
    table = 'ai_task_deploy_records'
    index_name = 'idx_deploy_records_client_msg'
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    existing_idx = {ix.get('name') for ix in insp.get_indexes(table)}
    if index_name in existing_idx:
        return
    cols = {c['name'] for c in insp.get_columns(table)}
    if 'msg_id' not in cols or 'client_id' not in cols:
        return
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f'CREATE INDEX `{index_name}` ON `{table}` (`client_id`, `msg_id`)'
                )
            )
            conn.commit()
        logger.info('Migration applied: index %s on %s', index_name, table)
    except Exception as e:
        logger.debug('Index %s skipped: %s', index_name, str(e)[:120])


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
        # DeployRecord 表增加 msg_id 字段（关联 chat message）
        """
        ALTER TABLE ai_task_deploy_records
        ADD COLUMN IF NOT EXISTS `msg_id` INT NOT NULL DEFAULT 0
        COMMENT '关联 chat 消息ID，0 表示未关联'
        """,
        # DeployRecord 表增加 (client_id, msg_id) 组合索引（重复创建时会抛错，由外层 try 吞掉）
        """
        CREATE INDEX `idx_deploy_records_client_msg`
        ON ai_task_deploy_records (`client_id`, `msg_id`)
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

    # route_prefix：标准 MySQL 不支持 ADD COLUMN IF NOT EXISTS，单独用探测 + ALTER
    _add_column_if_missing(
        engine,
        table='ai_task_client_deploys',
        column='route_prefix',
        column_ddl=(
            "`route_prefix` VARCHAR(128) NOT NULL DEFAULT '' "
            "COMMENT '生产 nginx 路径前缀；空或/表示根；如/api 将路径前缀去掉后转发到容器'"
        ),
    )

    # 发布记录表：标准 MySQL 下带 IF NOT EXISTS 的 ALTER 会整句失败，用探测补列
    _add_column_if_missing(
        engine,
        table='ai_task_deploy_records',
        column='msg_id',
        column_ddl="`msg_id` INT NOT NULL DEFAULT 0 COMMENT '关联 chat 消息ID，0 表示未关联'",
    )
    _add_column_if_missing(
        engine,
        table='ai_task_deploy_records',
        column='task_id',
        column_ddl="`task_id` INT NOT NULL DEFAULT 0 COMMENT '关联任务ID，0 表示未关联'",
    )
    _add_column_if_missing(
        engine,
        table='ai_task_deploy_records',
        column='chat_id',
        column_ddl="`chat_id` INT NOT NULL DEFAULT 0 COMMENT '关联 Chat ID，0 表示未关联'",
    )

    # 索引：仅在 msg_id 列存在且索引尚未创建时尝试（失败仅打 debug）
    _try_create_deploy_records_client_msg_index(engine)


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
