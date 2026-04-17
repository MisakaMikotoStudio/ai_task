#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端相关路由（Client RPC 调用）
"""

import logging

from flask import Blueprint, request, jsonify, current_app

logger = logging.getLogger(__name__)

from dao.client_dao import (
    get_client_by_id,
    get_client_repos,
    update_repo_default_branch, get_repo_by_id,
    get_client_env_vars,
    update_client_repo_token,
)
from service.client_service import (
    update_client_heartbeat,
)
from dao.chat_dao import get_running_chat_messages_by_client

client_bp = Blueprint('open_client', __name__)


@client_bp.route('/<int:client_id>/heartbeat', methods=['POST'])
def heartbeat(client_id):
    """客户端心跳（更新最后同步时间，带实例UUID验证）"""
    client = get_client_by_id(client_id=client_id, user_id=request.user_info.user_id)
    if not client:
        return jsonify({'code': 400, 'message': '客户端不存在或无权限'}), 400

    data = request.get_json() or {}
    instance_uuid = data.get('instance_uuid', '').strip()
    if not instance_uuid:
        return jsonify({'code': 400, 'message': 'instance_uuid不能为空'}), 400

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
    return jsonify({'code': 200, 'message': '获取运行中Chat消息成功', 'data': data})


@client_bp.route('/<int:client_id>/repos/<int:repo_id>/default-branch', methods=['PATCH'])
def update_repo_default_branch_api(client_id, repo_id):
    """更新仓库的默认主分支（供客户端启动时自动更新）"""
    client = get_client_by_id(client_id=client_id, user_id=request.user_info.user_id)
    if not client:
        return jsonify({'code': 400, 'message': '客户端不存在或无权限'}), 400

    repo = get_repo_by_id(repo_id=repo_id, client_id=client_id, user_id=request.user_info.user_id)
    if not repo:
        return jsonify({'code': 400, 'message': '仓库配置不存在'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    default_branch = data.get('default_branch', '').strip()
    if not default_branch:
        return jsonify({'code': 400, 'message': 'default_branch不能为空'}), 400

    if update_repo_default_branch(
        repo_id=repo_id,
        user_id=request.user_info.user_id,
        default_branch=default_branch,
    ):
        return jsonify({'code': 200, 'message': '默认分支更新成功'})
    return jsonify({'code': 500, 'message': '更新失败'}), 500


@client_bp.route('/<int:client_id>/config', methods=['GET'])
def get_client_config_api(client_id):
    """获取客户端完整配置（供客户端远程启动使用）"""
    client = get_client_by_id(client_id, request.user_info.user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    repos = get_client_repos(client_id, request.user_info.user_id)
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
    """为客户端生成 OSS STS 临时凭证。"""
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

    return jsonify({'code': 200, 'data': oss_data})


@client_bp.route('/<int:client_id>/repos/<int:repo_id>/refresh-token', methods=['POST'])
def refresh_repo_token_api(client_id, repo_id):
    """刷新仓库的 Installation Access Token"""
    from service.git_service import refresh_repo_token_by_url, GitHubServiceError

    client = get_client_by_id(client_id=client_id, user_id=request.user_info.user_id)
    if not client:
        return jsonify({'code': 400, 'message': '客户端不存在或无权限'}), 400

    repo = get_repo_by_id(repo_id=repo_id, client_id=client_id, user_id=request.user_info.user_id)
    if not repo:
        return jsonify({'code': 400, 'message': '仓库配置不存在'}), 400

    try:
        new_token = refresh_repo_token_by_url(repo_url=repo.url, force=True)
    except GitHubServiceError as e:
        return jsonify({'code': 500, 'message': f'token刷新失败: {e.message}'}), 500

    if not update_client_repo_token(repo_id=repo_id, user_id=request.user_info.user_id, token=new_token):
        return jsonify({'code': 500, 'message': 'token已刷新但更新数据库失败'}), 500

    return jsonify({'code': 200, 'message': 'token刷新成功', 'data': {'token': new_token}})


@client_bp.route('/startup-config', methods=['POST'])
def get_client_startup_config():
    """客户端启动配置接口"""
    from dao.client_dao import get_clients_for_startup
    from dao.client_dao import get_client_env_vars_by_client_ids
    from dao.client_dao import get_cannot_run_client_ids_by_user

    user = request.user_info

    body = request.get_json(silent=True) or {}
    client_ids = body.get('clientIds', [])
    if not isinstance(client_ids, list):
        return jsonify({'code': 400, 'message': 'clientIds 必须是数组'}), 400
    client_ids = [int(x) for x in client_ids if x is not None]

    is_admin = user.name == 'admin'
    if is_admin:
        result = get_clients_for_startup()
    else:
        result = get_clients_for_startup(user_id=user.user_id)
        for item in result:
            item['secret'] = request.headers.get('X-Client-Secret')

    permitted_config_client_ids = [item["client_id"] for item in result]
    invalid_ids = get_cannot_run_client_ids_by_user(user.user_id, client_ids, is_admin=is_admin)

    env_vars_map = get_client_env_vars_by_client_ids(permitted_config_client_ids)
    for item in result:
        env_vars = env_vars_map.get(item["client_id"], [])
        item["env_vars"] = [{"key": ev.key, "value": ev.value or ""} for ev in env_vars]

    return jsonify({
        'code': 200,
        'log_user': request.user_info.name,
        'configs': result,
        'invalid_ids': invalid_ids,
    })
