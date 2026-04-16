#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发布记录路由（Web 前端调用）
"""

import logging

from flask import Blueprint, request, jsonify

from dao.deploy_dao import (
    create_deploy_record,
    get_deploy_records_by_client,
    get_deploy_records_by_user,
    cancel_deploy_record,
    retry_deploy_record,
    get_latest_deploy_records_by_msg_ids,
)
from dao.client_dao import get_client_by_id
from dao.models import DeployRecord

logger = logging.getLogger(__name__)

deploy_bp = Blueprint('app_deploy', __name__)


@deploy_bp.route('/client/<int:client_id>/records', methods=['GET'])
def list_deploy_records(client_id):
    """获取指定客户端的发布记录列表（支持分页、状态/环境/msg_id 筛选）"""
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 20, type=int)
    status = request.args.get('status', '', type=str).strip() or None
    env = request.args.get('env', '', type=str).strip() or None
    msg_id_raw = request.args.get('msg_id', '', type=str).strip()
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20
    if status and status not in DeployRecord.VALID_STATUSES:
        return jsonify({'code': 400, 'message': f'无效的状态筛选，可选值: {", ".join(DeployRecord.VALID_STATUSES)}'}), 400
    if env and env not in DeployRecord.VALID_ENVS:
        return jsonify({'code': 400, 'message': f'无效的环境筛选，可选值: {", ".join(DeployRecord.VALID_ENVS)}'}), 400

    msg_id = None
    if msg_id_raw:
        try:
            msg_id = int(msg_id_raw)
        except ValueError:
            return jsonify({'code': 400, 'message': 'msg_id 必须为整数'}), 400
        if msg_id < 0:
            return jsonify({'code': 400, 'message': 'msg_id 不能为负数'}), 400

    result = get_deploy_records_by_client(
        user_id=user_id, client_id=client_id, status=status, env=env, msg_id=msg_id,
        page=page, page_size=page_size,
    )
    return jsonify({'code': 200, 'data': result})


@deploy_bp.route('/client/<int:client_id>/records/by-msgs', methods=['POST'])
def list_deploy_records_by_msgs(client_id):
    """批量查询指定 msg_id 列表下的最新发布记录，按 (msg_id, env) 聚合"""
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    data = request.get_json(silent=True) or {}
    msg_ids = data.get('msg_ids') or []
    if not isinstance(msg_ids, list):
        return jsonify({'code': 400, 'message': 'msg_ids 必须为数组'}), 400
    # 避免请求过大
    if len(msg_ids) > 500:
        return jsonify({'code': 400, 'message': 'msg_ids 数量不能超过 500'}), 400

    result = get_latest_deploy_records_by_msg_ids(user_id=user_id, client_id=client_id, msg_ids=msg_ids)
    return jsonify({'code': 200, 'data': result})


@deploy_bp.route('/records', methods=['GET'])
def list_all_deploy_records():
    """获取当前用户的发布记录列表（支持按应用、状态筛选 + 分页，附带应用名称）"""
    user_id = request.user_info.user_id

    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 20, type=int)
    status = request.args.get('status', '', type=str).strip() or None
    client_id = request.args.get('client_id', type=int)
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20
    if status and status not in DeployRecord.VALID_STATUSES:
        return jsonify({'code': 400, 'message': f'无效的状态筛选，可选值: {", ".join(DeployRecord.VALID_STATUSES)}'}), 400
    if client_id is not None:
        client = get_client_by_id(client_id, user_id)
        if not client:
            return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    result = get_deploy_records_by_user(user_id=user_id, client_id=client_id, status=status, page=page, page_size=page_size)
    return jsonify({'code': 200, 'data': result})


@deploy_bp.route('/client/<int:client_id>/records', methods=['POST'])
def create_deploy_record_api(client_id):
    """创建发布记录"""
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    env = (data.get('env') or '').strip()
    if env not in DeployRecord.VALID_ENVS:
        return jsonify({'code': 400, 'message': f'无效的环境标识，可选值: {", ".join(DeployRecord.VALID_ENVS)}'}), 400

    description = (data.get('description') or '').strip()
    if not description:
        return jsonify({'code': 400, 'message': '发布描述不能为空'}), 400

    status = (data.get('status') or DeployRecord.STATUS_PENDING).strip()
    if status not in DeployRecord.VALID_STATUSES:
        return jsonify({'code': 400, 'message': f'无效的状态，可选值: {", ".join(DeployRecord.VALID_STATUSES)}'}), 400

    detail = data.get('detail') or {}
    if not isinstance(detail, dict):
        return jsonify({'code': 400, 'message': 'detail 必须是字典'}), 400

    # msg_id 可选，默认 0；兼容 body.msg_id 或 detail.msg_id
    msg_id_raw = data.get('msg_id')
    if msg_id_raw is None:
        msg_id_raw = detail.get('msg_id')
    msg_id = 0
    if msg_id_raw is not None and msg_id_raw != '':
        try:
            msg_id = int(msg_id_raw)
        except (TypeError, ValueError):
            return jsonify({'code': 400, 'message': 'msg_id 必须为整数'}), 400
        if msg_id < 0:
            return jsonify({'code': 400, 'message': 'msg_id 不能为负数'}), 400

    # 同步写入 detail.msg_id（保持 detail 中的冗余字段，便于老前端兼容）
    if msg_id:
        detail['msg_id'] = msg_id

    record_id = create_deploy_record(
        user_id=user_id, client_id=client_id, env=env, description=description,
        status=status, detail=detail, msg_id=msg_id,
    )
    return jsonify({'code': 201, 'message': '发布记录创建成功', 'data': {'id': record_id}}), 201


@deploy_bp.route('/records/<int:record_id>/cancel', methods=['PATCH'])
def cancel_deploy_record_api(record_id):
    """取消发布记录"""
    user_id = request.user_info.user_id
    success = cancel_deploy_record(user_id=user_id, record_id=record_id)
    if not success:
        return jsonify({'code': 400, 'message': '记录不存在、无权限或状态不允许取消'}), 400

    return jsonify({'code': 200, 'message': '发布记录已取消'})


@deploy_bp.route('/records/<int:record_id>/retry', methods=['PATCH'])
def retry_deploy_record_api(record_id):
    """重试发布记录（失败或取消状态），将状态重置为 pending"""
    user_id = request.user_info.user_id
    success = retry_deploy_record(user_id=user_id, record_id=record_id)
    if not success:
        return jsonify({'code': 400, 'message': '记录不存在、无权限或状态不允许重试'}), 400

    return jsonify({'code': 200, 'message': '发布记录已重置为等待发布'})
