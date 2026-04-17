#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SQLAlchemy ORM 模型定义
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, JSON, Index, func, BigInteger, Text, Date, Boolean, Numeric
from decimal import Decimal
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


def to_iso_utc(dt: datetime):
    """统一将 datetime 序列化为 UTC ISO8601 字符串。"""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


class User(Base):
    """用户表"""
    __tablename__ = 'ai_task_users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, unique=True, comment='对外用户编号（6位整数，首位非0），业务表 user_id 均引用此列')
    name = Column(String(64), nullable=False, comment='用户名')
    password_hash = Column(String(256), nullable=False, comment='密码哈希')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')
    last_access_at = Column(DateTime, nullable=True, comment='最后访问时间')


    def to_dict(self):
        return {
            'user_id': self.user_id,
            'name': self.name,
            'created_at': to_iso_utc(self.created_at),
            'last_access_at': to_iso_utc(self.last_access_at)
        }


    __table_args__ = (
        Index('uk_users_name', 'name', unique=True),
        Index('uk_users_user_id', 'user_id', unique=True),
    )


class UserSession(Base):
    """用户会话表"""
    __tablename__ = 'ai_task_user_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    token = Column(String(255), nullable=False, comment='Token')
    expires_at = Column(DateTime, nullable=False, comment='过期时间')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')

    __table_args__ = (
        Index('idx_user_sessions_user_id', 'user_id'),
        Index('uk_user_sessions_token', 'token', unique=True),
    )


class Client(Base):
    """客户端表"""
    __tablename__ = 'ai_task_clients'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='所属用户ID')
    name = Column(String(32), nullable=False, comment='客户端名称')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')
    last_sync_at = Column(DateTime, nullable=True, comment='最后心跳时间')
    instance_uuid = Column(String(36), nullable=True, unique=True, comment='当前运行实例的唯一标识UUID')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    agent = Column(String(64), nullable=True, default='claude sdk', comment='Agent类型')
    official_cloud_deploy = Column(Integer, nullable=False, default=0, server_default='0', comment='官方云部署：0否 1是')
    version = Column(Integer, nullable=False, default=1, server_default='1', comment='客户端配置版本（用于触发启动器重建容器）')

    __table_args__ = (
        Index('uk_user_client', 'user_id', 'name', unique=True),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
            'last_sync_at': to_iso_utc(self.last_sync_at),
            'agent': self.agent or 'claude sdk',
            'official_cloud_deploy': self.official_cloud_deploy if self.official_cloud_deploy is not None else 0,
            'version': self.version or 0,
        }


class ClientHeartbeat(Base):
    """客户端心跳记录表"""
    __tablename__ = 'ai_task_client_heartbeats'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='客户端ID')
    instance_uuid = Column(String(64), nullable=False, comment='客户端实例UUID')
    last_sync_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='最近同步时间')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')

    __table_args__ = (
        Index('uk_user_client_unique', 'user_id', 'client_id', unique=True),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'client_id': self.client_id,
            'instance_uuid': self.instance_uuid,
            'last_sync_at': to_iso_utc(self.last_sync_at),
            'created_at': to_iso_utc(self.created_at)
        }


class Task(Base):
    """任务表"""
    __tablename__ = 'ai_task_tasks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='所属用户ID')
    title = Column(String(45), nullable=False, default='', comment='任务标题')
    status = Column(String(20), default='pending', nullable=False, comment='任务状态')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    extra = Column(Text, nullable=True, comment='附加信息')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
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
            'title': self.title or '',
            'status': self.status,
            'status_text': self.STATUS_TEXT.get(self.status, self.status),
            'client_id': self.client_id,
            'client_name': None,  # 需要单独查询
            'extra': self.extra or '',
            'deleted_at': to_iso_utc(self.deleted_at),
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class Objective(Base):
    """OKR目标表"""
    __tablename__ = 'ai_task_okr_objectives'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, comment='所属用户')
    title = Column(String(255), nullable=False, comment='目标标题')
    description = Column(Text, nullable=True, comment='目标描述')
    status = Column(String(32), nullable=False, default='draft', comment='状态：draft/active/completed/archived')
    sort_order = Column(Integer, nullable=False, default=0, comment='排序顺序')
    cycle_type = Column(String(16), nullable=False, default='quarter', comment='周期类型：week/month/quarter')
    cycle_start = Column(Date, nullable=True, comment='周期开始日期')
    cycle_end = Column(Date, nullable=True, comment='周期结束日期')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')

    __table_args__ = (
        Index('idx_objectives_user_id', 'user_id'),
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
            'sort_order': self.sort_order,
            'cycle_type': self.cycle_type,
            'cycle_start': str(self.cycle_start) if self.cycle_start else None,
            'cycle_end': str(self.cycle_end) if self.cycle_end else None,
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
            'deleted_at': to_iso_utc(self.deleted_at)
        }


