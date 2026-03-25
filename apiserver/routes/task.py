#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务相关路由
"""

from flask import Blueprint, request, jsonify

from routes.auth_plugin import login_required
from service.task_service import (
    create_task, get_tasks, get_task, update_status, update_flow, update_desc, delete_task,
    review_task, update_client, sync_execute, TaskNotFoundException, TaskValidationException
)
from dao.task_dao import get_task_by_id

task_bp = Blueprint('task', __name__)


@task_bp.route('', methods=['POST'])
@login_required
def create_task_api():
    """创建任务"""
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    try:
        task = create_task(
            user_id=request.user_info.id,
            title=data.get('title', ''),
            task_type=data.get('type', ''),
            client_id=data.get('client_id'),
            desc=data.get('desc'),
            status=data.get('status')
        )
    except TaskValidationException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except RuntimeError as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

    return jsonify({
        'code': 201,
        'message': '任务创建成功',
        'data': task.to_dict()
    }), 201


@task_bp.route('', methods=['GET'])
@login_required
def list_tasks():
    """获取任务列表，支持按状态和客户端过滤"""
    status = request.args.get('status')  # 可选查询参数
    client_id_str = request.args.get('clientId')  # 可选查询参数
    client_id = int(client_id_str) if client_id_str else None
    
    try:
        tasks = get_tasks(request.user_info.id, status, client_id)
    except TaskValidationException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400

    return jsonify({
        'code': 200,
        'message': '获取任务列表成功',
        'data': tasks
    })


@task_bp.route('/<int:task_id>', methods=['GET'])
@login_required
def get_task_api(task_id):
    """获取任务详情"""
    try:
        task = get_task(task_id, request.user_info.id)
    except TaskNotFoundException as e:
        return jsonify({'code': 404, 'message': str(e)}), 404
    
    return jsonify({
        'code': 200,
        'message': '获取任务成功',
        'data': task  # get_task 已返回处理后的字典
    })


@task_bp.route('/<int:task_id>/status', methods=['PATCH'])
@login_required
def update_task_status_api(task_id):
    """更新任务状态"""
    data = request.get_json()
    
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400
    
    try:
        result = update_status(
            task_id=task_id,
            user_id=request.user_info.id,
            status=data.get('status', '')
        )
    except TaskValidationException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except TaskNotFoundException as e:
        return jsonify({'code': 404, 'message': str(e)}), 404
    
    return jsonify({
        'code': 200,
        'message': '状态更新成功',
        'data': result
    })


@task_bp.route('/<int:task_id>/flow', methods=['PUT'])
@login_required
def update_task_flow_api(task_id):
    """更新任务流程"""
    data = request.get_json()
    
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400
    
    try:
        result = update_flow(
            task_id=task_id,
            user_id=request.user_info.id,
            flow=data.get('flow'),
            flow_status=data.get('flow_status')
        )
    except TaskValidationException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except TaskNotFoundException as e:
        return jsonify({'code': 404, 'message': str(e)}), 404
    
    return jsonify({
        'code': 200,
        'message': '流程更新成功',
        'data': result
    })


@task_bp.route('/<int:task_id>/desc', methods=['PATCH'])
@login_required
def update_task_desc_api(task_id):
    """更新任务描述"""
    data = request.get_json()
    
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400
    
    try:
        result = update_desc(
            task_id=task_id,
            user_id=request.user_info.id,
            desc=data.get('desc', ''),
            status=data.get('status')
        )
    except TaskValidationException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except TaskNotFoundException as e:
        return jsonify({'code': 404, 'message': str(e)}), 404
    
    return jsonify({
        'code': 200,
        'message': '任务描述更新成功',
        'data': result
    })


@task_bp.route('/<int:task_id>', methods=['DELETE'])
@login_required
def delete_task_api(task_id):
    """删除任务"""
    try:
        result = delete_task(
            task_id=task_id,
            user_id=request.user_info.id
        )
    except TaskNotFoundException as e:
        return jsonify({'code': 404, 'message': str(e)}), 404

    return jsonify({
        'code': 200,
        'message': '任务删除成功',
        'data': result
    })


@task_bp.route('/<int:task_id>/client', methods=['PATCH'])
@login_required
def update_task_client_api(task_id):
    """更新任务关联的客户端"""
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    try:
        result = update_client(
            task_id=task_id,
            user_id=request.user_info.id,
            client_id=data.get('client_id', 0)
        )
    except TaskValidationException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except TaskNotFoundException as e:
        return jsonify({'code': 404, 'message': str(e)}), 404

    return jsonify({
        'code': 200,
        'message': '客户端更新成功',
        'data': result
    })


@task_bp.route('/<int:task_id>/review', methods=['POST'])
@login_required
def review_task_api(task_id):
    """审核任务（通过/修订）"""
    data = request.get_json()
    
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400
    
    try:
        result = review_task(
            task_id=task_id,
            user_id=request.user_info.id,
            action=data.get('action', ''),
            feedback=data.get('feedback')
        )
    except TaskValidationException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except TaskNotFoundException as e:
        return jsonify({'code': 404, 'message': str(e)}), 404
    
    return jsonify({
        'code': 200,
        'message': result.get('message', '操作成功'),
        'data': result
    })


@task_bp.route('/sync_execute', methods=['POST'])
@login_required
def sync_execute_api():
    """
    同步执行信息到 ai_task_tasks.extra
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    develop_doc = data.get('develop_doc', '')
    merge_request = data.get('merge_request', [])

    if not task_id:
        return jsonify({'code': 400, 'message': 'task_id不能为空'}), 400
    if merge_request is None or not isinstance(merge_request, list):
        return jsonify({'code': 400, 'message': 'merge_request必须是数组'}), 400

    if not get_task_by_id(task_id=task_id, user_id=request.user_info.id):
        return jsonify({'code': 400, 'message': '任务不存在'}), 400

    try:
        result = sync_execute(
            task_id=int(task_id),
            user_id=request.user_info.id,
            develop_doc=develop_doc,
            merge_request=merge_request
        )
    except TaskNotFoundException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except TaskValidationException as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

    return jsonify({
        'code': 200,
        'message': '同步成功',
        'data': result
    })
