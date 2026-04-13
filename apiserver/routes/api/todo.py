#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
待办事项路由
"""

from flask import Blueprint, request, jsonify

from service.todo_service import create_todo, get_todos, update_todo, delete_todo

todo_bp = Blueprint('todo', __name__)


@todo_bp.route('', methods=['GET'])
def list_todos():
    """获取待办列表"""
    todos = get_todos(request.user_info.user_id)
    return jsonify({'code': 200, 'message': '获取待办列表成功', 'data': todos})


@todo_bp.route('', methods=['POST'])
def create_todo_api():
    """创建待办"""
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    todo = create_todo(user_id=request.user_info.user_id, content=data.get('content', ''))

    return jsonify({'code': 201, 'message': '待办创建成功', 'data': todo}), 201


@todo_bp.route('/<int:todo_id>', methods=['PATCH'])
def update_todo_api(todo_id):
    """更新待办"""
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    todo = update_todo(
        todo_id=todo_id,
        user_id=request.user_info.user_id,
        content=data.get('content'),
        completed=data.get('completed')
    )

    return jsonify({'code': 200, 'message': '待办更新成功', 'data': todo})


@todo_bp.route('/<int:todo_id>', methods=['DELETE'])
def delete_todo_api(todo_id):
    """删除待办"""
    result = delete_todo(todo_id=todo_id, user_id=request.user_info.user_id)

    return jsonify({'code': 200, 'message': '待办删除成功', 'data': result})
