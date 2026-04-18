#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端数据访问对象 - SQLAlchemy ORM 版本
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import or_

from .connection import get_db_session
from .models import (
    Client, ClientRepo, ClientEnvVar, User,
    ClientServer, ClientDomain, ClientDatabase, ClientDeploy,
)

logger = logging.getLogger(__name__)


def create_client(
    user_id: int,
    name: str,
    agent: str = 'claude sdk',
    official_cloud_deploy: int = 0
) -> int:
    """
    创建客户端

    Args:
        user_id: 用户ID
        name: 客户端名称
        agent: Agent类型
        official_cloud_deploy: 官方云部署（0否 1是）

    Returns:
        新创建的客户端ID
    """
    with get_db_session() as session:
        client = Client(
            user_id=user_id,
            name=name,
            agent=agent,
            official_cloud_deploy=official_cloud_deploy,
            version=1,  # 初始化版本，确保启动器能按版本命名容器
        )
        session.add(client)
        session.flush()
        return client.id


def get_clients_by_user(user_id: int) -> List[dict]:
    """
    获取用户创建的所有客户端

    Args:
        user_id: 用户ID

    Returns:
        客户端字典列表（包含editable）
    """
    with get_db_session() as session:
        clients = session.query(
            Client,
            User.name.label('creator_name'),
        ).outerjoin(
            User,
            User.id == Client.user_id,
        ).filter(
            Client.deleted_at.is_(None),
            Client.user_id == user_id,
        ).order_by(Client.created_at.desc()).all()

        result = []
        for client, creator_name in clients:
            data = client.to_dict()
            data['editable'] = (client.user_id == user_id)
            data['creator_name'] = creator_name
            result.append(data)
        return result


def count_cloud_deploy_clients(user_id: int, exclude_client_id: Optional[int] = None) -> int:
    """
    统计用户未删除的云部署客户端数量

    Args:
        user_id: 用户 ID
        exclude_client_id: 排除的客户端 ID（用于编辑场景，排除当前正在编辑的客户端）

    Returns:
        云部署客户端数量
    """
    with get_db_session() as session:
        query = (session.query(Client)
                 .filter(
                     Client.user_id == user_id,
                     Client.official_cloud_deploy == 1,
                     Client.deleted_at.is_(None),
                 ))
        if exclude_client_id is not None:
            query = query.filter(Client.id != exclude_client_id)
        return query.count()


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
            Client.deleted_at: datetime.now(timezone.utc)
        })
        return affected > 0


