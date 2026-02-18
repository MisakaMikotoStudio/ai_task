#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端数据访问对象 - SQLAlchemy ORM 版本
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import or_

from .connection import get_db_session
from .models import Client, ClientRepo, ClientEnvVar, User


def create_client(user_id: int, name: str, types: List[str], is_public: bool = False, agent: str = 'cloud') -> int:
    """
    创建客户端

    Args:
        user_id: 用户ID
        name: 客户端名称
        types: 支持的任务类型列表
        is_public: 是否公开
        agent: Agent类型

    Returns:
        新创建的客户端ID
    """
    with get_db_session() as session:
        client = Client(user_id=user_id, name=name, types=types, creator_id=user_id, is_public=is_public, agent=agent)
        session.add(client)
        session.flush()
        return client.id


def get_clients_by_user(user_id: int) -> List[dict]:
    """
    获取用户可见的所有客户端（自己创建的 + 公开的）

    Args:
        user_id: 用户ID

    Returns:
        客户端字典列表（包含creator_name和editable）
    """
    with get_db_session() as session:
        # 查询自己创建的 + 其他人公开的
        clients = session.query(Client, User.name).outerjoin(
            User, Client.creator_id == User.id
        ).filter(
            Client.deleted_at.is_(None),
            or_(
                Client.user_id == user_id,
                Client.is_public == True
            )
        ).order_by(Client.created_at.desc()).all()

        result = []
        for client, creator_name in clients:
            data = client.to_dict(include_creator_name=creator_name or '')
            data['editable'] = (client.user_id == user_id)
            result.append(data)
        return result


def get_clients_paginated(
    user_id: int,
    cursor: Optional[int] = None,
    limit: int = 20,
    only_mine: bool = False
) -> dict:
    """
    获取用户可见的客户端列表（游标分页）

    Args:
        user_id: 用户ID
        cursor: 游标（上一页最后一条记录的client_id），None表示第一页
        limit: 每页数量，默认20
        only_mine: 是否只看我创建的，默认False

    Returns:
        {
            "items": [...],       # 客户端列表
            "next_cursor": int,   # 下一页游标，None表示没有更多数据
            "has_more": bool      # 是否有更多数据
        }
    """
    with get_db_session() as session:
        # 构建基础查询
        query = session.query(Client, User.name).outerjoin(
            User, Client.creator_id == User.id
        ).filter(
            Client.deleted_at.is_(None)
        )

        # 根据筛选条件过滤
        if only_mine:
            # 只看我创建的
            query = query.filter(Client.user_id == user_id)
        else:
            # 我创建的 + 公开的
            query = query.filter(
                or_(
                    Client.user_id == user_id,
                    Client.is_public == True
                )
            )

        # 按ID倒序排列（新的在前）
        query = query.order_by(Client.id.desc())

        # 应用游标条件
        if cursor is not None:
            query = query.filter(Client.id < cursor)

        # 多查一条用于判断是否有更多数据
        clients = query.limit(limit + 1).all()

        # 判断是否有更多数据
        has_more = len(clients) > limit
        if has_more:
            clients = clients[:limit]

        # 构建结果
        result = []
        for client, creator_name in clients:
            data = client.to_dict(include_creator_name=creator_name or '')
            data['editable'] = (client.user_id == user_id)
            result.append(data)

        # 计算下一页游标
        next_cursor = result[-1]['id'] if result and has_more else None

        return {
            'items': result,
            'next_cursor': next_cursor,
            'has_more': has_more
        }


def get_client_by_id(client_id: int, user_id: int) -> Optional[Client]:
    """
    获取指定客户端
    
    Args:
        client_id: 客户端ID
        user_id: 用户ID
        
    Returns:
        Client对象或None
    """
    with get_db_session() as session:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.user_id == user_id,
            Client.deleted_at.is_(None)
        ).first()
        return client


def check_client_name_exists(user_id: int, name: str) -> bool:
    """
    检查客户端名称是否已存在
    
    Args:
        user_id: 用户ID
        name: 客户端名称
        
    Returns:
        是否存在
    """
    with get_db_session() as session:
        count = session.query(Client).filter(
            Client.user_id == user_id,
            Client.name == name,
            Client.deleted_at.is_(None)
        ).count()
        return count > 0


def delete_client(client_id: int, user_id: int) -> bool:
    """
    软删除客户端
    
    Args:
        client_id: 客户端ID
        user_id: 用户ID
        
    Returns:
        是否删除成功
    """
    with get_db_session() as session:
        affected = session.query(Client).filter(
            Client.id == client_id,
            Client.user_id == user_id,
            Client.deleted_at.is_(None)
        ).update({
            Client.deleted_at: datetime.now()
        })
        return affected > 0


