#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MySQL 资源服务层 —— 通过阿里云 RDS OpenAPI 统一处理 MySQL 资源操作

功能：
- 查询用户当前环境下有多少个数据库
- 为用户创建数据库，并生成仅对该数据库具有 admin 权限的账号 + 密码
"""

import logging
import secrets
import string
from typing import Dict, List

from dao.models import Resource

logger = logging.getLogger(__name__)

# 缓存：连接地址 → 实例 ID，避免每次都搜索全地域
_instance_id_cache: Dict[str, str] = {}


class ResourceMySQLError(Exception):
    """MySQL 资源操作失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _resolve_instance_id(client, connection_url: str) -> str:
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
            req = rds_models.DescribeDBInstancesRequest(
                region_id=region,
                page_size=100,
            )
            resp = client.describe_dbinstances(req)
            instances = resp.body.items.dbinstance or []
            for inst in instances:
                # 检查连接地址是否匹配：实例的连接地址是 {instance_id}.mysql.rds.aliyuncs.com
                # 而资源配置中可能是 {instance_id}{suffix}.mysql.rds.aliyuncs.com
                if connection_url.startswith(inst.dbinstance_id):
                    logger.info(
                        "_resolve_instance_id: found instance_id=%s in region=%s for url=%s",
                        inst.dbinstance_id, region, connection_url,
                    )
                    _instance_id_cache[connection_url] = inst.dbinstance_id
                    return inst.dbinstance_id
        except Exception as e:
            logger.warning(
                "_resolve_instance_id: failed to search region=%s, error=%s",
                region, str(e)[:100],
            )
            continue

    raise ResourceMySQLError(
        f"无法从连接地址 {connection_url} 找到对应的 RDS 实例，请检查资源配置"
    )


def _build_rds_client(resource: Resource):
    """
    根据资源的 extra 信息构建阿里云 RDS 客户端

    Args:
        resource: Resource 对象（type=mysql, source=aliyun）

    Returns:
        (client, instance_id) 元组
    """
    try:
        from alibabacloud_rds20140815.client import Client
        from alibabacloud_tea_openapi.models import Config
    except ImportError:
        raise ResourceMySQLError("阿里云 RDS SDK 未安装，请联系管理员")

    extra = resource.get_raw_extra()
    access_key_id = extra.get('access_key_id', '').strip()
    access_key_secret = extra.get('access_key_secret', '').strip()
    url = extra.get('url', '').strip()

    if not access_key_id or not access_key_secret:
        raise ResourceMySQLError("资源缺少 AccessKey ID 或 AccessKey Secret")
    if not url:
        raise ResourceMySQLError("资源缺少数据库实例地址 (url)")

    # 使用全局 endpoint，通过 API 反查真实实例 ID
    endpoint = "rds.aliyuncs.com"

    config = Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        endpoint=endpoint,
    )
    client = Client(config)

    # 从连接地址反查真实实例 ID（连接地址可能包含 VPC 等后缀如 'vo'）
    instance_id = _resolve_instance_id(client=client, connection_url=url)

    return client, instance_id


def _generate_password(length: int = 16) -> str:
    """
    生成满足阿里云复杂度要求的密码：
    大小写字母 + 数字 + 特殊字符，至少 8 位
    """
    if length < 8:
        length = 8
    # 确保至少包含每种字符各一个
    password_chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice('!@#$%^&*'),
    ]
    remaining = length - len(password_chars)
    all_chars = string.ascii_letters + string.digits + '!@#$%^&*'
    password_chars.extend(secrets.choice(all_chars) for _ in range(remaining))
    # 打乱顺序
    result = list(password_chars)
    secrets_gen = secrets.SystemRandom()
    secrets_gen.shuffle(result)
    return ''.join(result)


def list_databases(resource: Resource, user_id: int) -> List[Dict]:
    """
    查询用户在指定资源上的数据库列表

    数据库命名规则：以 {user_id}_ 为前缀的数据库视为该用户的数据库

    Args:
        resource: Resource 对象
        user_id: 用户 ID

    Returns:
        用户数据库列表 [{"db_name": ..., "db_status": ...}, ...]
    """
    from alibabacloud_rds20140815 import models as rds_models

    client, instance_id = _build_rds_client(resource=resource)
    prefix = f"{user_id}_"

    try:
        request = rds_models.DescribeDatabasesRequest(
            dbinstance_id=instance_id,
        )
        resp = client.describe_databases(request)
        databases = resp.body.databases.database or []

        user_dbs = []
        for db in databases:
            if db.dbname and db.dbname.startswith(prefix):
                user_dbs.append({
                    'db_name': db.dbname,
                    'db_status': db.dbstatus,
                })

        logger.info(
            "list_databases: resource_id=%s, user_id=%s, total_dbs=%d, user_dbs=%d",
            resource.id, user_id, len(databases), len(user_dbs),
        )
        return user_dbs
    except ResourceMySQLError:
        raise
    except Exception as e:
        logger.error(
            "list_databases failed: resource_id=%s, user_id=%s, error=%s",
            resource.id, user_id, str(e),
        )
        raise ResourceMySQLError(f"查询数据库列表失败：{str(e)}")


