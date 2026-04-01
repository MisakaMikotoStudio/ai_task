#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端相关路由
"""

from flask import Blueprint, request, jsonify, g

from dao.client_dao import (
    create_client, get_clients_by_user, get_client_by_id,
    check_client_name_exists, check_client_name_exists_exclude,
    delete_client, update_client,
    get_client_repos, update_client_repos, get_client_with_permission,
    update_repo_default_branch, get_repo_by_id, get_client_by_id_no_user_check,
    get_clients_paginated, get_usable_clients_for_task,
    get_client_env_vars, create_client_env_var, update_client_env_var, delete_client_env_var,
    increment_client_version,
)
from dao.heartbeat_dao import update_heartbeat, get_heartbeats_by_user
from dao.chat_dao import get_running_chats_by_client, get_running_chat_messages_by_client
from routes.auth_plugin import login_required

client_bp = Blueprint('client', __name__)

# Agent可选项列表（后端写死）
AVAILABLE_AGENTS = ['claude sdk', 'claude cli']


@client_bp.route('/agents', methods=['GET'])
@login_required
def get_available_agents():
    """
    获取可用的Agent列表

    Response:
        成功 (200):
            {
                "code": 200,
                "data": ["claude sdk", "claude cli"]
            }
    """
    return jsonify({
        'code': 200,
        'data': AVAILABLE_AGENTS
    })


@client_bp.route('', methods=['POST'])
@login_required
def create_client_api():
    """
    创建客户端
    
    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID
    
    Request Body:
        {
            "name": str,      # 客户端名称（必填，最多16个字符）
            "types": [str]    # 支持的任务类型列表（可选，默认为空数组）
        }
    
    Response:
        成功 (201):
            {
                "code": 201,
                "message": "客户端创建成功",
                "data": {
                    "id": int,        # 客户端ID
                    "name": str,      # 客户端名称
                    "types": [str]    # 支持的任务类型列表
                }
            }
        失败 (400):
            {"code": 400, "message": "错误信息"}
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    name = data.get('name', '').strip()
    types = data.get('types', [])
    agent = data.get('agent', 'claude sdk')
    try:
        official_cloud_deploy = int(data.get('official_cloud_deploy', 0) or 0)
    except (TypeError, ValueError):
        return jsonify({'code': 400, 'message': 'official_cloud_deploy 仅支持 0 或 1'}), 400

    if not name:
        return jsonify({'code': 400, 'message': '客户端名称不能为空'}), 400

    if len(name) > 16:
        return jsonify({'code': 400, 'message': '客户端名称长度不能超过16个字符'}), 400

    if not isinstance(types, list):
        return jsonify({'code': 400, 'message': 'types必须是数组'}), 400

    # 校验 agent 是否在可选列表中
    if agent not in AVAILABLE_AGENTS:
        return jsonify({'code': 400, 'message': f'无效的Agent类型，可选值: {", ".join(AVAILABLE_AGENTS)}'}), 400
    if official_cloud_deploy not in (0, 1):
        return jsonify({'code': 400, 'message': 'official_cloud_deploy 仅支持 0 或 1'}), 400

    # 检查是否已存在同名客户端
    if check_client_name_exists(request.user_info.id, name):
        return jsonify({'code': 400, 'message': '客户端名称已存在'}), 400
    
    # 创建客户端
    client_id = create_client(
        request.user_info.id,
        name,
        types,
        agent=agent,
        official_cloud_deploy=official_cloud_deploy
    )
    
    return jsonify({
        'code': 201,
        'message': '客户端创建成功',
        'data': {
            'id': client_id,
            'name': name,
            'types': types
        }
    }), 201