def update_client(
    client_id: int,
    user_id: int,
    name: Optional[str] = None,
    agent: Optional[str] = None,
    official_cloud_deploy: Optional[int] = None
) -> bool:
    """
    更新客户端信息

    Args:
        client_id: 客户端ID
        user_id: 用户ID
        name: 新的客户端名称
        agent: Agent类型
        official_cloud_deploy: 官方云部署（0否 1是）

    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        update_data = {}
        if name:
            update_data[Client.name] = name
        if agent:
            update_data[Client.agent] = agent
        if official_cloud_deploy is not None:
            update_data[Client.official_cloud_deploy] = official_cloud_deploy
        if not update_data:
            return False

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


def get_client_repos(client_id: int, user_id: int) -> List[ClientRepo]:
    """获取客户端的仓库配置列表"""
    with get_db_session() as session:
        repos = session.query(ClientRepo).filter(
            ClientRepo.client_id == client_id,
            ClientRepo.user_id == user_id,
            ClientRepo.deleted_at.is_(None)
        ).all()
        return repos


def apply_client_repo_sync(
    client_id: int,
    user_id: int,
    delete_ids: List[int],
    updates: List[Dict[str, Any]],
    inserts: List[Dict[str, Any]],
) -> None:
    """
    在同一事务内执行：按 ID 软删除、按行更新、插入新仓库。
    """
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        if delete_ids:
            session.query(ClientRepo).filter(
                ClientRepo.client_id == client_id,
                ClientRepo.user_id == user_id,
                ClientRepo.id.in_(delete_ids),
                ClientRepo.deleted_at.is_(None),
            ).update({ClientRepo.deleted_at: now}, synchronize_session=False)
        for row in updates:
            rid = row["id"]
            session.query(ClientRepo).filter(
                ClientRepo.id == rid,
                ClientRepo.client_id == client_id,
                ClientRepo.user_id == user_id,
                ClientRepo.deleted_at.is_(None),
            ).update(
                {
                    ClientRepo.desc: row["desc"],
                    ClientRepo.url: row["url"],
                    ClientRepo.token: row.get("token"),
                    ClientRepo.default_branch: row.get("default_branch", ""),
                    ClientRepo.branch_prefix: row.get("branch_prefix", "ai_"),
                    ClientRepo.docs_repo: row.get("docs_repo", False),
                },
                synchronize_session=False,
            )
        for ins in inserts:
            session.add(
                ClientRepo(
                    client_id=client_id,
                    user_id=user_id,
                    desc=ins.get("desc", ""),
                    url=ins.get("url", ""),
                    token=ins.get("token"),
                    default_branch=ins.get("default_branch", ""),
                    branch_prefix=ins.get("branch_prefix", "ai_"),
                    docs_repo=ins.get("docs_repo", False),
                )
            )


def update_repo_default_branch(repo_id: int, user_id: int, default_branch: str) -> bool:
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
            ClientRepo.id == repo_id,
            ClientRepo.user_id == user_id,
        ).update({
            ClientRepo.default_branch: default_branch
        })
        return affected > 0


def get_repo_by_id(repo_id: int, client_id: int, user_id: int) -> Optional[ClientRepo]:
    """获取单个仓库配置"""
    with get_db_session() as session:
        repo = session.query(ClientRepo).filter(
            ClientRepo.id == repo_id,
            ClientRepo.client_id == client_id,
            ClientRepo.user_id == user_id,
            ClientRepo.deleted_at.is_(None)
        ).first()
        return repo


def get_repo_by_url(user_id: int, url: str) -> Optional[ClientRepo]:
    """
    根据 URL 查询用户的仓库配置记录（不限 client_id）

    Args:
        user_id: 用户 ID
        url: 仓库 URL

    Returns:
        ClientRepo 对象或 None
    """
    with get_db_session() as session:
        return session.query(ClientRepo).filter(
            ClientRepo.user_id == user_id,
            ClientRepo.url == url,
            ClientRepo.deleted_at.is_(None),
        ).first()


def add_client_repo(
    client_id: int,
    user_id: int,
    url: str,
    desc: str = '',
    token: Optional[str] = None,
    default_branch: str = 'main',
    branch_prefix: str = 'ai_',
    docs_repo: bool = False,
) -> int:
    """
    新增一条客户端仓库配置记录

    Args:
        client_id: 客户端 ID
        user_id: 用户 ID
        url: 仓库 URL
        desc: 仓库简介
        token: 访问 token
        default_branch: 默认分支
        branch_prefix: 分支前缀
        docs_repo: 是否为文档仓库

    Returns:
        新创建的仓库配置记录 ID
    """
    with get_db_session() as session:
        repo = ClientRepo(
            client_id=client_id,
            user_id=user_id,
            url=url,
            desc=desc,
            token=token,
            default_branch=default_branch,
            branch_prefix=branch_prefix,
            docs_repo=docs_repo,
        )
        session.add(repo)
        session.flush()
        return repo.id


def update_client_repo_token(repo_id: int, user_id: int, token: str) -> bool:
    """
    更新仓库的访问 token

    Args:
        repo_id: 仓库配置 ID
        user_id: 用户 ID
        token: 新的访问 token

    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        affected = session.query(ClientRepo).filter(
            ClientRepo.id == repo_id,
            ClientRepo.user_id == user_id,
            ClientRepo.deleted_at.is_(None),
        ).update({ClientRepo.token: token})
        return affected > 0


