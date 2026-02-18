#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端相关路由
"""

from flask import Blueprint, request, jsonify, g, current_app

from dao.client_dao import (
    create_client, get_clients_by_user, get_client_by_id,
    check_client_name_exists, check_client_name_exists_exclude,
    delete_client, update_client,
    get_client_repos, update_client_repos, get_client_with_permission,
    update_repo_default_branch, get_repo_by_id, get_client_by_id_no_user_check,
    get_clients_paginated, get_usable_clients_for_task,
    get_client_env_vars, create_client_env_var, update_client_env_var, delete_client_env_var
)
from dao.heartbeat_dao import update_heartbeat, get_heartbeats_by_user
from routes.auth_plugin import login_required

client_bp = Blueprint('client', __name__)

# Agent可选项列表（后端写死）
AVAILABLE_AGENTS = ['Claude Code', 'cloud']


@client_bp.route('/agents', methods=['GET'])
@login_required
def get_available_agents():
    """
    获取可用的Agent列表

    Response:
        成功 (200):
            {
                "code": 200,
                "data": ["Claude Code"]
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
    is_public = data.get('is_public', False)
    agent = data.get('agent', 'cloud')

    if not name:
        return jsonify({'code': 400, 'message': '客户端名称不能为空'}), 400

    if len(name) > 16:
        return jsonify({'code': 400, 'message': '客户端名称长度不能超过16个字符'}), 400

    if not isinstance(types, list):
        return jsonify({'code': 400, 'message': 'types必须是数组'}), 400

    # 校验 agent 是否在可选列表中
    if agent not in AVAILABLE_AGENTS:
        return jsonify({'code': 400, 'message': f'无效的Agent类型，可选值: {", ".join(AVAILABLE_AGENTS)}'}), 400

    # 检查是否已存在同名客户端
    if check_client_name_exists(request.user_info.id, name):
        return jsonify({'code': 400, 'message': '客户端名称已存在'}), 400
    
    # 创建客户端
    client_id = create_client(request.user_info.id, name, types, is_public=is_public, agent=agent)
    
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
    获取当前用户可见的客户端列表（自己创建的 + 公开的），支持游标分页

    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID

    Query Parameters:
        cursor: int          # 游标（上一页最后一条记录的client_id），不传表示第一页
        limit: int           # 每页数量，默认20，最大100
        only_mine: bool      # 是否只看我创建的，默认false

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
                            "is_public": bool,      # 是否公开
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
    获取当前用户可用于创建任务的客户端列表

    包括：
    1. 用户自己创建的客户端
    2. 用户启动并上报过心跳的客户端（需要该客户端是用户自己创建或当前依然是公开状态）

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
                        "is_public": bool,      # 是否公开
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
            "is_public": bool,        # 是否公开（可选）
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
    is_public = data.get('is_public')
    agent = data.get('agent')

    if not name:
        return jsonify({'code': 400, 'message': '客户端名称不能为空'}), 400

    if len(name) > 16:
        return jsonify({'code': 400, 'message': '客户端名称长度不能超过16个字符'}), 400

    if not isinstance(types, list):
        return jsonify({'code': 400, 'message': 'types必须是数组'}), 400

    # 校验 agent 是否在可选列表中
    if agent is not None and agent not in AVAILABLE_AGENTS:
        return jsonify({'code': 400, 'message': f'无效的Agent类型，可选值: {", ".join(AVAILABLE_AGENTS)}'}), 400

    # 检查客户端是否存在
    if not get_client_by_id(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    # 检查名称是否与其他客户端重复
    if check_client_name_exists_exclude(request.user_info.id, name, client_id):
        return jsonify({'code': 400, 'message': '客户端名称已存在'}), 400

    # 更新客户端
    update_client(
        client_id, request.user_info.id, name, types,
        is_public=is_public,
        agent=agent
    )

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
        }

    Response:
        成功 (200):
            {"code": 200, "message": "心跳更新成功"}
        实例变更冷却中 (409):
            {"code": 409, "message": "客户端实例变更，请等待N秒后再重试客户端"}
        未找到 (404):
            {"code": 404, "message": "客户端不存在"}
        参数错误 (400):
            {"code": 400, "message": "instance_uuid不能为空"}
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    data = request.get_json() or {}
    instance_uuid = data.get('instance_uuid', '').strip()

    if not instance_uuid:
        return jsonify({'code': 400, 'message': 'instance_uuid不能为空'}), 400

    # 检查客户端是否存在
    client = get_client_by_id_no_user_check(client_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    # 实例变更冷却时间（秒）
    cooldown_seconds = current_app.config.get('INSTANCE_CHANGE_COOLDOWN_SECONDS', 60)

    # 更新心跳记录（使用新的心跳表）
    success, error_msg = update_heartbeat(
        user_id=request.user_info.id,
        client_id=client_id,
        instance_uuid=instance_uuid,
        instance_change_cooldown_seconds=cooldown_seconds
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
    return jsonify({'code': 200, 'message': '仓库配置更新成功'})


@client_bp.route('/<int:client_id>/config', methods=['GET'])
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
    from dao.user_dao import get_user_by_secret

    secret = request.headers.get('X-Client-Secret')
    if not secret:
        return jsonify({'code': 401, 'message': '缺少认证秘钥'}), 401

    # 通过secret查找user
    user = get_user_by_secret(secret)
    if not user:
        return jsonify({'code': 401, 'message': '无效的秘钥'}), 401

    # 获取client配置（需校验权限：创建者或公开）
    client = get_client_with_permission(client_id, user.id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    # 获取仓库配置
    repos = get_client_repos(client_id)
    # 获取环境变量（仅 cloud agent 有效）
    env_vars = get_client_env_vars(client_id)

    return jsonify({
        'code': 200,
        'data': {
            'id': client.id,
            'name': client.name,
            'agent': client.agent or 'Claude Code',
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

    return jsonify({'code': 200, 'message': '环境变量更新成功'})


@client_bp.route('/<int:client_id>/env-vars/<int:env_var_id>', methods=['DELETE'])
@login_required
def delete_client_env_var_api(client_id, env_var_id):
    """软删除客户端环境变量"""
    if not get_client_by_id(client_id, request.user_info.id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    if not delete_client_env_var(env_var_id, client_id):
        return jsonify({'code': 404, 'message': '环境变量不存在'}), 404

    return jsonify({'code': 200, 'message': '环境变量删除成功'})


@client_bp.route('/<int:client_id>/repos/<int:repo_id>/default-branch', methods=['PATCH'])
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
    from dao.user_dao import get_user_by_secret

    secret = request.headers.get('X-Client-Secret')
    if not secret:
        return jsonify({'code': 401, 'message': '缺少认证秘钥'}), 401

    # 通过secret查找user
    user = get_user_by_secret(secret)
    if not user:
        return jsonify({'code': 401, 'message': '无效的秘钥'}), 401

    # 获取client配置（需校验权限：创建者或公开）
    client = get_client_with_permission(client_id, user.id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    # 获取仓库配置
    repo = get_repo_by_id(repo_id)
    if not repo or repo.client_id != client_id:
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