class KeyResult(Base):
    """OKR关键结果表"""
    __tablename__ = 'ai_task_okr_key_results'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, comment='所属用户')
    objective_id = Column(BigInteger, nullable=False, comment='关联的目标ID')
    title = Column(String(255), nullable=False, comment='KR标题')
    description = Column(Text, nullable=True, comment='KR描述')
    sort_order = Column(Integer, nullable=False, default=0, comment='排序顺序')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')

    __table_args__ = (
        Index('idx_key_results', 'user_id', 'objective_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'objective_id': self.objective_id,
            'title': self.title,
            'description': self.description or '',
            'sort_order': self.sort_order,
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
            'deleted_at': to_iso_utc(self.deleted_at)
        }


class TodoItem(Base):
    """待办事项表"""
    __tablename__ = 'ai_task_todos'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    content = Column(String(500), nullable=False, comment='待办内容')
    completed = Column(Boolean, default=False, comment='是否完成')
    sort_order = Column(Integer, default=0, comment='排序')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')

    __table_args__ = (
        Index('idx_todos_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'completed': self.completed,
            'sort_order': self.sort_order,
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
            'deleted_at': to_iso_utc(self.deleted_at)
        }


class ClientRepo(Base):
    """客户端仓库配置表"""
    __tablename__ = 'ai_task_client_repos'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    desc = Column(String(255), nullable=False, comment='仓库简介')
    url = Column(String(512), nullable=False, comment='仓库URL')
    token = Column(String(255), nullable=True, comment='访问token')
    default_branch = Column(String(64), nullable=True, default='', comment='默认分支')
    branch_prefix = Column(String(64), nullable=False, default='ai_', comment='代码分支前缀')
    docs_repo = Column(Boolean, nullable=False, default=False, comment='是否为文档仓库')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_client_repos_user_id', 'user_id'),
        Index('idx_client_repos_user_id_client_id', 'user_id', 'client_id'),
        Index('idx_client_repos_client_id', 'client_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'client_id': self.client_id,
            'desc': self.desc,
            'url': self.url,
            'token': self.token,
            'default_branch': self.default_branch or '',
            'branch_prefix': self.branch_prefix or 'ai_',
            'docs_repo': self.docs_repo or False,
            'deleted_at': to_iso_utc(self.deleted_at),
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class ClientEnvVar(Base):
    """客户端环境变量配置表"""
    __tablename__ = 'ai_task_client_env_vars'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    key = Column(String(128), nullable=False, comment='环境变量名')
    value = Column(Text, nullable=True, comment='环境变量值')
    env = Column(String(16), nullable=True, default=None, comment='环境标识：test/prod，NULL表示通用')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_user_id_client_id', 'user_id', 'client_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'key': self.key,
            'value': self.value or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class UserSecret(Base):
    """用户秘钥表"""
    __tablename__ = 'ai_task_user_secrets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    secret = Column(String(64), nullable=False, unique=True, comment='64位秘钥')
    name = Column(String(64), nullable=False, comment='秘钥名称')
    type = Column(String(16), nullable=False, default='personal', comment='秘钥类型：cloud-官方云客户端专用，personal-用户自建')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    last_used_at = Column(DateTime, nullable=True, comment='最近使用时间')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_user_secrets_user_id', 'user_id'),
        Index('idx_user_secrets_secret_id', 'secret', unique=True),
    )

    TYPE_CLOUD = 'cloud'
    TYPE_PERSONAL = 'personal'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'secret': self.secret,
            'type': self.type or 'personal',
            'last_used_at': to_iso_utc(self.last_used_at),
            'created_at': to_iso_utc(self.created_at)
        }


class Chat(Base):
    """任务Chat列表表"""
    __tablename__ = 'ai_task_chat'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='所属用户ID')
    task_id = Column(Integer, nullable=False, comment='关联任务ID')
    client_id = Column(Integer, nullable=True, default=None, comment='关联客户端ID（task_id=0时使用）')
    title = Column(String(32), nullable=False, comment='Chat标题')
    status = Column(String(20), nullable=False, default='completed', comment='Chat状态')
    sessionid = Column(String(64), nullable=True, comment='会话ID')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_chat_task', 'user_id', 'task_id'),
    )

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_TERMINATED = 'terminated'

    STATUS_TEXT = {
        'pending': '等待执行',
        'running': '正在执行',
        'completed': '执行完成',
        'terminated': '被终止'
    }

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'client_id': self.client_id,
            'title': self.title,
            'status': self.status,
            'status_text': self.STATUS_TEXT.get(self.status, self.status),
            'sessionid': self.sessionid or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class Product(Base):
    """商品表"""
    __tablename__ = 'ai_task_shop_products'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    key = Column(String(64), nullable=False, comment='商品唯一 key')
    title = Column(String(128), nullable=False, comment='商品名称')
    desc = Column(Text, nullable=True, comment='商品描述（纯文本，支持换行）')
    price = Column(Numeric(10, 2), nullable=False, comment='价格（元）')
    expire_time = Column(Integer, nullable=True, comment='购买后有效时长（秒），NULL 表示永久')
    support_continue = Column(Boolean, nullable=False, default=False, comment='是否支持续费')
    icon = Column(String(512), nullable=True, comment='商品封面图 URL')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间（软删除）')

    __table_args__ = (
        Index('uk_shop_products_key', 'key', unique=True),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'title': self.title,
            'desc': self.desc or '',
            'price': float(self.price) if self.price is not None else 0.0,
            'expire_time': self.expire_time,
            'support_continue': bool(self.support_continue),
            'icon': self.icon or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
        }


