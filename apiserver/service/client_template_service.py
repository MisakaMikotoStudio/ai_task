#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从模板创建客户端应用 —— 自动创建仓库、数据库、分配云服务器
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from dao.client_dao import (
    add_client_database,
    check_client_name_exists,
    create_client,
    update_client_database,
    VALID_ENVS,
)
from dao.connection import remove_session
from service.client_service import AVAILABLE_AGENTS, ClientSaveError

logger = logging.getLogger(__name__)

VALID_APP_TYPES = ['web']


def _run_in_thread(fn, *args, **kwargs):
    """
    在子线程中执行任务的通用包装：
    - 任务执行完成后清理该线程的 scoped_session，避免连接泄漏
    - 任务抛出的异常仍会通过 Future 重新抛出，由调用方决定如何处理
    """
    try:
        return fn(*args, **kwargs)
    finally:
        try:
            remove_session()
        except Exception:
            pass


def create_client_from_template(user_id: int, app_types: list, app_name: str = '') -> int:
    """
    从模板生成默认应用：创建 Client，并并发创建默认仓库（文档 + 代码）与 test/prod 默认数据库。

    流程：
    1. 使用用户指定的名称或自动生成不重复的应用名称
    2. 创建 Client 记录
    3. 预解析 code_repo 资源（docs 与 code 仓库共用同一个 GitHub 组织）
    4. 并发执行互不依赖的慢操作：
       - 创建文档仓库（GitHub API + DB 写入）
       - 创建业务代码仓库（GitHub API + DB 写入，返回 repo_id）
       - test 环境 MySQL 创建（Aliyun API + DB 写入）
       - prod 环境 MySQL 创建（Aliyun API + DB 写入）
    5. 等待所有并发任务完成后，基于业务代码仓库 ID 创建默认 deploy 配置

    Args:
        user_id: 用户 ID
        app_types: 应用形态列表，如 ["web"]
        app_name: 用户指定的应用名称，为空则自动生成

    Returns:
        新创建的客户端 ID

    Raises:
        ClientSaveError: 校验失败或创建失败
    """
    import time

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
    # 默认 Agent 使用 claude sdk（AVAILABLE_AGENTS[0]），官方云部署默认开启
    client_id = create_client(
        user_id=user_id,
        name=client_name,
        agent=AVAILABLE_AGENTS[0],
        official_cloud_deploy=1,
    )
    logger.info(
        "create_client_from_template: user_id=%s, client_id=%s, app_types=%s",
        user_id, client_id, app_types,
    )

    # 预解析 code_repo 资源（docs 与 code 仓库共用同一个组织，避免随机选到不同资源）
    repo_ctx = _resolve_code_repo_context(user_id=user_id)

    # 并发执行互不依赖的慢操作（GitHub/Aliyun 网络调用）
    code_repo_id = _run_create_tasks_parallel(
        user_id=user_id, client_id=client_id, timestamp=timestamp, repo_ctx=repo_ctx,
    )

    # 为 web 类型应用创建默认部署配置（apiserver + web），均绑定到业务代码仓库
    if 'web' in app_types:
        _create_default_deploys(
            user_id=user_id, client_id=client_id, code_repo_id=code_repo_id,
        )

    return client_id


def _run_create_tasks_parallel(user_id: int, client_id: int, timestamp: int, repo_ctx):
    """
    并发执行以下互不依赖的任务，提升应用创建速度：
    - 文档仓库创建
    - 业务代码仓库创建（返回 client_repo 记录 ID）
    - 对每个环境分别创建默认 MySQL

    单个任务失败只记录日志、不影响其他任务（与原串行版本的语义一致）。

    Returns:
        业务代码仓库的 client_repo 记录 ID；若未创建成功则返回 None
    """
    envs = list(VALID_ENVS)
    # 4 个固定任务：docs 仓库 + code 仓库 + 每个 env 一个 DB
    max_workers = 2 + len(envs)

    code_repo_id = None
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        docs_future = executor.submit(
            _run_in_thread, _create_docs_repo_task, repo_ctx, user_id, client_id,
        )
        code_future = executor.submit(
            _run_in_thread, _create_code_repo_task, repo_ctx, user_id, client_id, timestamp,
        )
        db_futures = {
            env: executor.submit(_run_in_thread, _create_env_database_task, user_id, client_id, env)
            for env in envs
        }

        # 代码仓库 ID 是后续 deploy 的依赖，单独收集
        try:
            code_repo_id = code_future.result()
        except Exception:
            logger.exception(
                "create_client_from_template: code repo task failed, user_id=%s, client_id=%s",
                user_id, client_id,
            )
            code_repo_id = None

        try:
            docs_future.result()
        except Exception:
            logger.exception(
                "create_client_from_template: docs repo task failed, user_id=%s, client_id=%s",
                user_id, client_id,
            )

        for env, fut in db_futures.items():
            try:
                fut.result()
            except Exception:
                logger.exception(
                    "create_client_from_template: db task failed, user_id=%s, client_id=%s, env=%s",
                    user_id, client_id, env,
                )

    return code_repo_id


