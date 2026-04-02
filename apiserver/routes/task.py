#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务相关路由
"""

from flask import Blueprint, request, jsonify

from service.task_service import (
    create_task, get_tasks, get_task, update_status, delete_task,
    sync_execute
)
from dao.task_dao import get_task_by_id

task_bp = Blueprint('task', __name__)


@task_bp.route('', methods=['POST'])
def create_task_api():
    """创建任务"""
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    task = create_task(
        user_id=request.user_info.id,
        title=data.get('title', ''),
        client_id=data.get('client_id'),
        status=data.get('status')
    )

    return jsonify({
        'code': 201,
        'message': '任务创建成功',
        'data': task.to_dict()
    }), 201


@task_bp.route('', methods=['GET'])
def list_tasks():
    """获取任务列表，支持按状态过滤"""
    status = request.args.get('status')  # 可选查询参数

    tasks = get_tasks(request.user_info.id, status)

    return jsonify({
        'code': 200,
        'message': '获取任务列表成功',
        'data': tasks
    })


@task_bp.route('/<int:task_id>', methods=['GET'])
def get_task_info(task_id):
    """获取任务详情"""
    task = get_task(task_id=task_id, user_id=request.user_info.id)
    
    return jsonify({
        'code': 200,
        'message': '获取任务成功',
        'data': task  # get_task 已返回处理后的字典
    })


@task_bp.route('/<int:task_id>/status', methods=['PATCH'])
def update_task_status_api(task_id):
    """更新任务状态"""
    data = request.get_json()
    
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400
    
    result = update_status(
        task_id=task_id,
        user_id=request.user_info.id,
        status=data.get('status', '')
    )
    
    return jsonify({
        'code': 200,
        'message': '状态更新成功',
        'data': result
    })


@task_bp.route('/<int:task_id>', methods=['DELETE'])
def delete_task_api(task_id):
    """删除任务"""
    result = delete_task(
        task_id=task_id,
        user_id=request.user_info.id
    )

    return jsonify({
        'code': 200,
        'message': '任务删除成功',
        'data': result
    })


@task_bp.route('/sync_execute', methods=['POST'])
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

    result = sync_execute(
        task_id=int(task_id),
        user_id=request.user_info.id,
        develop_doc=develop_doc,
        merge_request=merge_request
    )

    return jsonify({
        'code': 200,
        'message': '同步成功',
        'data': result
    })