class Order(Base):
    """订单表"""
    __tablename__ = 'ai_task_shop_orders'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, comment='用户ID')
    product_id = Column(BigInteger, nullable=False, comment='商品ID')
    product_key = Column(String(64), nullable=False, comment='商品 key（冗余）')
    out_trade_no = Column(String(64), nullable=False, comment='商户订单号（唯一）')
    trade_no = Column(String(64), nullable=True, comment='第三方平台交易号')
    status = Column(String(20), nullable=False, default='pending', comment='订单状态')
    amount = Column(Numeric(10, 2), nullable=False, comment='实付金额（元）')
    order_type = Column(String(16), nullable=False, default='purchase', comment='purchase/renew')
    expire_at = Column(DateTime, nullable=True, comment='权益到期时间')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    STATUS_PENDING = 'pending'
    STATUS_PAID = 'paid'
    STATUS_FAILED = 'failed'
    STATUS_REFUNDED = 'refunded'

    __table_args__ = (
        Index('uk_shop_orders_out_trade_no', 'out_trade_no', unique=True),
        Index('idx_shop_orders_user_id', 'user_id'),
        Index('idx_shop_orders_status', 'status'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'product_id': self.product_id,
            'product_key': self.product_key,
            'out_trade_no': self.out_trade_no,
            'trade_no': self.trade_no or '',
            'status': self.status,
            'amount': float(self.amount) if self.amount is not None else 0.0,
            'order_type': self.order_type,
            'expire_at': to_iso_utc(self.expire_at),
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
        }


class ClientServer(Base):
    """客户端云服务器配置表"""
    __tablename__ = 'ai_task_client_servers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    env = Column(String(16), nullable=False, comment='环境标识：test/prod')
    name = Column(String(128), nullable=False, default='', comment='登录账号')
    password = Column(String(256), nullable=True, comment='登录密码')
    ip = Column(String(64), nullable=False, default='', comment='云服务器IP')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_client_servers_client_env', 'client_id', 'env'),
        Index('idx_client_servers_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'env': self.env,
            'name': self.name or '',
            'password': self.password or '',
            'ip': self.ip or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class ClientDomain(Base):
    """客户端域名配置表"""
    __tablename__ = 'ai_task_client_domains'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    env = Column(String(16), nullable=False, comment='环境标识：test/prod')
    domain = Column(String(256), nullable=False, default='', comment='域名')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_client_domains_client_env', 'client_id', 'env'),
        Index('idx_client_domains_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'env': self.env,
            'domain': self.domain or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class ClientDatabase(Base):
    """客户端数据库配置表"""
    __tablename__ = 'ai_task_client_databases'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    env = Column(String(16), nullable=False, comment='环境标识：test/prod')
    db_type = Column(String(32), nullable=False, default='mysql', comment='数据库类型：mysql')
    host = Column(String(256), nullable=False, default='', comment='数据库地址')
    port = Column(Integer, nullable=False, default=3306, comment='数据库端口')
    username = Column(String(128), nullable=False, default='', comment='用户名')
    password = Column(String(256), nullable=True, comment='密码')
    db_name = Column(String(128), nullable=False, default='', comment='数据库名称')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_client_databases_client_env', 'client_id', 'env'),
        Index('idx_client_databases_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'env': self.env,
            'db_type': self.db_type or 'mysql',
            'host': self.host or '',
            'port': self.port or 3306,
            'username': self.username or '',
            'password': self.password or '',
            'db_name': self.db_name or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class ClientPayment(Base):
    """客户端支付配置表"""
    __tablename__ = 'ai_task_client_payments'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    env = Column(String(16), nullable=False, comment='环境标识：test/prod')
    payment_type = Column(String(32), nullable=False, default='alipay', comment='支付类型：alipay')
    appid = Column(String(64), nullable=True, comment='支付宝应用ID')
    app_private_key = Column(Text, nullable=True, comment='应用RSA2私钥')
    alipay_public_key = Column(Text, nullable=True, comment='支付宝公钥')
    notify_url = Column(String(512), nullable=True, comment='异步通知回调URL')
    return_url = Column(String(512), nullable=True, comment='支付完成后同步跳转URL')
    gateway = Column(String(256), nullable=True, comment='支付宝网关')
    app_encrypt_key = Column(String(256), nullable=True, comment='接口内容AES加密密钥')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_client_payments_client_env', 'client_id', 'env'),
        Index('idx_client_payments_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'env': self.env,
            'payment_type': self.payment_type or 'alipay',
            'appid': self.appid or '',
            'app_private_key': self.app_private_key or '',
            'alipay_public_key': self.alipay_public_key or '',
            'notify_url': self.notify_url or '',
            'return_url': self.return_url or '',
            'gateway': self.gateway or '',
            'app_encrypt_key': self.app_encrypt_key or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class ClientOss(Base):
    """客户端对象存储配置表"""
    __tablename__ = 'ai_task_client_oss'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    env = Column(String(16), nullable=False, comment='环境标识：test/prod')
    oss_type = Column(String(32), nullable=False, default='cos', comment='存储类型：cos（腾讯云）')
    secret_id = Column(String(256), nullable=True, comment='SecretId')
    secret_key = Column(String(256), nullable=True, comment='SecretKey')
    region = Column(String(64), nullable=True, comment='地域')
    bucket = Column(String(128), nullable=True, comment='Bucket名称')
    base_url = Column(String(512), nullable=True, comment='公开访问域名前缀')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_client_oss_client_env', 'client_id', 'env'),
        Index('idx_client_oss_user_id', 'user_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'env': self.env,
            'oss_type': self.oss_type or 'cos',
            'secret_id': self.secret_id or '',
            'secret_key': self.secret_key or '',
            'region': self.region or '',
            'bucket': self.bucket or '',
            'base_url': self.base_url or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }


class ClientDeploy(Base):
    """客户端部署配置表（不区分环境）"""
    __tablename__ = 'ai_task_client_deploys'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    uuid = Column(String(6), nullable=False, unique=True, comment='6位随机数字唯一标识')
    repo_id = Column(Integer, nullable=True, comment='关联仓库ID（ai_task_client_repos.id）')
    work_dir = Column(String(512), nullable=True, default='', comment='工作目录路径，启动命令在此目录下运行')
    route_prefix = Column(String(128), nullable=False, default='', comment='生产 nginx 路径前缀；空或/表示根；如/api 将 /api/… 反代并去掉前缀后转发到容器:8080')
    startup_command = Column(Text, nullable=False, default='', comment='启动命令')
    official_configs = Column(JSON, nullable=False, comment='官方配置选项列表，如 ["app_name","domain","database","payment","oss"]')
    custom_config = Column(Text, nullable=True, comment='用户自定义 TOML 配置')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    VALID_OFFICIAL_CONFIGS = ['app_name', 'domain', 'database', 'payment', 'oss']

    __table_args__ = (
        Index('idx_client_deploys_client', 'client_id'),
        Index('idx_client_deploys_user', 'user_id'),
        Index('uk_client_deploys_uuid', 'uuid', unique=True),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'uuid': self.uuid or '',
            'repo_id': self.repo_id,
            'work_dir': self.work_dir or '',
            'route_prefix': self.route_prefix or '',
            'startup_command': self.startup_command or '',
            'official_configs': self.official_configs or [],
            'custom_config': self.custom_config or '',
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
        }


class PermissionConfig(Base):
    """权限配置表"""
    __tablename__ = 'ai_task_permission_configs'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    key = Column(String(64), nullable=False, comment='权限 key（如 subscribed, official_cloud_client_count）')
    type = Column(String(32), nullable=False, comment='权限校验类型：count_limit / subscribed')
    product_key = Column(String(64), nullable=False, comment='关联的产品 key')
    config_detail = Column(JSON, nullable=True, comment='具体权限配置（如 {"limit": 5}）')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间（软删除）')

    TYPE_COUNT_LIMIT = 'count_limit'
    TYPE_SUBSCRIBED = 'subscribed'
    VALID_TYPES = [TYPE_COUNT_LIMIT, TYPE_SUBSCRIBED]

    __table_args__ = (
        Index('idx_permission_configs_key', 'key'),
        Index('idx_permission_configs_key_product', 'key', 'product_key'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.key,
            'type': self.type,
            'product_key': self.product_key,
            'config_detail': self.config_detail or {},
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
        }


class Resource(Base):
    """资源表（admin 管理）"""
    __tablename__ = 'ai_task_resources'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, server_default='', comment='资源名称，唯一标识')
    type = Column(String(32), nullable=False, comment='资源类型：mysql/code_repo/cloud_server')
    source = Column(String(32), nullable=False, comment='资源来源：aliyun/github/tencent_cloud')
    envs = Column(JSON, nullable=False, comment='可用环境列表，如 ["test","prod"]')
    extra = Column(JSON, nullable=True, comment='补充详细信息（不同 type+source 对应不同字段）')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')
    deleted_at = Column(DateTime, nullable=True, comment='下架时间，NULL 表示上架中')

    # 类型常量
    TYPE_MYSQL = 'mysql'
    TYPE_CODE_REPO = 'code_repo'
    TYPE_CLOUD_SERVER = 'cloud_server'
    VALID_TYPES = [TYPE_MYSQL, TYPE_CODE_REPO, TYPE_CLOUD_SERVER]

    # 来源常量
    SOURCE_ALIYUN = 'aliyun'
    SOURCE_GITHUB = 'github'
    SOURCE_TENCENT_CLOUD = 'tencent_cloud'
    VALID_SOURCES = [SOURCE_ALIYUN, SOURCE_GITHUB, SOURCE_TENCENT_CLOUD]

    # 环境常量
    VALID_ENVS = ['test', 'prod']

    __table_args__ = (
        Index('idx_resources_type_source', 'type', 'source'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name or '',
            'type': self.type,
            'source': self.source,
            'envs': self.envs or [],
            'extra': self._safe_extra(),
            'is_online': self.deleted_at is None,
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
        }

    def _safe_extra(self):
        """返回 extra 时隐藏敏感字段（AccessKey Secret、private_key 等）"""
        raw = self.extra or {}
        safe = dict(raw)
        for key in ('access_key_secret', 'private_key', 'login_password'):
            if key in safe:
                val = safe[key]
                if val and len(val) > 6:
                    safe[key] = val[:3] + '***' + val[-3:]
                else:
                    safe[key] = '***'
        return safe

    def get_raw_extra(self):
        """返回原始 extra（含完整敏感字段，仅服务层使用）"""
        return self.extra or {}


class DeployRecord(Base):
    """发布记录表"""
    __tablename__ = 'ai_task_deploy_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='所属用户ID')
    client_id = Column(Integer, nullable=False, comment='关联客户端ID')
    msg_id = Column(Integer, nullable=False, default=0, server_default='0', comment='关联 chat 消息ID，0 表示未关联')
    env = Column(String(16), nullable=False, comment='环境标识：test/prod')
    description = Column(String(255), nullable=False, default='', comment='发布描述')
    status = Column(String(20), nullable=False, default='pending', comment='发布状态：pending/publishing/failed/success/cancel')
    detail = Column(JSON, default=dict, comment='发布详情（task_id, chat_id, msg_id 等）')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_deploy_records_user_client', 'user_id', 'client_id'),
        Index('idx_deploy_records_client_env', 'client_id', 'env'),
        Index('idx_deploy_records_client_msg', 'client_id', 'msg_id'),
    )

    STATUS_PENDING = 'pending'
    STATUS_PUBLISHING = 'publishing'
    STATUS_FAILED = 'failed'
    STATUS_SUCCESS = 'success'
    STATUS_CANCEL = 'cancel'

    VALID_STATUSES = [STATUS_PENDING, STATUS_PUBLISHING, STATUS_FAILED, STATUS_SUCCESS, STATUS_CANCEL]
    VALID_ENVS = ['test', 'prod']

    STATUS_TEXT = {
        'pending': '等待发布',
        'publishing': '发布中',
        'failed': '失败',
        'success': '成功',
        'cancel': '取消',
    }

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'msg_id': self.msg_id or 0,
            'env': self.env,
            'description': self.description or '',
            'status': self.status,
            'status_text': self.STATUS_TEXT.get(self.status, self.status),
            'detail': self.detail or {},
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at),
        }


class ChatMessage(Base):
    """Chat消息表"""
    __tablename__ = 'ai_task_chat_message'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, comment='所属用户ID')
    task_id = Column(Integer, nullable=False, comment='关联任务ID')
    chat_id = Column(Integer, nullable=False, comment='关联Chat ID')
    status = Column(String(64), nullable=False, default='pending', comment='执行状态')
    input = Column(Text, nullable=True, comment='用户输入')
    output = Column(Text, nullable=True, comment='Agent输出')
    extra = Column(JSON, default=dict, comment='附加信息')
    deleted_at = Column(DateTime, nullable=True, comment='删除时间，不为空表示已删除')
    created_at = Column(DateTime, server_default=func.utc_timestamp(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.utc_timestamp(), onupdate=func.utc_timestamp(), comment='更新时间')

    __table_args__ = (
        Index('idx_chat_message_user_id', 'user_id', 'task_id', 'chat_id'),
    )

    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_COMPLETED = 'completed'
    STATUS_TERMINATED = 'terminated'

    STATUS_TEXT = {
        'pending': '等待执行',
        'running': '正在执行',
        'completed': '执行完成',
        'terminated': '被终止'
    }

    def to_dict(self):
        return {
            'id': self.id,
            'task_id': self.task_id,
            'chat_id': self.chat_id,
            'status': self.status,
            'status_text': self.STATUS_TEXT.get(self.status, self.status),
            'input': self.input or '',
            'output': self.output or '',
            'extra': self.extra or {},
            'created_at': to_iso_utc(self.created_at),
            'updated_at': to_iso_utc(self.updated_at)
        }
