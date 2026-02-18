#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SQLAlchemy ORM 模型定义
"""

from sqlalchemy import Column, Integer, String, DateTime, JSON, Index, func, BigInteger, Text, Date, DECIMAL, Boolean
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


class User(Base):
    """用户表"""
    __tablename__ = 'ai_task_users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False, comment='用户名')
    password_hash = Column(String(256), nullable=False, comment='密码哈希')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')
    last_access_at = Column(DateTime, nullable=True, comment='最后访问时间')


    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'created_at': str(self.created_at) if self.created_at else None,
            'last_access_at': str(self.last_access_at) if self.last_access_at else None
        }


class UserSession(Base):
    """用户会话表"""
    __tablename__ = 'ai_task_user_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    token = Column(String(255), unique=True, nullable=False, comment='Token')
    expires_at = Column(DateTime, nullable=False, comment='过期时间')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')

    __table_args__ = (
        Index('idx_user_sessions_user_id', 'user_id'),
    )


class Client(Base):
    """客户端表"""
    __tablename__ = 'ai_task_clients'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='所属用户ID')
    name = Column(String(16), nullable=False, comment='客户端名称')
    types = Column(JSON, default=list, comment='支持的任务类型')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')
    last_sync_at = Column(DateTime, nullable=True, comment='最后心跳时间')
    instance_uuid = Column(String(36), nullable=True, unique=True, comment='当前运行实例的唯一标识UUID')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间')
    is_public = Column(Boolean, default=False, comment='是否公开')
    creator_id = Column(Integer, nullable=False, default=0, comment='创始人ID')
    agent = Column(String(64), nullable=True, default='Claude Code', comment='Agent类型')

    __table_args__ = (
        Index('idx_clients_user_id', 'user_id'),
        Index('idx_clients_user_deleted', 'user_id', 'deleted_at'),
        Index('uk_user_client', 'user_id', 'name', unique=True),
    )

    def to_dict(self, include_creator_name: str = None):
        result = {
            'id': self.id,
            'name': self.name,
            'types': self.types or [],
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None,
            'last_sync_at': str(self.last_sync_at) if self.last_sync_at else None,
            'is_public': self.is_public or False,
            'creator_id': self.creator_id,
            'agent': self.agent or 'Claude Code'
        }
        if include_creator_name is not None:
            result['creator_name'] = include_creator_name
        return result


class ClientHeartbeat(Base):
    """客户端心跳记录表"""
    __tablename__ = 'ai_task_client_heartbeats'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='客户端ID')
    instance_uuid = Column(String(36), nullable=False, comment='客户端实例UUID')
    last_sync_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='最近同步时间')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')

    __table_args__ = (
        Index('idx_heartbeat_user_client', 'user_id', 'client_id'),
        Index('uk_user_client_unique', 'user_id', 'client_id', unique=True),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'client_id': self.client_id,
            'instance_uuid': self.instance_uuid,
            'last_sync_at': str(self.last_sync_at) if self.last_sync_at else None,
            'created_at': str(self.created_at) if self.created_at else None
        }


class Task(Base):
    """任务表"""
    __tablename__ = 'ai_task_tasks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='所属用户ID')
    key = Column(String(8), nullable=False, comment='任务唯一键')
    title = Column(String(45), nullable=False, default='', comment='任务标题')
    desc = Column(String, nullable=True, comment='任务描述')
    status = Column(String(20), default='pending', nullable=False, comment='任务状态')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    type = Column(String(64), nullable=False, comment='任务类型')
    flow = Column(JSON, default=dict, comment='流程配置')
    flow_status = Column(String(32), default='pending', comment='流程状态')
    key_result_id = Column(BigInteger, nullable=True, comment='关联的KR ID')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        Index('idx_tasks_user_id', 'user_id'),
        Index('idx_tasks_user_key', 'user_id', 'key', unique=True),
        Index('idx_tasks_user_status', 'user_id', 'status'),
    )

    # 状态常量
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_SUSPENDED = 'suspended'
    STATUS_COMPLETED = 'completed'

    STATUS_TEXT = {
        'pending': '未开始',
        'running': '进行中',
        'suspended': '已挂起',
        'completed': '已结束'
    }

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'title': self.title or '',
            'desc': self.desc or '',
            'status': self.status,
            'status_text': self.STATUS_TEXT.get(self.status, self.status),
            'client_id': self.client_id,
            'client_name': None,  # 需要单独查询
            'type': self.type,
            'flow': self.flow or {},
            'flow_status': self.flow_status or '',
            'key_result_id': self.key_result_id,
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None
        }


class Objective(Base):
    """OKR目标表"""
    __tablename__ = 'ai_task_okr_objectives'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, comment='所属用户')
    title = Column(String(255), nullable=False, comment='目标标题')
    description = Column(Text, nullable=True, comment='目标描述')
    status = Column(String(32), nullable=False, default='draft', comment='状态：draft/active/completed/archived')
    progress = Column(Integer, nullable=False, default=0, comment='完成进度 0-100')
    sort_order = Column(Integer, nullable=False, default=0, comment='排序顺序')
    cycle_type = Column(String(16), nullable=False, default='quarter', comment='周期类型：week/month/quarter')
    cycle_start = Column(Date, nullable=True, comment='周期开始日期')
    cycle_end = Column(Date, nullable=True, comment='周期结束日期')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        Index('idx_objectives_user_id', 'user_id'),
        Index('idx_objectives_cycle_type', 'cycle_type'),
    )

    STATUS_DRAFT = 'draft'
    STATUS_ACTIVE = 'active'
    STATUS_COMPLETED = 'completed'
    STATUS_ARCHIVED = 'archived'

    STATUS_TEXT = {
        'draft': '草稿',
        'active': '进行中',
        'completed': '已完成',
        'archived': '已归档'
    }

    CYCLE_TYPES = ['week', 'month', 'quarter']

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'title': self.title,
            'description': self.description or '',
            'status': self.status,
            'status_text': self.STATUS_TEXT.get(self.status, self.status),
            'progress': self.progress,
            'sort_order': self.sort_order,
            'cycle_type': self.cycle_type,
            'cycle_start': str(self.cycle_start) if self.cycle_start else None,
            'cycle_end': str(self.cycle_end) if self.cycle_end else None,
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None
        }


class KeyResult(Base):
    """OKR关键结果表"""
    __tablename__ = 'ai_task_okr_key_results'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    objective_id = Column(BigInteger, nullable=False, comment='关联的目标ID')
    title = Column(String(255), nullable=False, comment='KR标题')
    description = Column(Text, nullable=True, comment='KR描述')
    target_value = Column(DECIMAL(10, 2), nullable=True, comment='目标值')
    current_value = Column(DECIMAL(10, 2), nullable=True, default=0, comment='当前值')
    unit = Column(String(32), nullable=True, comment='单位')
    progress = Column(Integer, nullable=False, default=0, comment='完成进度 0-100')
    sort_order = Column(Integer, nullable=False, default=0, comment='排序顺序')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        Index('idx_key_results_objective_id', 'objective_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'objective_id': self.objective_id,
            'title': self.title,
            'description': self.description or '',
            'target_value': float(self.target_value) if self.target_value else None,
            'current_value': float(self.current_value) if self.current_value else 0,
            'unit': self.unit or '',
            'progress': self.progress,
            'sort_order': self.sort_order,
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None
        }


class TodoItem(Base):
    """待办事项表"""
    __tablename__ = 'ai_task_todos'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    content = Column(String(500), nullable=False, comment='待办内容')
    completed = Column(Boolean, default=False, comment='是否完成')
    sort_order = Column(Integer, default=0, comment='排序')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        Index('idx_todos_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'completed': self.completed,
            'sort_order': self.sort_order,
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None
        }


class ClientRepo(Base):
    """客户端仓库配置表"""
    __tablename__ = 'ai_task_client_repos'

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    desc = Column(String(255), nullable=False, comment='仓库简介')
    url = Column(String(512), nullable=False, comment='仓库URL')
    token = Column(String(255), nullable=True, comment='访问token')
    default_branch = Column(String(64), nullable=True, default='', comment='默认分支')
    branch_prefix = Column(String(64), nullable=False, default='ai_', comment='代码分支前缀')
    docs_repo = Column(Boolean, nullable=False, default=False, comment='是否为文档仓库')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        Index('idx_client_repos_client_id', 'client_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'desc': self.desc,
            'url': self.url,
            'token': self.token,
            'default_branch': self.default_branch or '',
            'branch_prefix': self.branch_prefix or 'ai_',
            'docs_repo': self.docs_repo or False,
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None
        }


class ClientEnvVar(Base):
    """客户端环境变量配置表"""
    __tablename__ = 'ai_task_client_env_vars'

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    key = Column(String(128), nullable=False, comment='环境变量名')
    value = Column(Text, nullable=True, comment='环境变量值')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        Index('idx_client_env_vars_client_id', 'client_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'key': self.key,
            'value': self.value or '',
            'created_at': str(self.created_at) if self.created_at else None,
            'updated_at': str(self.updated_at) if self.updated_at else None
        }


class UserSecret(Base):
    """用户秘钥表"""
    __tablename__ = 'ai_task_user_secrets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    secret = Column(String(64), nullable=False, unique=True, comment='64位秘钥')
    name = Column(String(64), nullable=False, comment='秘钥名称')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        Index('idx_user_secrets_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'secret': self.secret,
            'created_at': str(self.created_at) if self.created_at else None
        }