def update_heartbeat(client_id: int, user_id: int) -> bool:
    """
    更新客户端心跳时间（旧版，仅更新心跳时间）
    
    Args:
        client_id: 客户端ID
        user_id: 用户ID
        
    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        affected = session.query(Client).filter(
            Client.id == client_id,
            Client.user_id == user_id,
            Client.deleted_at.is_(None)
        ).update({
            Client.last_sync_at: datetime.now()
        })
        return affected > 0


def update_heartbeat_with_uuid(
    client_id: int, 
    user_id: int, 
    instance_uuid: str,
    timeout_seconds: int
) -> tuple[bool, str]:
    """
    更新客户端心跳时间（带UUID验证）
    
    Args:
        client_id: 客户端ID
        user_id: 用户ID
        instance_uuid: 客户端实例的唯一标识UUID
        timeout_seconds: 心跳超时阈值（秒）
        
    Returns:
        (是否成功, 错误信息)
        - 成功: (True, "")
        - 客户端不存在: (False, "客户端不存在")
        - 实例冲突: (False, "同一个client不能启动多个服务")
    """
    with get_db_session() as session:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.user_id == user_id,
            Client.deleted_at.is_(None)
        ).first()
        
        if not client:
            return False, "客户端不存在"
        
        now = datetime.now()
        
        # 情况1: UUID相同，直接更新心跳时间
        if client.instance_uuid == instance_uuid:
            client.last_sync_at = now
            return True, ""
        
        # 情况2: UUID不同或为空，检查心跳是否超时
        if client.instance_uuid is None or client.last_sync_at is None:
            # 首次心跳或之前没有实例，直接接管
            client.instance_uuid = instance_uuid
            client.last_sync_at = now
            return True, ""
        
        # 计算上次心跳距离现在的时间
        time_since_last_heartbeat = (now - client.last_sync_at).total_seconds()
        
        if time_since_last_heartbeat > timeout_seconds:
            # 超过阈值，允许新实例接管
            client.instance_uuid = instance_uuid
            client.last_sync_at = now
            return True, ""
        else:
            # 未超过阈值，拒绝新实例
            return False, f"同一个client不能启动多个服务/或者上一个client保活还未失效请等待{timeout_seconds - time_since_last_heartbeat}秒重试"


def update_client(
    client_id: int,
    user_id: int,
    name: str,
    types: List[str],
    is_public: bool = None,
    agent: str = None
) -> bool:
    """
    更新客户端信息

    Args:
        client_id: 客户端ID
        user_id: 用户ID
        name: 新的客户端名称
        types: 新的任务类型列表
        is_public: 是否公开
        agent: Agent类型

    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        update_data = {
            Client.name: name,
            Client.types: types
        }
        if is_public is not None:
            update_data[Client.is_public] = is_public
        if agent is not None:
            update_data[Client.agent] = agent

        affected = session.query(Client).filter(
            Client.id == client_id,
            Client.user_id == user_id,
            Client.deleted_at.is_(None)
        ).update(update_data)
        return affected > 0


def check_client_name_exists_exclude(user_id: int, name: str, exclude_id: int) -> bool:
    """
    检查客户端名称是否已存在（排除指定ID）

    Args:
        user_id: 用户ID
        name: 客户端名称
        exclude_id: 排除的客户端ID

    Returns:
        是否存在
    """
    with get_db_session() as session:
        count = session.query(Client).filter(
            Client.user_id == user_id,
            Client.name == name,
            Client.id != exclude_id,
            Client.deleted_at.is_(None)
        ).count()
        return count > 0


def get_client_repos(client_id: int) -> List[ClientRepo]:
    """获取客户端的仓库配置列表"""
    with get_db_session() as session:
        repos = session.query(ClientRepo).filter(
            ClientRepo.client_id == client_id
        ).all()
        return repos


def update_client_repos(client_id: int, repos: List[dict]) -> bool:
    """
    批量更新客户端仓库配置（全量替换）

    Args:
        client_id: 客户端ID
        repos: 仓库配置列表，每项包含 desc/url/token/default_branch/is_docs_repo

    Returns:
        是否成功
    """
    with get_db_session() as session:
        # 删除旧配置
        session.query(ClientRepo).filter(ClientRepo.client_id == client_id).delete()

        # 添加新配置
        for repo in repos:
            new_repo = ClientRepo(
                client_id=client_id,
                desc=repo.get('desc', ''),
                url=repo.get('url', ''),
                token=repo.get('token'),
                default_branch=repo.get('default_branch', ''),
                branch_prefix=repo.get('branch_prefix', 'ai_'),
                docs_repo=repo.get('docs_repo', False)
            )
            session.add(new_repo)

        return True


def get_client_by_id_no_user_check(client_id: int) -> Optional[Client]:
    """获取客户端（不校验用户）"""
    with get_db_session() as session:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.deleted_at.is_(None)
        ).first()
        return client


def get_client_with_permission(client_id: int, user_id: int) -> Optional[Client]:
    """获取客户端（校验权限：创建者或公开）"""
    with get_db_session() as session:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.deleted_at.is_(None),
            or_(
                Client.user_id == user_id,
                Client.is_public == True
            )
        ).first()
        return client