def _create_env_database_task(user_id: int, client_id: int, env: str) -> None:
    """
    为单个环境创建默认 MySQL：查资源 → 写记录 → 调云创建 → 回写连接信息。
    业务异常（资源缺失、云创建失败）只记日志不抛出，保持与原串行逻辑一致。
    """
    from datetime import datetime, timezone
    from dao.resource_dao import get_online_resources_by_type_source
    from service.resource_mysql_service import create_database_with_name, ResourceMySQLError

    resources = get_online_resources_by_type_source(type='mysql', source='aliyun', env=env)
    if not resources:
        logger.warning(
            "create_client_from_template: no mysql resource for env=%s, user_id=%s, skipping",
            env, user_id,
        )
        return

    resource = resources[0]
    db_name = f"{env}_{user_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    record_id = add_client_database(
        client_id=client_id, user_id=user_id, env=env, db_name=db_name, db_type='mysql',
    )
    logger.info(
        "create_client_from_template: db record created, record_id=%s, env=%s, db_name=%s, resource_id=%s",
        record_id, env, db_name, resource.id,
    )

    try:
        result = create_database_with_name(resource=resource, user_id=user_id, db_name=db_name)
        update_client_database(
            record_id=record_id, user_id=user_id,
            host=result['instance_url'], port=result.get('port', 3306),
            username=result['account_name'], password=result['account_password'],
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


def _create_default_deploys(user_id: int, client_id: int, code_repo_id: int = None) -> None:
    """
    为默认应用创建两条 deploy 配置（均绑定到业务代码仓库）：
    1. apiserver：工作目录 apiserver、路由前缀 /api、启动命令为空；官方配置勾选 应用名、数据库
    2. web：工作目录 web、路由前缀 /、启动命令为空；官方配置勾选 应用名
    """
    from dao.client_dao import add_client_deploy
    from service.deploy_service import _generate_unique_uuid

    apiserver_uuid = _generate_unique_uuid()
    add_client_deploy(
        client_id=client_id, user_id=user_id, uuid=apiserver_uuid,
        repo_id=code_repo_id,
        work_dir='apiserver',
        route_prefix='/api',
        startup_command='',
        official_configs=['app_name', 'database'],
        custom_config='',
    )
    logger.info(
        "_create_default_deploys: apiserver deploy created, client_id=%s, uuid=%s, repo_id=%s",
        client_id, apiserver_uuid, code_repo_id,
    )

    web_uuid = _generate_unique_uuid()
    add_client_deploy(
        client_id=client_id, user_id=user_id, uuid=web_uuid,
        repo_id=code_repo_id,
        work_dir='web',
        route_prefix='/',
        startup_command='',
        official_configs=['app_name'],
        custom_config='',
    )
    logger.info(
        "_create_default_deploys: web deploy created, client_id=%s, uuid=%s, repo_id=%s",
        client_id, web_uuid, code_repo_id,
    )


DOCS_REPO_DESCRIPTION = '保存AI开发过程中的文档，不涉及任何业务代码'
CODE_REPO_DESCRIPTION = (
    '业务代码仓库。如果用户prompt中没有明确说明是与哪个代码仓库有关，'
    '那么默认指的是当前代码仓库。'
)


def _resolve_code_repo_context(user_id: int):
    """
    随机选择一个 code_repo 资源并解析组织名，供 docs/code 仓库共享使用。
    若没有可用资源或资源配置不完整，返回 None 且仅记日志。

    Returns:
        dict(resource=..., organization=...) 或 None
    """
    import random
    from dao.resource_dao import get_online_resources_by_type_source

    code_repo_resources = get_online_resources_by_type_source(type='code_repo', source='github')
    if not code_repo_resources:
        logger.warning(
            "_resolve_code_repo_context: no code_repo resource available, user_id=%s",
            user_id,
        )
        return None

    resource = random.choice(code_repo_resources)
    extra = resource.get_raw_extra()
    organization = (extra.get('organization') or '').strip()
    if not organization:
        logger.error(
            "_resolve_code_repo_context: code_repo resource id=%s missing organization, user_id=%s",
            resource.id, user_id,
        )
        return None
    return {'resource': resource, 'organization': organization}


def _create_docs_repo_task(repo_ctx, user_id: int, client_id: int):
    """文档仓库创建任务（GitHub API + DB 写入）。"""
    if not repo_ctx:
        return None
    return _ensure_and_bind_repo(
        resource=repo_ctx['resource'], organization=repo_ctx['organization'],
        user_id=user_id, client_id=client_id,
        repo_name=f"{user_id}_docs",
        is_docs_repo=True, description=DOCS_REPO_DESCRIPTION,
    )


def _create_code_repo_task(repo_ctx, user_id: int, client_id: int, timestamp: int):
    """业务代码仓库创建任务（GitHub API + DB 写入），返回 client_repo 记录 ID。"""
    if not repo_ctx:
        return None
    return _ensure_and_bind_repo(
        resource=repo_ctx['resource'], organization=repo_ctx['organization'],
        user_id=user_id, client_id=client_id,
        repo_name=f"{user_id}_app_{timestamp}",
        is_docs_repo=False, description=CODE_REPO_DESCRIPTION,
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
):
    """
    确保仓库存在并绑定到应用，返回新建的 client_repo 记录 ID。

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
        repo_record_id = add_client_repo(
            client_id=client_id, user_id=user_id, url=existing_repo.url,
            desc=description, token=existing_repo.token,
            default_branch=existing_repo.default_branch or 'main',
            branch_prefix=existing_repo.branch_prefix or 'ai_', docs_repo=is_docs_repo,
        )
        return repo_record_id

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

    return repo_record_id


