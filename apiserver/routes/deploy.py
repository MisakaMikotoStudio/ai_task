#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发布记录相关路由
"""

from flask import Blueprint, request, jsonify

from dao.client_dao import get_client_by_id
from dao.deploy_dao import create_deploy_record, get_deploy_records_by_client, cancel_deploy_record, get_deploy_record_by_id
from dao.models import DeployRecord
from service.deploy_service import merge_branch_to_default

deploy_bp = Blueprint('deploy', __name__)


@deploy_bp.route('/records', methods=['GET'])
def list_deploy_records():
    """
    获取发布记录列表

    Query Parameters:
        client_id: int  # 应用ID（必填）

    Response:
        成功 (200):
            {"code": 200, "data": [...]}
    """
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify({'code': 400, 'message': 'client_id 参数必填'}), 400

    user_id = request.user_info.user_id
    if not get_client_by_id(client_id=client_id, user_id=user_id):
        return jsonify({'code': 400, 'message': '应用不存在'}), 400

    records = get_deploy_records_by_client(user_id=user_id, client_id=client_id)
    return jsonify({'code': 200, 'message': '获取成功', 'data': records})


@deploy_bp.route('/records', methods=['POST'])
def create_deploy_record_api():
    """
    创建发布记录

    Request Body:
        {
            "client_id": int,
            "environment": "test" | "prod",
            "description": str,
            "detail": {"task_id": int, "chat_id": int, "msg_id": int}
        }

    Response:
        成功 (201):
            {"code": 201, "data": {...}}
    """
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    user_id = request.user_info.user_id
    client_id = data.get('client_id')
    environment = (data.get('environment') or '').strip()
    description = (data.get('description') or '').strip()
    detail = data.get('detail', {})

    if not client_id:
        return jsonify({'code': 400, 'message': 'client_id 必填'}), 400
    if environment not in (DeployRecord.ENV_TEST, DeployRecord.ENV_PROD):
        return jsonify({'code': 400, 'message': f'environment 必须为 {DeployRecord.ENV_TEST} 或 {DeployRecord.ENV_PROD}'}), 400
    if not isinstance(detail, dict):
        return jsonify({'code': 400, 'message': 'detail 必须为字典'}), 400

    if not get_client_by_id(client_id=client_id, user_id=user_id):
        return jsonify({'code': 400, 'message': '应用不存在'}), 400

    record = create_deploy_record(user_id=user_id, client_id=client_id, environment=environment, description=description, detail=detail)
    return jsonify({'code': 201, 'message': '发布记录创建成功', 'data': record.to_dict()}), 201


@deploy_bp.route('/records/<int:record_id>/cancel', methods=['PATCH'])
def cancel_deploy_record_api(record_id):
    """
    取消发布记录

    Response:
        成功 (200):
            {"code": 200, "message": "已取消"}
    """
    user_id = request.user_info.user_id
    record = get_deploy_record_by_id(record_id=record_id, user_id=user_id)
    if not record:
        return jsonify({'code': 400, 'message': '发布记录不存在'}), 400

    if record.status not in (DeployRecord.STATUS_PENDING, DeployRecord.STATUS_PUBLISHING):
        return jsonify({'code': 400, 'message': f'当前状态 {record.status} 不可取消'}), 400

    ok = cancel_deploy_record(record_id=record_id, user_id=user_id)
    if not ok:
        return jsonify({'code': 500, 'message': '取消失败'}), 500

    return jsonify({'code': 200, 'message': '已取消'})


@deploy_bp.route('/merge', methods=['POST'])
def merge_to_default():
    """
    将 chat 分支合并到各仓库的默认分支

    Request Body:
        {
            "client_id": int,
            "task_id": int,
            "chat_id": int
        }

    Response:
        成功 (200):
            {"code": 200, "data": {"results": [...]}}
    """
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    user_id = request.user_info.user_id
    client_id = data.get('client_id')
    task_id = data.get('task_id')
    chat_id = data.get('chat_id')

    if not client_id or not task_id or not chat_id:
        return jsonify({'code': 400, 'message': 'client_id, task_id, chat_id 均为必填'}), 400

    if not get_client_by_id(client_id=client_id, user_id=user_id):
        return jsonify({'code': 400, 'message': '应用不存在'}), 400

    results = merge_branch_to_default(user_id=user_id, client_id=client_id, task_id=task_id, chat_id=chat_id)

    all_success = all(r.get('success') for r in results) if results else False
    code = 200 if all_success else 207
    message = '合并成功' if all_success else '部分仓库合并失败'

    return jsonify({'code': code, 'message': message, 'data': {'results': results}}), 200
