#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端相关路由
"""

import logging
import secrets
import string

from flask import Blueprint, request, jsonify, g, current_app

logger = logging.getLogger(__name__)

from dao.client_dao import (
    create_client, get_clients_by_user, get_client_by_id,
    check_client_name_exists,
    delete_client,
    get_client_repos,
    update_repo_default_branch, get_repo_by_id,
    get_client_env_vars,
)
from flask import current_app
from service.client_service import (
    AVAILABLE_AGENTS,
    get_client_detail,
    save_client,
    ClientSaveError,
    update_client_heartbeat,
    generate_default_database,
)
from dao.heartbeat_dao import get_heartbeats_by_user
from dao.chat_dao import get_running_chat_messages_by_client

client_bp = Blueprint('client', __name__)


@client_bp.route('/agents', methods=['GET'])
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
def create_client_api():
    """
    创建客户端
    
    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID
    
    Request Body（与 PUT 保存同一套规则；字符串 strip；缺省见下）:
        {
            "name": str,                      # 必填，最多16字符
            "agent": str,                     # 缺省 claude sdk；非空时须为合法 Agent
            "official_cloud_deploy": 0|1|str, # 缺省 0
            "repos": [...],                   # 可选；若带键则全量校验；url/desc/token 等 strip
            "env_vars": [{"key","value"},...] # 可选；若带键则按 key 全量校验
        }
    
    Response:
        成功 (201):
            {
                "code": 201,
                "message": "客户端创建成功",
                "data": { ... }  # 与 GET /clients/<id> 详情结构一致（含 repos、env_vars 等全量）
            }
        失败 (400):
            {"code": 400, "message": "错误信息"}
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    client_id = save_client(
        user_id=request.user_info.user_id,
        data=data,
        client_id=None,
    )
    response_data = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
    if not response_data:
        return jsonify({'code': 500, 'message': '客户端保存成功但读取详情失败'}), 500

    return jsonify({
        'code': 201,
        'message': '客户端创建成功',
        'data': response_data,
    }), 201


@client_bp.route('/generate-default-database', methods=['POST'])
def generate_default_database_api():
    """
    生成默认数据库：在配置的数据库实例上创建 {user_id}_app_{version} 数据库，
    返回数据库连接配置供前端自动填充。

    Response:
        成功 (200):
            {
                "code": 200,
                "message": "数据库创建成功",
                "data": {
                    "db_type": "mysql",
                    "host": "...",
                    "port": 3306,
                    "username": "...",
                    "password": "...",
                    "db_name": "{user_id}_app_{version}"
                }
            }
        失败 (400):
            {"code": 400, "message": "错误信息"}
    """
    try:
        config = current_app.config['APP_CONFIG'].default_database
        db_info = generate_default_database(
            user_id=request.user_info.user_id,
            config=config,
        )
        return jsonify({
            'code': 200,
            'message': '数据库创建成功',
            'data': db_info,
        })
    except ClientSaveError as e:
        return jsonify({'code': 400, 'message': e.message}), 400


@client_bp.route('', methods=['GET'])
def list_clients():
    """
    获取当前用户创建的全部客户端列表

    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID

    Response:
        成功 (200):
            {
                "code": 200,
                "message": "获取客户端列表成功",
                "data": [
                    {
                        "id": int,              # 客户端ID
                        "name": str,            # 客户端名称
                        "last_sync_at": str,    # 最后心跳时间（ISO格式，可为null）
                        "created_at": str,      # 创建时间（ISO格式）
                        "editable": bool        # 是否可编辑
                    },
                    ...
                ]
            }
        未认证 (401):
            {"code": 401, "message": "缺少认证token"}
    """
    user_id = request.user_info.user_id
    result = get_clients_by_user(user_id)

    # 将 heartbeat 表中的最新心跳时间合并到客户端列表，避免前端二次请求
    heartbeats = get_heartbeats_by_user(user_id)
    heartbeat_map = {hb.get('client_id'): hb.get('last_sync_at') for hb in heartbeats}

    for client in result:
        if client.get('id') in heartbeat_map:
            client['last_sync_at'] = heartbeat_map[client.get('id')]

    return jsonify({
        'code': 200,
        'message': '获取客户端列表成功',
        'data': result
    })


@client_bp.route('/<int:client_id>', methods=['GET'])
def get_client_detail_api(client_id):
    """
    根据 ID 获取客户端详情（基本信息、环境变量、代码仓库等，供编辑页一次性加载）

    Response data 在 client.to_dict() 基础上包含：
        editable, repos, env_vars；last_sync_at 与列表接口一致会合并心跳表中的最新时间。
    """
    user_id = request.user_info.user_id
    payload = get_client_detail(client_id, user_id)
    if not payload:
        return jsonify({'code': 400, 'message': '客户端不存在'}), 400

    return jsonify({
        'code': 200,
        'message': '获取客户端详情成功',
        'data': payload
    })