def create_database_with_name(resource: Resource, user_id: int, db_name: str) -> Dict:
    """
    在指定资源上创建一个指定名称的数据库，并生成仅对该数据库具有读写权限的账号

    Args:
        resource: Resource 对象
        user_id: 用户 ID
        db_name: 数据库名称（由调用方生成）

    Returns:
        {
            "db_name": "...",
            "account_name": "...",
            "account_password": "...",
            "instance_url": "...",
            "port": 3306,
        }
    """
    from alibabacloud_rds20140815 import models as rds_models

    client, instance_id = _build_rds_client(resource=resource)
    extra = resource.get_raw_extra()
    instance_url = extra.get('url', '')
    port = int(extra.get('port', 3306))

    try:
        # 1. 创建数据库
        create_db_req = rds_models.CreateDatabaseRequest(
            dbinstance_id=instance_id,
            dbname=db_name,
            character_set_name="utf8mb4",
        )
        client.create_database(create_db_req)
        logger.info(
            "create_database_with_name: resource_id=%s, user_id=%s, db_name=%s",
            resource.id, user_id, db_name,
        )

        # 2. 创建账户（账号名由 db_name 派生，截断到 32 字符以内）
        account_name = f"u_{db_name}"
        if len(account_name) > 32:
            account_name = account_name[:32]
        account_password = _generate_password(length=16)

        create_account_req = rds_models.CreateAccountRequest(
            dbinstance_id=instance_id,
            account_name=account_name,
            account_password=account_password,
            account_type="Normal",
        )
        client.create_account(create_account_req)
        logger.info(
            "create_account: resource_id=%s, user_id=%s, account=%s",
            resource.id, user_id, account_name,
        )

        # 3. 授权账户对数据库的权限
        grant_req = rds_models.GrantAccountPrivilegeRequest(
            dbinstance_id=instance_id,
            account_name=account_name,
            dbname=db_name,
            account_privilege="ReadWrite",
        )
        client.grant_account_privilege(grant_req)
        logger.info(
            "grant_privilege: resource_id=%s, account=%s, db=%s, privilege=ReadWrite",
            resource.id, account_name, db_name,
        )

        return {
            'db_name': db_name,
            'account_name': account_name,
            'account_password': account_password,
            'instance_url': instance_url,
            'port': port,
        }

    except ResourceMySQLError:
        raise
    except Exception as e:
        logger.error(
            "create_database_with_name failed: resource_id=%s, user_id=%s, db_name=%s, error=%s",
            resource.id, user_id, db_name, str(e),
        )
        raise ResourceMySQLError(f"创建数据库失败：{str(e)}")


def create_database_for_user(resource: Resource, user_id: int) -> Dict:
    """
    为用户创建数据库，并生成仅对该数据库具有 admin 权限的账号和密码

    数据库命名规则：{user_id}_app_{version}，version 从 1 开始递增
    账号命名规则：u{user_id}_{version}

    Args:
        resource: Resource 对象
        user_id: 用户 ID

    Returns:
        {
            "db_name": "...",
            "account_name": "...",
            "account_password": "...",
            "instance_url": "...",
        }
    """
    from alibabacloud_rds20140815 import models as rds_models

    client, instance_id = _build_rds_client(resource=resource)
    extra = resource.get_raw_extra()
    instance_url = extra.get('url', '')

    try:
        # 1. 查询已有数据库，确定可用的 version
        request = rds_models.DescribeDatabasesRequest(
            dbinstance_id=instance_id,
        )
        resp = client.describe_databases(request)
        existing_dbs = {db.dbname for db in (resp.body.databases.database or [])}

        version = 1
        while True:
            db_name = f"{user_id}_app_{version}"
            if db_name not in existing_dbs:
                break
            version += 1
            if version > 9999:
                raise ResourceMySQLError("数据库名称生成失败：版本号超出上限")

        # 2. 创建数据库
        create_db_req = rds_models.CreateDatabaseRequest(
            dbinstance_id=instance_id,
            dbname=db_name,
            character_set_name="utf8mb4",
        )
        client.create_database(create_db_req)
        logger.info(
            "create_database: resource_id=%s, user_id=%s, db_name=%s",
            resource.id, user_id, db_name,
        )

        # 3. 创建账户
        account_name = f"u{user_id}_{version}"
        # 阿里云账号名长度限制（2~32 字符）
        if len(account_name) > 32:
            account_name = account_name[:32]
        account_password = _generate_password(length=16)

        create_account_req = rds_models.CreateAccountRequest(
            dbinstance_id=instance_id,
            account_name=account_name,
            account_password=account_password,
            account_type="Normal",
        )
        client.create_account(create_account_req)
        logger.info(
            "create_account: resource_id=%s, user_id=%s, account=%s",
            resource.id, user_id, account_name,
        )

        # 4. 授权账户对数据库的权限（ReadWrite 对应普通用户最高权限）
        grant_req = rds_models.GrantAccountPrivilegeRequest(
            dbinstance_id=instance_id,
            account_name=account_name,
            dbname=db_name,
            account_privilege="ReadWrite",
        )
        client.grant_account_privilege(grant_req)
        logger.info(
            "grant_privilege: resource_id=%s, account=%s, db=%s, privilege=ReadWrite",
            resource.id, account_name, db_name,
        )

        return {
            'db_name': db_name,
            'account_name': account_name,
            'account_password': account_password,
            'instance_url': instance_url,
        }

    except ResourceMySQLError:
        raise
    except Exception as e:
        logger.error(
            "create_database_for_user failed: resource_id=%s, user_id=%s, error=%s",
            resource.id, user_id, str(e),
        )
        raise ResourceMySQLError(f"创建数据库失败：{str(e)}")
