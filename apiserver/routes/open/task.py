#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务相关路由（Client RPC 调用）
"""

from flask import Blueprint, request, jsonify

from service.task_service import get_task, sync_execute

task_bp = Blueprint('open_task', __name__)


@task_bp.route('/<int:task_id>', methods=['GET'])
def get_task_info(task_id):
    """获取任务详情（供客户端查询任务信息）"""
    task = get_task(task_id=task_id, user_id=request.user_info.user_id)
    return jsonify({'code': 200, 'message': '获取任务成功', 'data': task})


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
        user_id=request.user_info.user_id,
        develop_doc=develop_doc,
        merge_request=merge_request
    )

    return jsonify({'code': 200, 'message': '同步成功', 'data': result})