@client_bp.route('', methods=['GET'])
@login_required
def list_clients():
    """
    获取当前用户创建的客户端列表（仅创始人可见），支持游标分页

    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID

    Query Parameters:
        cursor: int          # 游标（上一页最后一条记录的client_id），不传表示第一页
        limit: int           # 每页数量，默认20，最大100
        only_mine: bool      # 是否只看我创建的，默认false（当前仅返回自己创建的）

    Response:
        成功 (200):
            {
                "code": 200,
                "message": "获取客户端列表成功",
                "data": {
                    "items": [
                        {
                            "id": int,              # 客户端ID
                            "name": str,            # 客户端名称
                            "types": [str],         # 支持的任务类型列表
                            "last_sync_at": str,    # 最后心跳时间（ISO格式，可为null）
                            "created_at": str,      # 创建时间（ISO格式）
                            "creator_name": str,    # 创始人名称
                            "editable": bool        # 是否可编辑
                        },
                        ...
                    ],
                    "next_cursor": int,   # 下一页游标，null表示没有更多数据
                    "has_more": bool      # 是否有更多数据
                }
            }
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    # 解析查询参数
    cursor_str = request.args.get('cursor')
    cursor = int(cursor_str) if cursor_str else None

    limit_str = request.args.get('limit', '20')
    limit = min(int(limit_str), 100) if limit_str.isdigit() else 20

    only_mine_str = request.args.get('only_mine', 'false').lower()
    only_mine = only_mine_str in ('true', '1', 'yes')

    result = get_clients_paginated(
        user_id=request.user_info.id,
        cursor=cursor,
        limit=limit,
        only_mine=only_mine
    )

    return jsonify({
        'code': 200,
        'message': '获取客户端列表成功',
        'data': result
    })


@client_bp.route('/usable', methods=['GET'])
@login_required
def list_usable_clients():
    """
    获取当前用户可用于创建任务的客户端列表（仅用户自己创建的客户端）

    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID

    Response:
        成功 (200):
            {
                "code": 200,
                "message": "获取可用客户端列表成功",
                "data": [
                    {
                        "id": int,              # 客户端ID
                        "name": str,            # 客户端名称
                        "types": [str],         # 支持的任务类型列表
                        "creator_name": str,    # 创始人名称
                        "editable": bool        # 是否可编辑
                    },
                    ...
                ]
            }
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    clients = get_usable_clients_for_task(request.user_info.id)

    return jsonify({
        'code': 200,
        'message': '获取可用客户端列表成功',
        'data': clients
    })


@client_bp.route('/<int:client_id>', methods=['GET'])
@login_required
def get_client_api(client_id):
    """
    获取单个客户端信息
    
    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID
    
    URL Parameters:
        client_id: int  # 客户端ID
    
    Response:
        成功 (200):
            {
                "code": 200,
                "message": "获取客户端成功",
                "data": {
                    "id": int,              # 客户端ID
                    "name": str,            # 客户端名称
                    "types": [str],         # 支持的任务类型列表
                    "last_sync_at": str,    # 最后心跳时间（ISO格式，可为null）
                    "created_at": str       # 创建时间（ISO格式）
                }
            }
        未找到 (404):
            {"code": 404, "message": "客户端不存在"}
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    client = get_client_with_permission(client_id, request.user_info.id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404
    
    return jsonify({
        'code': 200,
        'message': '获取客户端成功',
        'data': client.to_dict()
    })


@client_bp.route('/<int:client_id>', methods=['PUT'])
@login_required
def update_client_api(client_id):
    """
    更新客户端信息

    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID

    URL Parameters:
        client_id: int  # 客户端ID

    Request Body:
        {
            "name": str,              # 客户端名称（必填，最多16个字符）
            "types": [str],           # 支持的任务类型列表（可选，默认为空数组）
            "agent": str              # Agent类型（可选）
        }

    Response:
        成功 (200):
            {
                "code": 200,
                "message": "客户端更新成功",
                "data": {...}
            }
        失败 (400):
            {"code": 400, "message": "错误信息"}
        未找到 (404):
            {"code": 404, "message": "客户端不存在"}
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    name = data.get('name', '').strip()
    types = data.get('types', [])
    agent = data.get('agent')
    official_cloud_deploy = data.get('official_cloud_deploy')
    if official_cloud_deploy is not None:
        try:
            official_cloud_deploy = int(official_cloud_deploy)
        except (TypeError, ValueError):
            return jsonify({'code': 400, 'message': 'official_cloud_deploy 仅支持 0 或 1'}), 400

    if not name:
        return jsonify({'code': 400, 'message': '客户端名称不能为空'}), 400

    if len(name) > 16:
        return jsonify({'code': 400, 'message': '客户端名称长度不能超过16个字符'}), 400

    if not isinstance(types, list):
        return jsonify({'code': 400, 'message': 'types必须是数组'}), 400

    # 校验 agent 是否在可选列表中
    if agent is not None and agent not in AVAILABLE_AGENTS:
        return jsonify({'code': 400, 'message': f'无效的Agent类型，可选值: {", ".join(AVAILABLE_AGENTS)}'}), 400
    if official_cloud_deploy is not None and official_cloud_deploy not in (0, 1):
        return jsonify({'code': 400, 'message': 'official_cloud_deploy 仅支持 0 或 1'}), 400

    # 检查客户端是否存在（顺便获取旧 agent 用于判断是否真的变更）
    old_client = get_client_by_id(client_id, request.user_info.id)
    if not old_client:
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    # 检查名称是否与其他客户端重复
    if check_client_name_exists_exclude(request.user_info.id, name, client_id):
        return jsonify({'code': 400, 'message': '客户端名称已存在'}), 400

    # 更新客户端
    update_client(
        client_id, request.user_info.id, name, types,
        agent=agent,
        official_cloud_deploy=official_cloud_deploy
    )

    # 仅当前端更新会影响客户端执行配置时，才增加 version
    # 目前：agent 类型变更会影响云客户端启动执行的逻辑
    old_official_cloud_deploy = old_client.official_cloud_deploy if old_client.official_cloud_deploy is not None else 0
    if (agent is not None and agent != (old_client.agent or 'claude sdk')) or (
        official_cloud_deploy is not None and official_cloud_deploy != old_official_cloud_deploy
    ):
        increment_client_version(client_id, request.user_info.id)

    return jsonify({
        'code': 200,
        'message': '客户端更新成功',
        'data': {
            'id': client_id,
            'name': name,
            'types': types
        }
    })


