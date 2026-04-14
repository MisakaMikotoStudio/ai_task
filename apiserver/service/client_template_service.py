#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从模板创建客户端应用 —— 自动创建仓库、数据库、分配云服务器
"""

import logging

from dao.client_dao import (
    add_client_database,
    check_client_name_exists,
    create_client,
    update_client_database,
    VALID_ENVS,
)
from service.client_service import AVAILABLE_AGENTS, ClientSaveError

logger = logging.getLogger(__name__)

VALID_APP_TYPES = ['web']


def create_client_from_template(user_id: int, app_types: list, app_name: str = '') -> int:
    """
    从模板生成默认应用：创建 Client，创建默认仓库（文档 + 代码），然后在 test/prod 环境下各创建一个默认数据库。

    流程：
    1. 使用用户指定的名称或自动生成不重复的应用名称
    2. 创建 Client 记录
    3. 创建默认仓库：
       a. 随机选择一个 code_repo 类型的资源
       b. 文档仓库 {user_id}_docs：检查是否已存在，不存在则创建
       c. 代码仓库 {user_id}_app_{timestamp}：创建新仓库
       d. 绑定仓库到应用
    4. 对于 test、prod 两个环境：
       a. 查找可用的 MySQL Resource
       b. 生成 db_name = {env}_{user_id}_{yyyyMMddHHmmss}
       c. 写入 ClientDatabase 记录
       d. 调用资源服务在云上创建数据库 + 专属账号
       e. 回写连接信息到 ClientDatabase

    Args:
        user_id: 用户 ID
        app_types: 应用形态列表，如 ["web"]
        app_name: 用户指定的应用名称，为空则自动生成

    Returns:
        新创建的客户端 ID

    Raises:
        ClientSaveError: 校验失败或创建失败
    """
    import random
    import time
    from datetime import datetime, timezone
    from dao.resource_dao import get_online_resources_by_type_source
    from service.resource_mysql_service import create_database_with_name, ResourceMySQLError

    # 校验 app_types
    if not app_types or not isinstance(app_types, list):
        raise ClientSaveError('请选择至少一种应用形态')
    for at in app_types:
        if at not in VALID_APP_TYPES:
            raise ClientSaveError(f'不支持的应用形态：{at}')

    # 确定应用名称：优先使用用户指定名称，否则自动生成
    timestamp = int(time.time())
    if app_name:
        client_name = app_name[:16]
        if check_client_name_exists(user_id, client_name):
            raise ClientSaveError(f'应用名称 "{client_name}" 已存在，请更换名称')
    else:
        base_name = f"默认应用_{timestamp}"
        client_name = base_name[:16]
        retries = 0
        while check_client_name_exists(user_id, client_name):
            retries += 1
            if retries > 5:
                raise ClientSaveError('应用名称生成失败，请稍后重试')
            suffix = f"_{retries}"
            client_name = base_name[:16 - len(suffix)] + suffix

    # 创建 Client
    client_id = create_client(
        user_id=user_id,
        name=client_name,
        agent=AVAILABLE_AGENTS[0],
        official_cloud_deploy=0,
    )
    logger.info(
        "create_client_from_template: user_id=%s, client_id=%s, app_types=%s",
        user_id, client_id, app_types,
    )

    # 创建默认仓库
    _create_default_repos(user_id=user_id, client_id=client_id, timestamp=timestamp)

    # 为 test、prod 两个环境分别创建数据库
    for env in VALID_ENVS:
        # 从资源管理中获取可用的 MySQL 资源
        resources = get_online_resources_by_type_source(type='mysql', source='aliyun', env=env)
        if not resources:
            logger.warning(
                "create_client_from_template: no mysql resource for env=%s, user_id=%s, skipping",
                env, user_id,
            )
            continue

        resource = resources[0]
        db_name = f"{env}_{user_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        # 先写入 ClientDatabase 记录
        record_id = add_client_database(
            client_id=client_id, user_id=user_id, env=env, db_name=db_name, db_type='mysql',
        )
        logger.info(
            "create_client_from_template: db record created, record_id=%s, env=%s, db_name=%s, resource_id=%s",
            record_id, env, db_name, resource.id,
        )

        # 调用资源服务在云上创建数据库 + 专属账号
        try:
            result = create_database_with_name(resource=resource, user_id=user_id, db_name=db_name)
            # 回写连接信息
            update_client_database(
                record_id=record_id,
                user_id=user_id,
                host=result['instance_url'],
                port=result.get('port', 3306),
                username=result['account_name'],
                password=result['account_password'],
            )
            logger.info(
                "create_client_from_template: cloud db created, env=%s, db_name=%s, host=%s",
                env, db_name, result['instance_url'],
            )
        except ResourceMySQLError as e:
            logger.error(
                "create_client_from_template: cloud db creation failed, env=%s, db_name=%s, resource_id=%s, error=%s",
                env, db_name, resource.id, e.message,
            )
            # 不阻断其他环境的创建，继续处理

    # 为 test、prod 两个环境分别分配云服务器资源
    from dao.client_dao import upsert_client_server
    for env in VALID_ENVS:
        cloud_server_resources = get_online_resources_by_type_source(
            type='cloud_server', source='tencent_cloud', env=env,
        )
        if not cloud_server_resources:
            logger.warning(
                "create_client_from_template: no cloud_server resource for env=%s, user_id=%s, skipping",
                env, user_id,
            )
            continue

        selected = random.choice(cloud_server_resources)
        upsert_client_server(
            client_id=client_id, user_id=user_id, env=env, name='', password='', ip=selected.name,
        )
        logger.info(
            "create_client_from_template: cloud server assigned, env=%s, resource_name=%s, resource_id=%s",
            env, selected.name, selected.id,
        )

    return client_id


def _create_default_repos(user_id: int, client_id: int, timestamp: int) -> None:
    """
    为默认应用创建文档仓库和代码仓库。

    流程：
    1. 随机选择一个 code_repo 类型的资源
    2. 文档仓库（{user_id}_docs）：检查数据库是否已存在，不存在则创建
    3. 代码仓库（{user_id}_app_{timestamp}）：直接创建
    4. 绑定仓库记录到应用
    """
    import random
    from dao.resource_dao import get_online_resources_by_type_source
    from service.git_service import GitHubServiceError, build_repo_url

    # 1. 随机选择一个 code_repo 资源
    code_repo_resources = get_online_resources_by_type_source(type='code_repo', source='github')
    if not code_repo_resources:
        logger.warning(
            "_create_default_repos: no code_repo resource available, user_id=%s, skipping repo creation",
            user_id,
        )
        return

    resource = random.choice(code_repo_resources)
    extra = resource.get_raw_extra()
    organization = (extra.get('organization') or '').strip()
    if not organization:
        logger.error(
            "_create_default_repos: code_repo resource id=%s missing organization, user_id=%s",
            resource.id, user_id,
        )
        return

    # 2. 文档仓库: {user_id}_docs
    docs_repo_name = f"{user_id}_docs"
    _ensure_and_bind_repo(
        resource=resource, organization=organization, user_id=user_id, client_id=client_id,
        repo_name=docs_repo_name, is_docs_repo=True, description=f"用户 {user_id} 的文档仓库",
    )

    # 3. 代码仓库: {user_id}_app_{timestamp}（从模板创建）
    code_repo_name = f"{user_id}_app_{timestamp}"
    _ensure_and_bind_repo(
        resource=resource, organization=organization, user_id=user_id, client_id=client_id,
        repo_name=code_repo_name, is_docs_repo=False, description=f"用户 {user_id} 的代码仓库",
        template_owner='MisakaMikotoStudio', template_repo='template',
    )


def _ensure_and_bind_repo(
    resource,
    organization: str,
    user_id: int,
    client_id: int,
    repo_name: str,
    is_docs_repo: bool,
    description: str,
    template_owner: str = '',
    template_repo: str = '',
) -> None:
    """
    确保仓库存在并绑定到应用。

    1. 拼接仓库 URL，查询数据库是否已有记录
    2. 如果已存在：直接绑定到当前应用
    3. 如果不存在：创建 GitHub 仓库 + scoped token，回写到数据库
    """
    from dao.client_dao import get_repo_by_url, add_client_repo, update_client_repo_after_creation
    from service.git_service import GitHubServiceError, setup_repo_for_user, build_repo_url

    repo_url = build_repo_url(organization=organization, repo_name=repo_name)
    repo_type_label = "文档仓库" if is_docs_repo else "代码仓库"

    # 检查数据库是否已有该 URL 的记录
    existing_repo = get_repo_by_url(user_id=user_id, url=repo_url)

    if existing_repo:
        # 仓库已存在，直接绑定到当前应用
        logger.info(
            "_ensure_and_bind_repo: repo already exists, binding to client, "
            "user_id=%s, client_id=%s, repo_url=%s, existing_repo_id=%s",
            user_id, client_id, repo_url, existing_repo.id,
        )
        add_client_repo(
            client_id=client_id, user_id=user_id, url=existing_repo.url,
            desc=existing_repo.desc or description, token=existing_repo.token,
            default_branch=existing_repo.default_branch or 'main',
            branch_prefix=existing_repo.branch_prefix or 'ai_', docs_repo=is_docs_repo,
        )
        return

    # 仓库不存在，先创建数据库记录
    repo_record_id = add_client_repo(
        client_id=client_id, user_id=user_id, url=repo_url, desc=description,
        token=None, default_branch='main', branch_prefix='ai_', docs_repo=is_docs_repo,
    )
    logger.info(
        "_ensure_and_bind_repo: db record created, repo_record_id=%s, user_id=%s, repo_name=%s, type=%s",
        repo_record_id, user_id, repo_name, repo_type_label,
    )

    # 调用 GitHub API 创建仓库 + 生成 token
    try:
        result = setup_repo_for_user(
            resource=resource, user_id=user_id, repo_name=repo_name,
            description=description, is_docs_repo=is_docs_repo,
            template_owner=template_owner, template_repo=template_repo,
        )

        # 回写 token 和 default_branch 到数据库记录
        token = result.get('token', '')
        default_branch = result.get('default_branch', 'main')
        if token or default_branch != 'main':
            update_client_repo_after_creation(
                repo_id=repo_record_id, user_id=user_id,
                token=token, default_branch=default_branch,
            )

        logger.info(
            "_ensure_and_bind_repo: github repo created, user_id=%s, repo=%s/%s, type=%s, default_branch=%s",
            user_id, organization, repo_name, repo_type_label, default_branch,
        )
    except GitHubServiceError as e:
        logger.error(
            "_ensure_and_bind_repo: github repo creation failed, user_id=%s, repo_name=%s, type=%s, error=%s",
            user_id, repo_name, repo_type_label, e.message,
        )
        # 不阻断应用创建流程，继续处理


