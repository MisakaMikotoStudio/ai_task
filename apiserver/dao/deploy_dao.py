#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
部署配置数据访问对象
"""

import logging
import random
from datetime import datetime, timezone
from typing import List, Optional

from .connection import get_db_session
from .models import ClientDeploy

logger = logging.getLogger(__name__)


def generate_unique_uuid(max_retries: int = 10) -> str:
    """
    生成全局唯一的6位随机数字字符串。

    Args:
        max_retries: 最大重试次数

    Returns:
        6位数字字符串（首位非0）

    Raises:
        RuntimeError: 达到最大重试次数仍未生成唯一值
    """
    for _ in range(max_retries):
        uuid_val = str(random.randint(100000, 999999))
        with get_db_session() as session:
            exists = session.query(ClientDeploy.id).filter(
                ClientDeploy.uuid == uuid_val,
                ClientDeploy.deleted_at.is_(None),
            ).first()
            if not exists:
                return uuid_val
    raise RuntimeError("无法生成唯一的6位数字标识，请稍后重试")


def get_deploys_by_client(client_id: int, user_id: int) -> List[ClientDeploy]:
    """获取客户端下所有未删除的部署配置"""
    with get_db_session() as session:
        return session.query(ClientDeploy).filter(
            ClientDeploy.client_id == client_id,
            ClientDeploy.user_id == user_id,
            ClientDeploy.deleted_at.is_(None),
        ).order_by(ClientDeploy.id.asc()).all()


def get_deploy_by_id(deploy_id: int, user_id: int) -> Optional[ClientDeploy]:
    """根据ID获取单个部署配置"""
    with get_db_session() as session:
        return session.query(ClientDeploy).filter(
            ClientDeploy.id == deploy_id,
            ClientDeploy.user_id == user_id,
            ClientDeploy.deleted_at.is_(None),
        ).first()


def create_deploy(user_id: int, client_id: int, startup_command: str, official_configs: list, custom_config: str = '') -> int:
    """
    创建部署配置

    Returns:
        新建记录的 ID
    """
    uuid_val = generate_unique_uuid()
    with get_db_session() as session:
        deploy = ClientDeploy(
            user_id=user_id,
            client_id=client_id,
            uuid=uuid_val,
            startup_command=startup_command,
            official_configs=official_configs,
            custom_config=custom_config,
        )
        session.add(deploy)
        session.flush()
        return deploy.id


def update_deploy(deploy_id: int, user_id: int, startup_command: str, official_configs: list, custom_config: str = '') -> bool:
    """
    更新部署配置（不更新 uuid）

    Returns:
        是否成功更新
    """
    with get_db_session() as session:
        deploy = session.query(ClientDeploy).filter(
            ClientDeploy.id == deploy_id,
            ClientDeploy.user_id == user_id,
            ClientDeploy.deleted_at.is_(None),
        ).first()
        if not deploy:
            return False
        deploy.startup_command = startup_command
        deploy.official_configs = official_configs
        deploy.custom_config = custom_config
        return True


def soft_delete_deploy(deploy_id: int, user_id: int) -> bool:
    """软删除部署配置"""
    with get_db_session() as session:
        deploy = session.query(ClientDeploy).filter(
            ClientDeploy.id == deploy_id,
            ClientDeploy.user_id == user_id,
            ClientDeploy.deleted_at.is_(None),
        ).first()
        if not deploy:
            return False
        deploy.deleted_at = datetime.now(timezone.utc)
        return True


def apply_deploy_sync(client_id: int, user_id: int, delete_ids: List[int], updates: List[dict], inserts: List[dict]):
    """
    批量同步部署配置：删除、更新、新增。

    Args:
        client_id: 客户端ID
        user_id: 用户ID
        delete_ids: 需要软删除的记录ID列表
        updates: 需要更新的记录 [{"id": ..., "startup_command": ..., "official_configs": ..., "custom_config": ...}]
        inserts: 需要新增的记录 [{"startup_command": ..., "official_configs": ..., "custom_config": ...}]
    """
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        if delete_ids:
            session.query(ClientDeploy).filter(
                ClientDeploy.id.in_(delete_ids),
                ClientDeploy.user_id == user_id,
                ClientDeploy.deleted_at.is_(None),
            ).update({ClientDeploy.deleted_at: now}, synchronize_session='fetch')

        for item in updates:
            session.query(ClientDeploy).filter(
                ClientDeploy.id == item['id'],
                ClientDeploy.user_id == user_id,
                ClientDeploy.deleted_at.is_(None),
            ).update({
                ClientDeploy.startup_command: item.get('startup_command', ''),
                ClientDeploy.official_configs: item.get('official_configs', []),
                ClientDeploy.custom_config: item.get('custom_config', ''),
            }, synchronize_session='fetch')

        for item in inserts:
            uuid_val = generate_unique_uuid()
            deploy = ClientDeploy(
                user_id=user_id,
                client_id=client_id,
                uuid=uuid_val,
                startup_command=item.get('startup_command', ''),
                official_configs=item.get('official_configs', []),
                custom_config=item.get('custom_config', ''),
            )
            session.add(deploy)