def update_repo_default_branch(repo_id: int, default_branch: str) -> bool:
    """
    更新单个仓库的默认分支
    
    Args:
        repo_id: 仓库配置ID
        default_branch: 默认分支名称
        
    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        affected = session.query(ClientRepo).filter(
            ClientRepo.id == repo_id
        ).update({
            ClientRepo.default_branch: default_branch
        })
        return affected > 0


def get_repo_by_id(repo_id: int) -> Optional[ClientRepo]:
    """获取单个仓库配置"""
    with get_db_session() as session:
        repo = session.query(ClientRepo).filter(
            ClientRepo.id == repo_id
        ).first()
        return repo


def get_usable_clients_for_task(user_id: int) -> List[dict]:
    """
    获取用户可用于创建任务的客户端列表

    包括：
    1. 用户自己创建的客户端
    2. 用户启动并上报过心跳的客户端（需要该客户端是用户自己创建或当前依然是公开状态）

    Args:
        user_id: 用户ID

    Returns:
        可用客户端字典列表
    """
    from .models import ClientHeartbeat

    with get_db_session() as session:
        # 1. 获取用户自己创建的客户端ID集合
        own_client_ids = set(
            row[0] for row in session.query(Client.id).filter(
                Client.user_id == user_id,
                Client.deleted_at.is_(None)
            ).all()
        )

        # 2. 获取用户上报过心跳的客户端ID列表
        heartbeat_client_ids = set(
            row[0] for row in session.query(ClientHeartbeat.client_id).filter(
                ClientHeartbeat.user_id == user_id
            ).all()
        )

        # 合并所有可能的客户端ID
        candidate_ids = own_client_ids.union(heartbeat_client_ids)

        if not candidate_ids:
            return []

        # 查询这些客户端的详细信息，同时校验：
        # - 客户端未删除
        # - 客户端是用户自己创建 OR 客户端是公开的
        clients = session.query(Client, User.name).outerjoin(
            User, Client.creator_id == User.id
        ).filter(
            Client.id.in_(candidate_ids),
            Client.deleted_at.is_(None),
            or_(
                Client.user_id == user_id,
                Client.is_public == True
            )
        ).order_by(Client.created_at.desc()).all()

        result = []
        for client, creator_name in clients:
            data = client.to_dict(include_creator_name=creator_name or '')
            data['editable'] = (client.user_id == user_id)
            result.append(data)
        return result


def get_cloud_clients_with_secret() -> List[dict]:
    """
    获取所有 agent 类型为 cloud 的客户端，同时 JOIN 其所属用户的云客户端专用秘钥。
    若用户没有该专用秘钥则不返回该客户端。
    返回: [{'client_id': int, 'secret': str}, ...]
    """
    from .models import UserSecret
    CLOUD_SECRET_NAME = '云客户端专用(请勿删除)'
    with get_db_session() as session:
        rows = session.query(Client.id, UserSecret.secret).join(
            UserSecret,
            (UserSecret.user_id == Client.user_id) &
            (UserSecret.name == CLOUD_SECRET_NAME)
        ).filter(
            Client.agent == 'cloud',
            Client.deleted_at.is_(None)
        ).all()
        return [{'client_id': client_id, 'secret': secret} for client_id, secret in rows]


def get_client_env_vars(client_id: int) -> List[ClientEnvVar]:
    """获取客户端有效的环境变量列表（deleted_at 为空的记录）"""
    with get_db_session() as session:
        return session.query(ClientEnvVar).filter(
            ClientEnvVar.client_id == client_id,
            ClientEnvVar.deleted_at.is_(None)
        ).order_by(ClientEnvVar.id.asc()).all()


def create_client_env_var(client_id: int, key: str, value: str) -> int:
    """新增环境变量，返回新记录ID"""
    with get_db_session() as session:
        env_var = ClientEnvVar(client_id=client_id, key=key, value=value)
        session.add(env_var)
        session.flush()
        return env_var.id


def update_client_env_var(env_var_id: int, client_id: int, key: str, value: str) -> bool:
    """更新环境变量"""
    with get_db_session() as session:
        affected = session.query(ClientEnvVar).filter(
            ClientEnvVar.id == env_var_id,
            ClientEnvVar.client_id == client_id,
            ClientEnvVar.deleted_at.is_(None)
        ).update({ClientEnvVar.key: key, ClientEnvVar.value: value})
        return affected > 0


def delete_client_env_var(env_var_id: int, client_id: int) -> bool:
    """软删除环境变量（设置 deleted_at）"""
    with get_db_session() as session:
        affected = session.query(ClientEnvVar).filter(
            ClientEnvVar.id == env_var_id,
            ClientEnvVar.client_id == client_id,
            ClientEnvVar.deleted_at.is_(None)
        ).update({ClientEnvVar.deleted_at: datetime.now()})
        return affected > 0


def check_client_usable_for_task(client_id: int, user_id: int) -> bool:
    """
    校验用户是否可以使用指定客户端创建任务

    条件：
    1. 客户端未删除
    2. 客户端是用户自己创建 OR 客户端当前是公开状态

    Args:
        client_id: 客户端ID
        user_id: 用户ID

    Returns:
        是否可以使用
    """
    with get_db_session() as session:
        client = session.query(Client).filter(
            Client.id == client_id,
            Client.deleted_at.is_(None),
            or_(
                Client.user_id == user_id,
                Client.is_public == True
            )
        ).first()
        return client is not None