@client_bp.route('/<int:client_id>', methods=['DELETE'])
@login_required
def delete_client_api(client_id):
    """
    删除客户端（软删除）
    
    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID
    
    URL Parameters:
        client_id: int  # 客户端ID
    
    Response:
        成功 (200):
            {"code": 200, "message": "客户端删除成功"}
        未找到 (404):
            {"code": 404, "message": "客户端不存在"}
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    if not delete_client(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 400
    
    return jsonify({'code': 200, 'message': '客户端删除成功'})


@client_bp.route('/<int:client_id>/heartbeat', methods=['POST'])
@login_required
def heartbeat(client_id):
    """
    客户端心跳（更新最后同步时间，带实例UUID验证）

    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID

    URL Parameters:
        client_id: int  # 客户端ID

    Request Body:
        {
            "instance_uuid": str  # 客户端实例的唯一标识UUID（必填）
        }s

    Response:
        成功 (200):
            {"code": 200, "message": "心跳更新成功"}
        未找到 (404):
            {"code": 404, "message": "客户端不存在或无权限"}
        参数错误 (400):
            {"code": 400, "message": "instance_uuid不能为空"}
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    # 检查客户端是否存在
    client = get_client_with_permission(client_id, request.user_info.id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    data = request.get_json() or {}
    instance_uuid = data.get('instance_uuid', '').strip()
    if not instance_uuid:
        return jsonify({'code': 400, 'message': 'instance_uuid不能为空'}), 400

    # 更新心跳记录（使用新的心跳表）
    success, error_msg = update_heartbeat(
        user_id=request.user_info.id,
        client_id=client_id,
        instance_uuid=instance_uuid
    )
    if not success:
        return jsonify({'code': 409, 'message': error_msg}), 409
    return jsonify({'code': 200, 'message': '心跳更新成功'})


@client_bp.route('/heartbeats', methods=['GET'])
@login_required
def get_user_heartbeats():
    """
    获取当前用户所有客户端的心跳记录

    Response:
        成功 (200):
            {"code": 200, "data": [{"client_id": 1, "last_sync_at": "...", ...}]}
    """
    heartbeats = get_heartbeats_by_user(request.user_info.id)
    return jsonify({'code': 200, 'data': heartbeats})


@client_bp.route('/running_chat', methods=['GET'])
@login_required
def get_running_chat_api():
    """获取指定客户端下仍在运行中的Chat消息列表（供客户端轮询）"""
    client_id_raw = request.args.get('clientId') or request.headers.get('X-Client-ID')
    if not client_id_raw:
        return jsonify({'code': 400, 'message': 'clientId不能为空'}), 400

    try:
        client_id = int(client_id_raw)
    except (TypeError, ValueError):
        return jsonify({'code': 400, 'message': 'clientId必须是整数'}), 400

    if client_id <= 0:
        return jsonify({'code': 400, 'message': 'clientId必须大于0'}), 400

    # 权限校验：仅可查询自己创建的或公开客户端
    if not get_client_with_permission(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    data = get_running_chats_by_client(request.user_info.id, client_id)
    return jsonify({
        'code': 200,
        'message': '获取运行中Chat成功',
        'data': data
    })


@client_bp.route('/<int:client_id>/running_chat_message', methods=['GET'])
@login_required
def get_running_chat_message_api(client_id):
    """获取指定客户端下需要处理的对话任务消息（供客户端轮询）"""
    # 权限校验：仅可查询自己创建的或公开客户端
    if not get_client_with_permission(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404
    data = get_running_chat_messages_by_client(request.user_info.id, client_id)
    return jsonify({
        'code': 200,
        'message': '获取运行中Chat消息成功',
        'data': data
    })


@client_bp.route('/<int:client_id>/copy', methods=['POST'])
@login_required
def copy_client_api(client_id):
    """
    复制客户端（复制基本信息、环境变量、仓库配置）

    URL Parameters:
        client_id: int  # 源客户端ID

    Response:
        成功 (201):
            {
                "code": 201,
                "message": "客户端复制成功",
                "data": {"id": int, "name": str}
            }
        未找到 (404):
            {"code": 404, "message": "客户端不存在"}
    """
    source_client = get_client_by_id(client_id, request.user_info.id)
    if not source_client:
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    repos = get_client_repos(client_id)
    env_vars = get_client_env_vars(client_id)

    # 生成不重名的副本名称
    suffix = '_副本'
    base = source_client.name[:16 - len(suffix)]
    copy_name = base + suffix
    counter = 2
    while check_client_name_exists(request.user_info.id, copy_name):
        suffix_n = f'_副本{counter}'
        copy_name = source_client.name[:16 - len(suffix_n)] + suffix_n
        counter += 1
        if counter > 99:
            return jsonify({'code': 400, 'message': '副本名称生成失败，请手动重命名后重试'}), 400

    new_client_id = create_client(
        request.user_info.id,
        copy_name,
        source_client.types or [],
        agent=source_client.agent or 'claude sdk',
        official_cloud_deploy=source_client.official_cloud_deploy or 0
    )

    if repos:
        update_client_repos(new_client_id, [repo.to_dict() for repo in repos])

    for ev in env_vars:
        create_client_env_var(new_client_id, ev.key, ev.value or '')

    return jsonify({
        'code': 201,
        'message': '客户端复制成功',
        'data': {'id': new_client_id, 'name': copy_name}
    }), 201


@client_bp.route('/<int:client_id>/repos', methods=['GET'])
@login_required
def get_client_repos_api(client_id):
    """获取客户端仓库配置列表"""
    # 检查客户端是否存在且有权限（创建者或公开客户端）
    if not get_client_with_permission(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    repos = get_client_repos(client_id)
    return jsonify({
        'code': 200,
        'data': [repo.to_dict() for repo in repos]
    })


@client_bp.route('/<int:client_id>/repos', methods=['PUT'])
@login_required
def update_client_repos_api(client_id):
    """批量更新客户端仓库配置（全量替换）"""
    # 检查客户端是否存在且有权限
    if not get_client_by_id(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    repos = data.get('repos', [])
    if not isinstance(repos, list):
        return jsonify({'code': 400, 'message': 'repos必须是数组'}), 400

    # 校验每个仓库配置
    docs_repo_count = 0
    for idx, repo in enumerate(repos):
        repo_num = idx + 1
        if not repo.get('url'):
            return jsonify({'code': 400, 'message': f'仓库#{repo_num} URL不能为空'}), 400
        # 如果url以http开头，token必填
        if repo.get('url', '').startswith('http') and not repo.get('token'):
            return jsonify({'code': 400, 'message': f'仓库#{repo_num} 使用HTTP地址时token必填'}), 400
        if not repo.get('desc'):
            return jsonify({'code': 400, 'message': f'仓库#{repo_num} 简介不能为空'}), 400
        # 统计文档仓库数量
        if repo.get('docs_repo'):
            docs_repo_count += 1

    # 校验：必须有且仅有一个文档仓库
    if docs_repo_count == 0:
        return jsonify({'code': 400, 'message': '必须指定一个文档仓库'}), 400
    if docs_repo_count > 1:
        return jsonify({'code': 400, 'message': '只能指定一个文档仓库'}), 400

    update_client_repos(client_id, repos)
    # 仓库配置变更会影响客户端执行
    increment_client_version(client_id, request.user_info.id)
    return jsonify({'code': 200, 'message': '仓库配置更新成功'})


@client_bp.route('/<int:client_id>/config', methods=['GET'])
@login_required
def get_client_config_api(client_id):
    """
    获取客户端完整配置（供客户端远程启动使用）

    Headers:
        X-Client-Secret: <secret>  # 认证秘钥

    Response:
        成功 (200): 客户端完整配置
        未认证 (401): 秘钥无效
        未找到 (404): 客户端不存在或无权限
    """
    # 获取client配置（需校验权限：创建者或公开）
    client = get_client_with_permission(client_id, request.user_info.id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    # 获取仓库配置
    repos = get_client_repos(client_id)
    # 获取环境变量（官方云部署/容器启动场景有效）
    env_vars = get_client_env_vars(client_id)

    return jsonify({
        'code': 200,
        'data': {
            'id': client.id,
            'name': client.name,
            'login_user_name': request.user_info.name,
            'agent': client.agent,
            'repos': [repo.to_dict() for repo in repos],
            'env_vars': [ev.to_dict() for ev in env_vars]
        }
    })


@client_bp.route('/<int:client_id>/env-vars', methods=['GET'])
@login_required
def get_client_env_vars_api(client_id):
    """获取客户端环境变量列表"""
    if not get_client_with_permission(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    env_vars = get_client_env_vars(client_id)
    return jsonify({
        'code': 200,
        'data': [ev.to_dict() for ev in env_vars]
    })


@client_bp.route('/<int:client_id>/env-vars', methods=['POST'])
@login_required
def create_client_env_var_api(client_id):
    """新增客户端环境变量"""
    if not get_client_by_id(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    key = data.get('key', '').strip()
    value = data.get('value', '')

    if not key:
        return jsonify({'code': 400, 'message': '环境变量名不能为空'}), 400

    env_var_id = create_client_env_var(client_id, key, value)
    # 环境变量变更会影响客户端执行
    increment_client_version(client_id, request.user_info.id)
    return jsonify({'code': 201, 'message': '环境变量创建成功', 'data': {'id': env_var_id}}), 201


@client_bp.route('/<int:client_id>/env-vars/<int:env_var_id>', methods=['PUT'])
@login_required
def update_client_env_var_api(client_id, env_var_id):
    """更新客户端环境变量"""
    if not get_client_by_id(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    key = data.get('key', '').strip()
    value = data.get('value', '')

    if not key:
        return jsonify({'code': 400, 'message': '环境变量名不能为空'}), 400

    if not update_client_env_var(env_var_id, client_id, key, value):
        return jsonify({'code': 404, 'message': '环境变量不存在'}), 404

    # 环境变量变更会影响客户端执行
    increment_client_version(client_id, request.user_info.id)

    return jsonify({'code': 200, 'message': '环境变量更新成功'})


@client_bp.route('/<int:client_id>/env-vars/<int:env_var_id>', methods=['DELETE'])
@login_required
def delete_client_env_var_api(client_id, env_var_id):
    """软删除客户端环境变量"""
    if not get_client_by_id(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    if not delete_client_env_var(env_var_id, client_id):
        return jsonify({'code': 404, 'message': '环境变量不存在'}), 404

    # 环境变量变更会影响客户端执行
    increment_client_version(client_id, request.user_info.id)
    return jsonify({'code': 200, 'message': '环境变量删除成功'})


@client_bp.route('/<int:client_id>/repos/<int:repo_id>/default-branch', methods=['PATCH'])
@login_required
def update_repo_default_branch_api(client_id, repo_id):
    """
    更新仓库的默认主分支（供客户端启动时自动更新）

    Headers:
        X-Client-Secret: <secret>  # 认证秘钥

    URL Parameters:
        client_id: int  # 客户端ID
        repo_id: int    # 仓库配置ID

    Request Body:
        {
            "default_branch": str  # 默认分支名称（必填）
        }

    Response:
        成功 (200):
            {"code": 200, "message": "默认分支更新成功"}
        失败 (400):
            {"code": 400, "message": "错误信息"}
        未认证 (401):
            {"code": 401, "message": "缺少认证秘钥"}
        未找到 (404):
            {"code": 404, "message": "仓库配置不存在或无权限"}
    """
    # 获取client配置（需校验权限：创建者或公开）
    client = get_client_with_permission(client_id, request.user_info.id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    # 获取仓库配置
    repo = get_repo_by_id(repo_id, client_id)
    if not repo:
        return jsonify({'code': 404, 'message': '仓库配置不存在'}), 404

    # 获取请求数据
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    default_branch = data.get('default_branch', '').strip()
    if not default_branch:
        return jsonify({'code': 400, 'message': 'default_branch不能为空'}), 400

    # 更新默认分支
    if update_repo_default_branch(repo_id, default_branch):
        return jsonify({'code': 200, 'message': '默认分支更新成功'})
    else:
        return jsonify({'code': 500, 'message': '更新失败'}), 500


@client_bp.route('/startup-config', methods=['POST'])
@login_required
def get_client_startup_config():
    """
    客户端启动配置接口

    验证方式：
        1. 请求头 X-Client-Secret 必须是有效秘钥
        2. 秘钥对应的用户名决定可查询范围（admin 可查询全部；非 admin 仅查询自身）

    注意逻辑：
    为什么要返回 invalid_ids？
    因为客户端机器中可能会有多个用户的客户端容器，传过来的查询客户端id并不一定是当前用户的且有效，需要服务端做区分

    request body:
        {
            "clientIds": [int, ...] # 客户端ID列表
        }

    Response:
        成功 (200):
            {
                "code": 200,
                "configs": [
                    {
                        "client_id": int,   # 客户端ID
                        "secret": str,      # 客户端专用秘钥
                        "version": int,     # 客户端配置版本号（用于启动器容器名）
                        "env_vars": [       # 客户端配置的环境变量（用于 docker run -e 注入）
                            {"key": str, "value": str},
                            ...
                        ]
                    },
                    ...
                ]，
                "invalid_ids": [int, ...] # 无效的客户端ID列表
            }
        未认证 (401):
            {"code": 401, "message": "错误信息"}
        参数错误 (400):
            {"code": 400, "message": "错误信息"}
    """
    from dao.client_dao import get_clients_for_startup
    from dao.client_dao import get_client_env_vars_by_client_ids

    user = request.user_info

    body = request.get_json(silent=True) or {}
    client_ids = body.get('clientIds', [])
    if not isinstance(client_ids, list):
        return jsonify({'code': 400, 'message': 'clientIds 必须是数组'}), 400
    client_ids = [int(x) for x in client_ids if x is not None]

    # admin：查询所有官方云部署客户端
    # 非 admin：查询当前用户下官方云部署客户端
    if user.name == 'admin':
        result = get_clients_for_startup()
    else:
        result = get_clients_for_startup(user_id=user.id)
        # 非admin用户，直接使用请求头的秘钥
        for item in result:
            item['secret'] = request.headers.get('X-Client-Secret')

    permitted_config_client_ids = [item["client_id"] for item in result]
    invalid_ids = [cid for cid in client_ids if cid not in permitted_config_client_ids]

    env_vars_map = get_client_env_vars_by_client_ids(permitted_config_client_ids)
    for item in result:
        env_vars = env_vars_map.get(item["client_id"], [])
        item["env_vars"] = [{"key": ev.key, "value": ev.value or ""} for ev in env_vars]

    # result 每项包含 client_id/secret/version/env_vars
    return jsonify({
        'code': 200,
        'configs': result,
        'invalid_ids': invalid_ids,
    })