@client_bp.route('/<int:client_id>', methods=['PUT'])
def update_client_api(client_id):
    """
    更新客户端信息（支持编辑页统一保存）

    Headers:
        Authorization: Bearer <token>  # 认证令牌
        traceId: str                   # 请求追踪ID

    URL Parameters:
        client_id: int  # 客户端ID

    Request Body（与 POST 创建同一套规则；编辑页全量提交）:
        {
            "name": str,
            "agent": str,                     # 缺省 claude sdk
            "official_cloud_deploy": 0|1|str, # 缺省 0
            "repos": [...],                   # 可选；若带键则全量同步
            "env_vars": [{"key","value"},...] # 可选；若带键则全量同步
        }

    若传入 repos 或 env_vars 且发生实际变更，会递增客户端配置 version。

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

    save_client(user_id=request.user_info.user_id, data=data, client_id=client_id)
    response_data = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
    if not response_data:
        return jsonify({'code': 500, 'message': '客户端保存成功但读取详情失败'}), 500

    return jsonify({
        'code': 200,
        'message': '客户端更新成功',
        'data': response_data
    })


@client_bp.route('/<int:client_id>', methods=['DELETE'])
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
    if not delete_client(client_id, request.user_info.user_id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 400
    
    return jsonify({'code': 200, 'message': '客户端删除成功'})


@client_bp.route('/<int:client_id>/heartbeat', methods=['POST'])
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
    client = get_client_by_id(client_id=client_id, user_id=request.user_info.user_id)
    if not client:
        return jsonify({'code': 400, 'message': '客户端不存在或无权限'}), 400

    data = request.get_json() or {}
    instance_uuid = data.get('instance_uuid', '').strip()
    if not instance_uuid:
        return jsonify({'code': 400, 'message': 'instance_uuid不能为空'}), 400

    # 更新心跳记录（使用新的心跳表）
    success, error_msg = update_client_heartbeat(
        user_id=request.user_info.user_id,
        client_id=client_id,
        instance_uuid=instance_uuid
    )
    if not success:
        return jsonify({'code': 409, 'message': error_msg}), 409
    return jsonify({'code': 200, 'message': '心跳更新成功'})


@client_bp.route('/<int:client_id>/running_chat_message', methods=['GET'])
def get_running_chat_message_api(client_id):
    """获取指定客户端下需要处理的对话任务消息（供客户端轮询）"""
    if not get_client_by_id(client_id=client_id, user_id=request.user_info.user_id):
        return jsonify({'code': 400, 'message': '客户端不存在或无权限'}), 400
    data = get_running_chat_messages_by_client(user_id=request.user_info.user_id, client_id=client_id)
    return jsonify({
        'code': 200,
        'message': '获取运行中Chat消息成功',
        'data': data
    })


@client_bp.route('/<int:client_id>/copy', methods=['POST'])
def copy_client_api(client_id):
    """
    复制客户端（复制基本信息、环境变量、仓库配置）

    URL Parameters:
        client_id: int  # 源客户端ID

    Response:
        成功 (201):
            data 与 GET /<client_id> 详情结构一致（含 editable、repos、env_vars、last_sync_at 等）
        未找到:
            {"code": 400, "message": "客户端不存在"}
    """
    user_id = request.user_info.user_id
    source_detail = get_client_detail(client_id=client_id, user_id=user_id)
    if not source_detail:
        return jsonify({'code': 400, 'message': '客户端不存在'}), 400

    source_name = source_detail['name']
    suffix_plain = '_副本'
    copy_name = source_name[:16 - len(suffix_plain)] + suffix_plain
    retries = 0
    while check_client_name_exists(user_id, copy_name):
        if retries >= 3:
            return jsonify({'code': 400, 'message': '副本名称生成失败，请手动重命名后重试'}), 400
        rand = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(4))
        suffix = suffix_plain + rand
        copy_name = source_name[:16 - len(suffix)] + suffix
        retries += 1
    
    source_detail.pop('id')
    cid = save_client(user_id=user_id, data=source_detail, client_id=None)
    payload = get_client_detail(client_id=cid, user_id=user_id)
    return jsonify({
        'code': 201,
        'message': '客户端复制成功',
        'data': payload,
    }), 201


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
    # 获取client配置（需校验权限：创建者）
    client = get_client_by_id(client_id=client_id, user_id=request.user_info.user_id)
    if not client:
        return jsonify({'code': 400, 'message': '客户端不存在或无权限'}), 400

    # 获取仓库配置
    repo = get_repo_by_id(repo_id=repo_id, client_id=client_id, user_id=request.user_info.user_id)
    if not repo:
        return jsonify({'code': 400, 'message': '仓库配置不存在'}), 400

    # 获取请求数据
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    default_branch = data.get('default_branch', '').strip()
    if not default_branch:
        return jsonify({'code': 400, 'message': 'default_branch不能为空'}), 400

    # 更新默认分支
    if update_repo_default_branch(
        repo_id=repo_id,
        user_id=request.user_info.user_id,
        default_branch=default_branch,
    ):
        return jsonify({'code': 200, 'message': '默认分支更新成功'})
    return jsonify({'code': 500, 'message': '更新失败'}), 500


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
    # 获取client配置（需校验权限：创建者）
    client = get_client_by_id(client_id, request.user_info.user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    # 获取仓库配置
    repos = get_client_repos(client_id, request.user_info.user_id)
    # 获取环境变量（官方云部署/容器启动场景有效）
    env_vars = get_client_env_vars(client_id, request.user_info.user_id)

    return jsonify({
        'code': 200,
        'data': {
            'id': client.id,
            'name': client.name,
            'login_user_name': request.user_info.name,
            'agent': client.agent,
            'repos': [repo.to_dict() for repo in repos],
            'env_vars': [ev.to_dict() for ev in env_vars],
        }
    })


@client_bp.route('/<int:client_id>/oss-sts', methods=['GET'])
def get_client_oss_sts_api(client_id):
    """
    为客户端生成 OSS STS 临时凭证。
    独立于 config 接口，客户端仅在凭证过期时调用，避免高频轮询 config 时重复生成 STS。
    """
    from service import oss_service

    client = get_client_by_id(client_id=client_id, user_id=request.user_info.user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    config = current_app.config['APP_CONFIG']
    try:
        oss_data = oss_service.get_sts_temp_credentials(
            config=config.oss,
            user_id=request.user_info.user_id,
        )
    except Exception as e:
        logger.warning("生成 STS 临时凭证失败: %s", e)
        return jsonify({'code': 500, 'message': '生成 STS 临时凭证失败'}), 500

    return jsonify({
        'code': 200,
        'data': oss_data,
    })


@client_bp.route('/startup-config', methods=['POST'])
def get_client_startup_config():
    """
    客户端启动配置接口

    验证方式：
        1. 请求头 X-Client-Secret 必须是有效秘钥
        2. 秘钥对应的用户名决定可查询范围（admin 可查询全部；非 admin 仅查询自身）

    注意逻辑：
    为什么要返回 invalid_ids？
    因为客户端机器中可能会有多个用户的客户端容器；invalid_ids 表示请求里的 clientIds 中，
    哪些是当前用户名下已软删除的客户端，便于启动器区分「曾属于本用户但已删除」与其它情况（他人客户端、从未存在等）。，这里返回的是之前用户可用但是当下不可用的客户端id

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
                "invalid_ids": [int, ...] # 当前用户已软删除的客户端ID（clientIds 子集）
            }
        未认证 (401):
            {"code": 401, "message": "错误信息"}
        参数错误 (400):
            {"code": 400, "message": "错误信息"}
    """
    from dao.client_dao import get_clients_for_startup
    from dao.client_dao import get_client_env_vars_by_client_ids
    from dao.client_dao import get_cannot_run_client_ids_by_user

    user = request.user_info

    body = request.get_json(silent=True) or {}
    client_ids = body.get('clientIds', [])
    if not isinstance(client_ids, list):
        return jsonify({'code': 400, 'message': 'clientIds 必须是数组'}), 400
    client_ids = [int(x) for x in client_ids if x is not None]

    # admin：查询所有官方云部署客户端
    # 非 admin：查询当前用户下官方云部署客户端
    is_admin = user.name == 'admin'
    if is_admin:
        result = get_clients_for_startup()
    else:
        result = get_clients_for_startup(user_id=user.user_id)
        # 非admin用户，直接使用请求头的秘钥
        for item in result:
            item['secret'] = request.headers.get('X-Client-Secret')

    permitted_config_client_ids = [item["client_id"] for item in result]
    invalid_ids = get_cannot_run_client_ids_by_user(user.user_id, client_ids, is_admin=is_admin)

    env_vars_map = get_client_env_vars_by_client_ids(permitted_config_client_ids)
    for item in result:
        env_vars = env_vars_map.get(item["client_id"], [])
        item["env_vars"] = [{"key": ev.key, "value": ev.value or ""} for ev in env_vars]

    # result 每项包含 client_id/secret/version/env_vars
    return jsonify({
        'code': 200,
        'log_user': request.user_info.name,
        'configs': result,
        'invalid_ids': invalid_ids,
    })

