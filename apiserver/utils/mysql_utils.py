#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MySQL（阿里云 RDS）底层工具 —— 纯 SDK 操作，不依赖业务模型

功能：
- RDS 客户端构建
- 实例 ID 反查（通过连接地址在各地域搜索）
- 数据库 / 账号 / 权限 的创建
- 密码生成
"""

import logging
import secrets
import string
import threading
from typing import Dict, List

logger = logging.getLogger(__name__)

# 缓存：连接地址 → 实例 ID，避免每次都搜索全地域
_instance_id_cache: Dict[str, str] = {}
_instance_id_cache_lock = threading.Lock()


class ResourceMySQLError(Exception):
    """MySQL 资源操作失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ──────────────────────────────────────────────────────
#  RDS 客户端与实例 ID
# ──────────────────────────────────────────────────────

def create_rds_client(access_key_id: str, access_key_secret: str, endpoint: str = "rds.aliyuncs.com"):
    """
    构建阿里云 RDS 客户端

    Args:
        access_key_id: 阿里云 AccessKey ID
        access_key_secret: 阿里云 AccessKey Secret
        endpoint: RDS 全局 endpoint

    Returns:
        阿里云 RDS Client 实例
    """
    try:
        from alibabacloud_rds20140815.client import Client
        from alibabacloud_tea_openapi.models import Config
    except ImportError:
        raise ResourceMySQLError("阿里云 RDS SDK 未安装，请联系管理员")

    config = Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        endpoint=endpoint,
    )
    return Client(config)


def resolve_instance_id(client, connection_url: str) -> str:
    """
    通过连接地址在各地域搜索真实的 RDS 实例 ID。

    阿里云 RDS 连接地址格式：{instance_id}{suffix}.mysql.rds.aliyuncs.com
    其中 suffix 可能为 'ro'（只读）、'vo'（VPC）等，不属于实例 ID。
    因此不能直接从 URL 截取，需要通过 API 反查。

    Args:
        client: 阿里云 RDS Client（全局 endpoint）
        connection_url: 资源配置中的连接地址

    Returns:
        真实的实例 ID

    Raises:
        ResourceMySQLError: 无法找到匹配的实例
    """
    from alibabacloud_rds20140815 import models as rds_models

    # 先查缓存
    with _instance_id_cache_lock:
        cached = _instance_id_cache.get(connection_url)
    if cached:
        return cached

    # 搜索的地域列表（覆盖国内主要地域）
    regions = [
        'cn-hangzhou', 'cn-shanghai', 'cn-shenzhen', 'cn-beijing',
        'cn-qingdao', 'cn-zhangjiakou', 'cn-huhehaote', 'cn-chengdu',
        'cn-guangzhou', 'cn-wulanchabu', 'cn-nanjing', 'cn-fuzhou',
        'cn-hongkong',
    ]

    for region in regions:
        try:
            req = rds_models.DescribeDBInstancesRequest(region_id=region, page_size=100)
            resp = client.describe_dbinstances(req)
            instances = resp.body.items.dbinstance or []
            for inst in instances:
                if connection_url.startswith(inst.dbinstance_id):
                    logger.info(
                        "resolve_instance_id: found instance_id=%s in region=%s for url=%s",
                        inst.dbinstance_id, region, connection_url,
                    )
                    with _instance_id_cache_lock:
                        _instance_id_cache[connection_url] = inst.dbinstance_id
                    return inst.dbinstance_id
        except Exception as e:
            logger.warning(
                "resolve_instance_id: failed to search region=%s, error=%s",
                region, str(e)[:100],
            )
            continue

    raise ResourceMySQLError(
        f"无法从连接地址 {connection_url} 找到对应的 RDS 实例，请检查资源配置"
    )


# ──────────────────────────────────────────────────────
#  数据库 / 账号 / 权限操作
# ──────────────────────────────────────────────────────

def describe_databases(client, instance_id: str) -> List:
    """
    查询 RDS 实例下的所有数据库列表

    Args:
        client: RDS Client
        instance_id: 实例 ID

    Returns:
        数据库列表（RDS SDK 对象列表）
    """
    from alibabacloud_rds20140815 import models as rds_models

    request = rds_models.DescribeDatabasesRequest(dbinstance_id=instance_id)
    resp = client.describe_databases(request)
    return resp.body.databases.database or []


def create_database(client, instance_id: str, db_name: str) -> None:
    """
    在 RDS 实例上创建数据库

    Args:
        client: RDS Client
        instance_id: 实例 ID
        db_name: 数据库名称
    """
    from alibabacloud_rds20140815 import models as rds_models

    req = rds_models.CreateDatabaseRequest(
        dbinstance_id=instance_id,
        dbname=db_name,
        character_set_name="utf8mb4",
    )
    client.create_database(req)


def create_account(client, instance_id: str, account_name: str, account_password: str) -> None:
    """
    在 RDS 实例上创建普通账号

    Args:
        client: RDS Client
        instance_id: 实例 ID
        account_name: 账号名
        account_password: 密码
    """
    from alibabacloud_rds20140815 import models as rds_models

    req = rds_models.CreateAccountRequest(
        dbinstance_id=instance_id,
        account_name=account_name,
        account_password=account_password,
        account_type="Normal",
    )
    client.create_account(req)


def grant_account_privilege(
    client,
    instance_id: str,
    account_name: str,
    db_name: str,
    privilege: str = "ReadWrite",
) -> None:
    """
    为 RDS 账号授予指定数据库权限

    Args:
        client: RDS Client
        instance_id: 实例 ID
        account_name: 账号名
        db_name: 数据库名
        privilege: 权限级别（默认 ReadWrite）
    """
    from alibabacloud_rds20140815 import models as rds_models

    req = rds_models.GrantAccountPrivilegeRequest(
        dbinstance_id=instance_id,
        account_name=account_name,
        dbname=db_name,
        account_privilege=privilege,
    )
    client.grant_account_privilege(req)


# ──────────────────────────────────────────────────────
#  密码工具
# ──────────────────────────────────────────────────────

def generate_password(length: int = 16) -> str:
    """
    生成满足阿里云复杂度要求的密码：
    大小写字母 + 数字 + 特殊字符，至少 8 位
    """
    if length < 8:
        length = 8
    password_chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice('!@#$%^&*'),
    ]
    remaining = length - len(password_chars)
    all_chars = string.ascii_letters + string.digits + '!@#$%^&*'
    password_chars.extend(secrets.choice(all_chars) for _ in range(remaining))
    result = list(password_chars)
    secrets_gen = secrets.SystemRandom()
    secrets_gen.shuffle(result)
    return ''.join(result)
