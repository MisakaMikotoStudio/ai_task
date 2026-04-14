#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务相关路由（Web 前端调用）
"""

from flask import Blueprint, request, jsonify

from service.task_service import (
    create_task, get_tasks, get_task, update_status, delete_task,
    TaskValidationException
)

task_bp = Blueprint('app_task', __name__)


@task_bp.route('', methods=['POST'])
def create_task_api():
    """创建任务"""
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    task = create_task(
        user_id=request.user_info.user_id,
        title=data.get('title', ''),
        client_id=data.get('client_id'),
        status=data.get('status')
    )

    return jsonify({'code': 201, 'message': '任务创建成功', 'data': task.to_dict()}), 201


@task_bp.route('', methods=['GET'])
def list_tasks():
    """获取任务列表，支持按状态过滤"""
    status_param = (request.args.get('status') or '').strip()
    statuses = [item.strip() for item in status_param.split(',') if item.strip()] if status_param else None

    page = int(request.args.get('page', 1))
    page_num = int(request.args.get('pageNum', 20))
    tasks = get_tasks(
        user_id=request.user_info.user_id,
        statuses=statuses,
        page=page,
        page_num=page_num
    )
    return jsonify({'code': 200, 'message': '获取任务列表成功', 'data': tasks})


@task_bp.route('/<int:task_id>', methods=['GET'])
def get_task_info(task_id):
    """获取任务详情"""
    task = get_task(task_id=task_id, user_id=request.user_info.user_id)

    return jsonify({'code': 200, 'message': '获取任务成功', 'data': task})


@task_bp.route('/<int:task_id>/status', methods=['PATCH'])
def update_task_status_api(task_id):
    """更新任务状态"""
    data = request.get_json()

    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    result = update_status(task_id=task_id, user_id=request.user_info.user_id, status=data.get('status', ''))

    return jsonify({'code': 200, 'message': '状态更新成功', 'data': result})


@task_bp.route('/<int:task_id>', methods=['DELETE'])
def delete_task_api(task_id):
    """删除任务"""
    result = delete_task(task_id=task_id, user_id=request.user_info.user_id)

    return jsonify({'code': 200, 'message': '任务删除成功', 'data': result})