def update_client_repo_after_creation(
    repo_id: int,
    user_id: int,
    token: str,
    default_branch: str = 'main',
) -> bool:
    """
    仓库创建完成后回写 token 和 default_branch。

    在 GitHub API 创建仓库后调用，将实际的 token 和 default_branch 更新到数据库记录。
    当使用模板创建仓库时 default_branch 可能与初始值 'main' 不同。

    Args:
        repo_id: 仓库配置 ID
        user_id: 用户 ID
        token: 访问 token
        default_branch: 仓库默认分支名

    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        update_fields = {}
        if token:
            update_fields[ClientRepo.token] = token
        if default_branch:
            update_fields[ClientRepo.default_branch] = default_branch
        if not update_fields:
            return False
        affected = session.query(ClientRepo).filter(
            ClientRepo.id == repo_id,
            ClientRepo.user_id == user_id,
            ClientRepo.deleted_at.is_(None),
        ).update(update_fields)
        return affected > 0


def get_clients_for_startup(user_id:  Optional[int] = None) -> List[dict]:
    if user_id is None:
        """
        获取 official_cloud_deploy 类型为 1 的客户端，同时 JOIN 其所属用户的官方云部署有效秘钥。
        若用户没有官方云部署的有效秘钥则不返回该客户端。
        返回: [{'client_id': int, 'secret': str, 'version': int}, ...]
        """
        from .models import UserSecret
        with get_db_session() as session:
            query = session.query(Client.id, Client.version, UserSecret.secret).join(
                UserSecret,
                (UserSecret.user_id == Client.user_id) &
                (UserSecret.type == UserSecret.TYPE_CLOUD) &
                (UserSecret.deleted_at.is_(None))
            ).filter(
                Client.official_cloud_deploy == 1,
                Client.deleted_at.is_(None)
            )
            rows = query.all()
            return [{'client_id': client_id, 'secret': secret, 'version': version} for client_id, version, secret in rows]

    """
    获取用户可用于创建任务的客户端列表，不返回秘钥，官方云部署客户端不返回
    """
    with get_db_session() as session:
        query = session.query(Client.id, Client.version).filter(
            Client.user_id == user_id,
            Client.official_cloud_deploy == 0,
            Client.deleted_at.is_(None),
        )
        rows = query.all()
        return [{'client_id': client_id, 'version': version} for client_id, version in rows]


def get_cannot_run_client_ids_by_user(user_id: int, client_ids: List[int], is_admin: bool = False) -> List[int]:
    """
    在传入的 client_ids 中，返回「不应在宿主机本地 Docker 继续运行」的客户端 ID 子集（用于启动器清理遗留容器）。

    规则：
    - 已软删除（deleted_at 非空）的客户端：始终计入。
    - 非管理员：official_cloud_deploy=1 的客户端也计入，避免宿主机误跑应与云端并行的实例。
    - 管理员：仅软删除计入；官方云客户端不得出现在本列表中（configs 全量下发云客户端，若标为 invalid 会导致
      main_docker 每轮删建本地容器死循环）。

    返回顺序与 client_ids 中首次出现的顺序一致。
    """
    if not client_ids:
        logger.debug(
            "get_cannot_run_client_ids_by_user: skip empty client_ids user_id=%s is_admin=%s",
            user_id,
            is_admin,
        )
        return []

    with get_db_session() as session:
        q = session.query(Client.id).filter(
            Client.user_id == user_id,
            Client.id.in_(client_ids),
        )
        if is_admin:
            q = q.filter(Client.deleted_at.isnot(None))
        else:
            q = q.filter(
                or_(
                    Client.deleted_at.isnot(None),
                    Client.official_cloud_deploy == 1,
                )
            )
        cannot_run_set = {row[0] for row in q.all()}

    ordered = [cid for cid in client_ids if cid in cannot_run_set]
    return ordered


def increment_client_version(client_id: int, user_id: int) -> bool:
    """
    增加客户端配置版本号（用于触发启动器按新版本重建容器）
    所有会影响到客户端docker执行环境的才需要调用这个接口，其他客户端可以通过配置同步自适应的不需要调用这个接口
    """
    with get_db_session() as session:
        affected = session.query(Client).filter(
            Client.id == client_id,
            Client.user_id == user_id,
            Client.deleted_at.is_(None),
        ).update({Client.version: Client.version + 1})
        return affected > 0


def get_client_env_vars(client_id: int, user_id: int) -> List[ClientEnvVar]:
    """获取客户端有效的环境变量列表（deleted_at 为空的记录）"""
    with get_db_session() as session:
        return session.query(ClientEnvVar).filter(
            ClientEnvVar.client_id == client_id,
            ClientEnvVar.user_id == user_id,
            ClientEnvVar.deleted_at.is_(None)
        ).order_by(ClientEnvVar.id.asc()).all()


def get_client_env_vars_by_client_ids(client_ids: List[int]) -> Dict[int, List[ClientEnvVar]]:
    """批量获取多个客户端的环境变量，按 client_id 分组返回。
    谨慎使用：这个接口不限制用户ID，可能会泄露其他用户的环境变量，仅用于admin场景批量获取配置。"""
    if not client_ids:
        return {}
    with get_db_session() as session:
        rows = session.query(ClientEnvVar).filter(
            ClientEnvVar.client_id.in_(client_ids),
            ClientEnvVar.deleted_at.is_(None)
        ).all()

        grouped: Dict[int, List[ClientEnvVar]] = {}
        for ev in rows:
            grouped.setdefault(ev.client_id, []).append(ev)
        return grouped


def create_client_env_var(client_id: int, user_id: int, key: str, value: str) -> int:
    """新增环境变量，返回新记录ID"""
    with get_db_session() as session:
        env_var = ClientEnvVar(client_id=client_id, user_id=user_id, key=key, value=value)
        session.add(env_var)
        session.flush()
        return env_var.id


def update_client_env_var(env_var_id: int, client_id: int, user_id: int, key: str, value: str) -> bool:
    """更新环境变量"""
    with get_db_session() as session:
        affected = session.query(ClientEnvVar).filter(
            ClientEnvVar.id == env_var_id,
            ClientEnvVar.client_id == client_id,
            ClientEnvVar.user_id == user_id,
            ClientEnvVar.deleted_at.is_(None)
        ).update({ClientEnvVar.key: key, ClientEnvVar.value: value})
        return affected > 0


def delete_client_env_var(env_var_id: int, client_id: int, user_id: int) -> bool:
    """软删除环境变量（设置 deleted_at，UTC）"""
    with get_db_session() as session:
        affected = session.query(ClientEnvVar).filter(
            ClientEnvVar.id == env_var_id,
            ClientEnvVar.client_id == client_id,
            ClientEnvVar.user_id == user_id,
            ClientEnvVar.deleted_at.is_(None)
        ).update({ClientEnvVar.deleted_at: datetime.now(timezone.utc)})
        return affected > 0


def apply_client_env_var_sync(
    client_id: int,
    user_id: int,
    delete_ids: List[int],
    updates: List[Dict[str, Any]],
    inserts: List[Dict[str, Any]],
) -> None:
    """
    在同一事务内：按 ID 软删除（deleted_at 为 UTC）、更新、插入环境变量。
    """
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        if delete_ids:
            session.query(ClientEnvVar).filter(
                ClientEnvVar.client_id == client_id,
                ClientEnvVar.user_id == user_id,
                ClientEnvVar.id.in_(delete_ids),
                ClientEnvVar.deleted_at.is_(None),
            ).update({ClientEnvVar.deleted_at: now}, synchronize_session=False)
        for row in updates:
            session.query(ClientEnvVar).filter(
                ClientEnvVar.id == row["id"],
                ClientEnvVar.client_id == client_id,
                ClientEnvVar.user_id == user_id,
                ClientEnvVar.deleted_at.is_(None),
            ).update(
                {
                    ClientEnvVar.key: row["key"],
                    ClientEnvVar.value: row.get("value", ""),
                },
                synchronize_session=False,
            )
        for ins in inserts:
            session.add(
                ClientEnvVar(
                    client_id=client_id,
                    user_id=user_id,
                    key=ins["key"],
                    value=ins.get("value", ""),
                )
            )


# ============================================================
# 基础设施配置 DAO（云服务器、域名、数据库、支付、对象存储）
# ============================================================

VALID_ENVS = ('test', 'prod')


def get_all_active_servers_by_env(env: str) -> List[ClientServer]:
    """
    获取指定环境下所有未删除的云服务器配置（跨用户，供调度器使用）。

    用于测试环境过期容器清理等需要全局遍历的后台任务。
    """
    with get_db_session() as session:
        return session.query(ClientServer).filter(
            ClientServer.env == env,
            ClientServer.deleted_at.is_(None),
        ).all()


def get_client_servers(client_id: int, user_id: int, env: str = None) -> List[ClientServer]:
    """获取客户端云服务器配置（所有环境）"""
    with get_db_session() as session:
        if env:
            return session.query(ClientServer).filter(
                ClientServer.client_id == client_id,
                ClientServer.user_id == user_id,
                ClientServer.env == env,
                ClientServer.deleted_at.is_(None),
            ).all()
        return session.query(ClientServer).filter(
            ClientServer.client_id == client_id,
            ClientServer.user_id == user_id,
            ClientServer.deleted_at.is_(None),
        ).order_by(ClientServer.env.asc()).all()


def upsert_client_server(
    client_id: int,
    user_id: int,
    env: str,
    name: str,
    password: str,
    ip: str,
) -> None:
    """新增或更新指定环境的云服务器配置（每个环境只保留一条）"""
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        existing = session.query(ClientServer).filter(
            ClientServer.client_id == client_id,
            ClientServer.user_id == user_id,
            ClientServer.env == env,
            ClientServer.deleted_at.is_(None),
        ).first()
        if existing:
            existing.name = name
            existing.password = password
            existing.ip = ip
            existing.updated_at = now
        else:
            session.add(ClientServer(
                client_id=client_id,
                user_id=user_id,
                env=env,
                name=name,
                password=password,
                ip=ip,
            ))


def delete_client_server_by_env(client_id: int, user_id: int, env: str) -> None:
    """软删除指定环境的云服务器配置"""
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        session.query(ClientServer).filter(
            ClientServer.client_id == client_id,
            ClientServer.user_id == user_id,
            ClientServer.env == env,
            ClientServer.deleted_at.is_(None),
        ).update({ClientServer.deleted_at: now}, synchronize_session=False)


def get_client_domains(client_id: int, user_id: int, env: str = None) -> List[ClientDomain]:
    """获取客户端域名配置（所有环境）"""
    with get_db_session() as session:
        if env:
            return session.query(ClientDomain).filter(
                ClientDomain.client_id == client_id,
                ClientDomain.user_id == user_id,
                ClientDomain.env == env,
                ClientDomain.deleted_at.is_(None),
            ).all()
        return session.query(ClientDomain).filter(
            ClientDomain.client_id == client_id,
            ClientDomain.user_id == user_id,
            ClientDomain.deleted_at.is_(None),
        ).order_by(ClientDomain.env.asc()).all()


def sync_client_domains(client_id: int, user_id: int, env: str, domains: List[str]) -> None:
    """全量同步指定环境的域名配置"""
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        # 软删除该环境下所有旧记录
        session.query(ClientDomain).filter(
            ClientDomain.client_id == client_id,
            ClientDomain.user_id == user_id,
            ClientDomain.env == env,
            ClientDomain.deleted_at.is_(None),
        ).update({ClientDomain.deleted_at: now}, synchronize_session=False)
        # 插入新记录
        for domain in domains:
            domain = domain.strip()
            if domain:
                session.add(ClientDomain(
                    client_id=client_id,
                    user_id=user_id,
                    env=env,
                    domain=domain,
                ))


def get_client_databases(client_id: int, user_id: int) -> List[ClientDatabase]:
    """获取客户端数据库配置（所有环境）"""
    with get_db_session() as session:
        return session.query(ClientDatabase).filter(
            ClientDatabase.client_id == client_id,
            ClientDatabase.user_id == user_id,
            ClientDatabase.deleted_at.is_(None),
        ).order_by(ClientDatabase.env.asc(), ClientDatabase.id.asc()).all()


def sync_client_databases(
    client_id: int,
    user_id: int,
    env: str,
    databases: List[Dict[str, Any]],
) -> None:
    """全量同步指定环境的数据库配置"""
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        session.query(ClientDatabase).filter(
            ClientDatabase.client_id == client_id,
            ClientDatabase.user_id == user_id,
            ClientDatabase.env == env,
            ClientDatabase.deleted_at.is_(None),
        ).update({ClientDatabase.deleted_at: now}, synchronize_session=False)
        for db in databases:
            session.add(ClientDatabase(
                client_id=client_id,
                user_id=user_id,
                env=env,
                db_type=db.get('db_type', 'mysql'),
                host=db.get('host', ''),
                port=int(db.get('port', 3306)),
                username=db.get('username', ''),
                password=db.get('password', ''),
                db_name=db.get('db_name', ''),
            ))


def add_client_database(
    client_id: int,
    user_id: int,
    env: str,
    db_name: str,
    db_type: str = 'mysql',
    host: str = '',
    port: int = 3306,
    username: str = '',
    password: str = '',
) -> int:
    """
    新增一条客户端数据库配置记录

    Args:
        client_id: 客户端ID
        user_id: 用户ID
        env: 环境标识（test/prod）
        db_name: 数据库名称
        db_type: 数据库类型
        host: 数据库地址
        port: 端口
        username: 用户名
        password: 密码

    Returns:
        新记录的ID
    """
    with get_db_session() as session:
        record = ClientDatabase(
            client_id=client_id,
            user_id=user_id,
            env=env,
            db_type=db_type,
            host=host,
            port=port,
            username=username,
            password=password,
            db_name=db_name,
        )
        session.add(record)
        session.flush()
        return record.id


def update_client_database(
    record_id: int,
    user_id: int,
    host: str,
    port: int,
    username: str,
    password: str,
) -> bool:
    """
    回写数据库连接信息到已有记录

    Args:
        record_id: 记录ID
        user_id: 用户ID（归属校验）
        host: 数据库地址
        port: 端口
        username: 用户名
        password: 密码

    Returns:
        是否更新成功
    """
    with get_db_session() as session:
        affected = session.query(ClientDatabase).filter(
            ClientDatabase.id == record_id,
            ClientDatabase.user_id == user_id,
            ClientDatabase.deleted_at.is_(None),
        ).update({
            ClientDatabase.host: host,
            ClientDatabase.port: port,
            ClientDatabase.username: username,
            ClientDatabase.password: password,
        }, synchronize_session=False)
        return affected > 0


# ============================================================
# 部署配置 DAO
# ============================================================

def get_client_deploys(client_id: int, user_id: int) -> List[ClientDeploy]:
    """获取客户端的部署配置列表（不区分环境）"""
    with get_db_session() as session:
        return session.query(ClientDeploy).filter(
            ClientDeploy.client_id == client_id,
            ClientDeploy.user_id == user_id,
            ClientDeploy.deleted_at.is_(None),
        ).order_by(ClientDeploy.id.asc()).all()


def get_client_deploy_by_id(deploy_id: int, client_id: int, user_id: int) -> Optional[ClientDeploy]:
    """获取单条部署配置"""
    with get_db_session() as session:
        return session.query(ClientDeploy).filter(
            ClientDeploy.id == deploy_id,
            ClientDeploy.client_id == client_id,
            ClientDeploy.user_id == user_id,
            ClientDeploy.deleted_at.is_(None),
        ).first()


def is_deploy_uuid_exists(uuid: str) -> bool:
    """检查 deploy uuid 是否已存在（全局唯一）"""
    with get_db_session() as session:
        count = session.query(ClientDeploy).filter(
            ClientDeploy.uuid == uuid,
            ClientDeploy.deleted_at.is_(None),
        ).count()
        return count > 0


def add_client_deploy(
    client_id: int, user_id: int, uuid: str, startup_command: str, official_configs: list,
    custom_config: str = '', repo_id: int = None, work_dir: str = '', route_prefix: str = '',
) -> int:
    """新增一条部署配置，返回新记录 ID"""
    with get_db_session() as session:
        record = ClientDeploy(
            client_id=client_id, user_id=user_id, uuid=uuid,
            repo_id=repo_id, work_dir=work_dir or '', route_prefix=route_prefix or '',
            startup_command=startup_command, official_configs=official_configs, custom_config=custom_config or '',
        )
        session.add(record)
        session.flush()
        return record.id


def update_client_deploy(
    deploy_id: int, client_id: int, user_id: int, startup_command: str, official_configs: list,
    custom_config: str = '', repo_id: int = None, work_dir: str = '', route_prefix: str = '',
) -> bool:
    """更新部署配置（uuid 不可更改）"""
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        affected = session.query(ClientDeploy).filter(
            ClientDeploy.id == deploy_id,
            ClientDeploy.client_id == client_id,
            ClientDeploy.user_id == user_id,
            ClientDeploy.deleted_at.is_(None),
        ).update({
            ClientDeploy.repo_id: repo_id,
            ClientDeploy.work_dir: work_dir or '',
            ClientDeploy.route_prefix: route_prefix or '',
            ClientDeploy.startup_command: startup_command,
            ClientDeploy.official_configs: official_configs,
            ClientDeploy.custom_config: custom_config or '',
            ClientDeploy.updated_at: now,
        }, synchronize_session=False)
        return affected > 0


def soft_delete_client_deploys(client_id: int, user_id: int, exclude_ids: List[int]) -> None:
    """软删除 client 下不在 exclude_ids 中的部署配置"""
    now = datetime.now(timezone.utc)
    with get_db_session() as session:
        query = session.query(ClientDeploy).filter(
            ClientDeploy.client_id == client_id,
            ClientDeploy.user_id == user_id,
            ClientDeploy.deleted_at.is_(None),
        )
        if exclude_ids:
            query = query.filter(ClientDeploy.id.notin_(exclude_ids))
        query.update({ClientDeploy.deleted_at: now}, synchronize_session=False)


def check_client_usable_for_user(client_id: int, user_id: int) -> bool:
    """
    校验用户是否可以使用指定客户端创建任务

    条件：
    1. 客户端未删除
    2. 客户端是用户自己创建

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
            Client.user_id == user_id,
        ).first()
        return client is not None