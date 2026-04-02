#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SQLAlchemy 数据库连接管理
"""

from contextlib import contextmanager
from typing import Optional, Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, Session

from config_model import DatabaseConfig

# 全局引擎和Session工厂
_engine = None
_session_factory = None
_scoped_session = None


def init_connection(config: DatabaseConfig):
    """
    初始化数据库连接
    
    Args:
        config: 数据库配置对象
    """
    global _engine, _session_factory, _scoped_session
    
    if config.type != "mysql":
        raise ValueError(f"Unsupported database type: {config.type}. Only 'mysql' is supported.")
    
    # 构建连接URL
    connection_url = (
        f"mysql+pymysql://{config.username}:{config.password}"
        f"@{config.url}:{config.port}/{config.database}?charset=utf8mb4"
    )
    
    # 创建引擎
    _engine = create_engine(
        connection_url,
        echo=False,              # 生产环境关闭SQL日志
        pool_size=10,            # 连接池大小
        max_overflow=20,         # 超出池大小后最多再创建的连接数
        pool_timeout=30,         # 等待连接超时时间
        pool_recycle=3600,       # 连接回收时间（1小时）
        pool_pre_ping=True,      # 使用前检测连接是否存活
        connect_args={
            "init_command": "SET SESSION time_zone='+00:00'"
        }
    )
    
    # 创建Session工厂
    _session_factory = sessionmaker(
        bind=_engine,
        expire_on_commit=False   # 提交后不过期对象，避免延迟加载问题
    )
    
    # 创建线程安全的scoped_session
    _scoped_session = scoped_session(_session_factory)
    
    print(f"Database engine initialized: {config.url}:{config.port}/{config.database}")


def get_engine():
    """获取数据库引擎"""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_connection first.")
    return _engine


def get_session() -> Session:
    """
    获取数据库Session（线程安全）
    
    Returns:
        SQLAlchemy Session对象
    """
    if _scoped_session is None:
        raise RuntimeError("Database not initialized. Call init_connection first.")
    return _scoped_session()


def remove_session():
    """移除当前线程的Session（用于请求结束后清理）"""
    if _scoped_session is not None:
        _scoped_session.remove()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    获取数据库Session的上下文管理器

    自动处理提交和回滚

    Usage:
        with get_db_session() as session:
            user = session.query(User).filter(User.id == 1).first()
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
