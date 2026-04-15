#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端管理路由（Web 前端调用）
"""

import secrets
import string

from flask import Blueprint, request, jsonify

from dao.client_dao import (
    get_clients_by_user, get_client_by_id,
    check_client_name_exists,
    delete_client,
    get_client_repos,
    get_client_env_vars,
    get_client_domains,
)
from service.client_service import (
    AVAILABLE_AGENTS,
    get_client_detail,
    save_client,
    ClientSaveError,
    create_client_from_template,
    DeployConfigError,
    execute_deploy,
    generate_deploy_toml,
)
from dao.heartbeat_dao import get_heartbeats_by_user

client_bp = Blueprint('app_client', __name__)


@client_bp.route('/agents', methods=['GET'])
def get_available_agents():
    """获取可用的Agent列表"""
    return jsonify({'code': 200, 'data': AVAILABLE_AGENTS})


@client_bp.route('', methods=['POST'])
def create_client_api():
    """创建客户端"""
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    try:
        client_id = save_client(
            user_id=request.user_info.user_id,
            data=data,
            client_id=None,
        )
    except (ClientSaveError, DeployConfigError) as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    response_data = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
    if not response_data:
        return jsonify({'code': 500, 'message': '客户端保存成功但读取详情失败'}), 500

    return jsonify({'code': 201, 'message': '客户端创建成功', 'data': response_data}), 201


@client_bp.route('/create-from-template', methods=['POST'])
def create_from_template_api():
    """从模板生成默认应用"""
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    app_types = data.get('app_types', [])
    app_name = data.get('name', '').strip() if data.get('name') else ''

    try:
        client_id = create_client_from_template(
            user_id=request.user_info.user_id,
            app_types=app_types,
            app_name=app_name,
        )
        response_data = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
        if not response_data:
            return jsonify({'code': 500, 'message': '应用创建成功但读取详情失败'}), 500

        return jsonify({'code': 201, 'message': '应用创建成功', 'data': response_data}), 201
    except ClientSaveError as e:
        return jsonify({'code': 400, 'message': e.message}), 400


@client_bp.route('', methods=['GET'])
def list_clients():
    """获取当前用户创建的全部客户端列表"""
    user_id = request.user_info.user_id
    result = get_clients_by_user(user_id)

    heartbeats = get_heartbeats_by_user(user_id)
    heartbeat_map = {hb.get('client_id'): hb.get('last_sync_at') for hb in heartbeats}

    for client in result:
        if client.get('id') in heartbeat_map:
            client['last_sync_at'] = heartbeat_map[client.get('id')]

    return jsonify({'code': 200, 'message': '获取客户端列表成功', 'data': result})


@client_bp.route('/<int:client_id>', methods=['GET'])
def get_client_detail_api(client_id):
    """根据 ID 获取客户端详情"""
    user_id = request.user_info.user_id
    payload = get_client_detail(client_id, user_id)
    if not payload:
        return jsonify({'code': 400, 'message': '客户端不存在'}), 400

    return jsonify({'code': 200, 'message': '获取客户端详情成功', 'data': payload})


@client_bp.route('/<int:client_id>', methods=['PUT'])
def update_client_api(client_id):
    """更新客户端信息"""
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    try:
        save_client(user_id=request.user_info.user_id, data=data, client_id=client_id)
    except (ClientSaveError, DeployConfigError) as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    response_data = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
    if not response_data:
        return jsonify({'code': 500, 'message': '客户端保存成功但读取详情失败'}), 500

    return jsonify({'code': 200, 'message': '客户端更新成功', 'data': response_data})


@client_bp.route('/<int:client_id>', methods=['DELETE'])
def delete_client_api(client_id):
    """删除客户端（软删除）"""
    if not delete_client(client_id, request.user_info.user_id):
        return jsonify({'code': 404, 'message': '客户端不存在'}), 404

    return jsonify({'code': 200, 'message': '客户端删除成功'})


@client_bp.route('/<int:client_id>/copy', methods=['POST'])
def copy_client_api(client_id):
    """复制客户端"""
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
    return jsonify({'code': 201, 'message': '客户端复制成功', 'data': payload}), 201


@client_bp.route('/<int:client_id>/config', methods=['GET'])
def get_client_config_api(client_id):
    """获取客户端完整配置（repos、agent 等）"""
    client = get_client_by_id(client_id, request.user_info.user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    repos = get_client_repos(client_id, request.user_info.user_id)
    env_vars = get_client_env_vars(client_id, request.user_info.user_id)
    domain_rows = get_client_domains(client_id, request.user_info.user_id)
    domains_result = {}
    for dom in domain_rows:
        domains_result.setdefault(dom.env, []).append(dom.domain)

    return jsonify({
        'code': 200,
        'data': {
            'id': client.id,
            'name': client.name,
            'login_user_name': request.user_info.name,
            'agent': client.agent,
            'repos': [repo.to_dict() for repo in repos],
            'env_vars': [ev.to_dict() for ev in env_vars],
            'domains': domains_result,
        }
    })


@client_bp.route('/<int:client_id>/deploy-preview', methods=['POST'])
def deploy_preview_api(client_id):
    """预览部署配置生成的 TOML 内容"""
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 400, 'message': '客户端不存在'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    official_configs = data.get('official_configs', [])
    custom_config = data.get('custom_config', '')
    env = data.get('env', 'prod')

    try:
        toml_content = generate_deploy_toml(
            client_id=client_id, user_id=user_id,
            official_configs=official_configs, custom_config=custom_config, env=env,
        )
        return jsonify({'code': 200, 'data': {'toml_content': toml_content}})
    except DeployConfigError as e:
        return jsonify({'code': 400, 'message': e.message}), 400


@client_bp.route('/<int:client_id>/deploy/<int:deploy_id>/execute', methods=['POST'])
def deploy_execute_api(client_id, deploy_id):
    """执行部署：SSH 远程写入 TOML 配置文件"""
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 400, 'message': '客户端不存在'}), 400

    try:
        result = execute_deploy(client_id=client_id, user_id=user_id, deploy_id=deploy_id)
        return jsonify({'code': 200, 'message': result})
    except DeployConfigError as e:
        return jsonify({'code': 400, 'message': e.message}), 400
