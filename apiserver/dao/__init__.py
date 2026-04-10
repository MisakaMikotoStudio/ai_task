#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DAO (Data Access Object) 模块
数据库访问层 - SQLAlchemy ORM
"""

from .connection import get_db_session, init_connection, remove_session
from .init_db import init_database
from .models import User, Client, Task, Product, Order, PermissionConfig, Resource
from . import product_dao, order_dao, permission_dao, resource_dao

__all__ = [
    'get_db_session',
    'init_connection',
    'remove_session',
    'init_database',
    'User',
    'Client',
    'Task',
    'Product',
    'Order',
    'PermissionConfig',
    'Resource',
    'product_dao',
    'order_dao',
    'permission_dao',
    'resource_dao',
]
