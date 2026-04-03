#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Chat相关路由
"""

from flask import Blueprint, request, jsonify

from dao.models import Chat, ChatMessage
from dao.chat_dao import (
    create_chat, get_chats_by_task, get_chat_by_id, update_chat_status, soft_delete_chat,
    create_chat_message, get_messages_by_chat, get_running_message, 
    soft_delete_message, update_message, update_chat_sessionid, get_message_by_id
)
from dao.task_dao import get_task_by_id

chat_bp = Blueprint('chat', __name__)

@chat_bp.route('/task/<int:task_id>/chats', methods=['GET'])
def list_chats(task_id):
    """获取任务的Chat列表"""
    chats = get_chats_by_task(user_id=request.user_info.id, task_id=task_id)
    return jsonify({'code': 200, 'message': '获取成功', 'data': chats})


@chat_bp.route('/task/<int:task_id>/chats', methods=['POST'])
def create_chat_api(task_id):
    """创建Chat"""
    if not get_task_by_id(task_id=task_id, user_id=request.user_info.id):
        return jsonify({'code': 400, 'message': '任务不存在'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    title = data.get('title', '').strip()
    if not title:
        return jsonify({'code': 400, 'message': '标题不能为空'}), 400
    if len(title) > 32:
        return jsonify({'code': 400, 'message': '标题最多32个字符'}), 400

    chat = create_chat(user_id=request.user_info.id, task_id=task_id, title=title)

    return jsonify({'code': 201, 'message': 'Chat创建成功', 'data': chat.to_dict()}), 201


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/status', methods=['PATCH'])
def update_chat_status_api(task_id, chat_id):
    """更新Chat状态"""
    if not get_chat_by_id(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    status = data.get('status', '')
    if status not in [Chat.STATUS_PENDING, Chat.STATUS_RUNNING, Chat.STATUS_COMPLETED, Chat.STATUS_TERMINATED]:
        return jsonify({'code': 400, 'message': '无效的状态值'}), 400

    ok = update_chat_status(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id, status=status)
    if not ok:
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    return jsonify({'code': 200, 'message': '状态更新成功'})


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>', methods=['DELETE'])
def delete_chat_api(task_id, chat_id):
    """删除Chat"""
    if not get_chat_by_id(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400
    ok = soft_delete_chat(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id)
    if not ok:
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    return jsonify({'code': 200, 'message': '删除成功'})


@chat_bp.route('/task/<int:task_id>/messages', methods=['POST'])
def create_chat_and_message_api(task_id):
    """自动创建Chat并发送消息（Chat标题取输入内容前32字符）"""
    if not get_task_by_id(task_id=task_id, user_id=request.user_info.id):
        return jsonify({'code': 400, 'message': '任务不存在'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    input_text = data.get('input', '').strip()
    if not input_text:
        return jsonify({'code': 400, 'message': '输入内容不能为空'}), 400

    title = input_text[:32]
    extra = data.get('extra', {})

    chat = create_chat(user_id=request.user_info.id, task_id=task_id, title=title)
    msg = create_chat_message(
        user_id=request.user_info.id,
        task_id=task_id,
        chat_id=chat.id,
        input_text=input_text,
        extra=extra,
    )
    update_chat_status(user_id=request.user_info.id, chat_id=chat.id, task_id=task_id, status='pending')

    return jsonify({
        'code': 201,
        'message': '创建成功',
        'data': {
            'chat': chat.to_dict(),
            'message': msg.to_dict()
        }
    }), 201


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/messages', methods=['GET'])
def list_messages(task_id, chat_id):
    """获取Chat的消息列表"""
    messages = get_messages_by_chat(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id)
    return jsonify({'code': 200, 'message': '获取成功', 'data': messages})


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/messages', methods=['POST'])
def create_message_api(task_id, chat_id):
    """创建Chat消息（发送输入）"""
    chat = get_chat_by_id(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id)
    if not chat:
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    running = get_running_message(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id)
    if running:
        return jsonify({'code': 400, 'message': '当前Chat有正在执行的消息，请等待完成或终止后再发送'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    input_text = data.get('input', '').strip()
    if not input_text:
        return jsonify({'code': 400, 'message': '输入内容不能为空'}), 400

    extra = data.get('extra', {})

    msg = create_chat_message(
        user_id=request.user_info.id,
        task_id=task_id,
        chat_id=chat_id,
        input_text=input_text,
        extra=extra,
    )
    update_chat_status(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id, status='pending')

    return jsonify({'code': 201, 'message': '消息创建成功', 'data': msg.to_dict()}), 201


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/messages/<int:message_id>', methods=['DELETE'])
def delete_message_api(task_id, chat_id, message_id):
    """
    用户终止：软删除该消息（设置 deleted_at），Chat状态重置为completed，
    返回被删除消息的input内容用于前端回填输入框。
    """
    if not get_message_by_id(user_id=request.user_info.id, message_id=message_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': '消息不存在'}), 400
    input_text = soft_delete_message(user_id=request.user_info.id, message_id=message_id, chat_id=chat_id, task_id=task_id)
    if input_text is None:
        return jsonify({'code': 400, 'message': '消息不存在或已删除'}), 400

    update_chat_status(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id, status='completed')

    return jsonify({
        'code': 200,
        'message': '消息已撤销',
        'data': {'input': input_text}
    })


@chat_bp.route('/msg/sync_execute', methods=['POST'])
def sync_execute_message_api():
    """
    同步执行结果到 ChatMessage：
    - develop_doc/merge_request 写入 extra
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    develop_doc = data.get('develop_doc', '') or ''
    merge_request = data.get('merge_request', [])

    if not get_message_by_id(user_id=request.user_info.id, message_id=message_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': '消息不存在'}), 400


    extra = {
        'develop_doc': develop_doc,
        'merge_request': merge_request
    }
    update_message(
        user_id=request.user_info.id,
        task_id=int(task_id),
        chat_id=int(chat_id),
        message_id=int(message_id),
        extra=extra,
        status=ChatMessage.STATUS_COMPLETED,
    )
    update_chat_status(
        user_id=request.user_info.id,
        chat_id=int(chat_id),
        task_id=int(task_id),
        status=Chat.STATUS_COMPLETED,
    )

    return jsonify({'code': 200, 'message': '同步成功'})


@chat_bp.route('/update_chat_status', methods=['POST'])
def update_chat_status_by_client_api():
    """
    客户端更新 Chat 状态（running / completed / terminated）
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    chat_id = data.get('chat_id')
    status = data.get('status', '')
    if not get_chat_by_id(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    if status not in Chat.STATUS_TEXT:
        return jsonify({'code': 400, 'message': '无效的状态值'}), 400

    update_chat_status(user_id=request.user_info.id, chat_id=int(chat_id), task_id=int(task_id), status=status)
    return jsonify({'code': 200, 'message': '状态更新成功'})


@chat_bp.route('/msg/update_message_status', methods=['POST'])
def update_message_status_by_client_api():
    """
    客户端更新 Message 状态（running / completed / terminated）
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    status = data.get('status', '')
    if not get_message_by_id(user_id=request.user_info.id, message_id=message_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': '消息不存在'}), 400
    update_message(user_id=request.user_info.id, task_id=task_id, chat_id=chat_id, message_id=message_id, status=status)
    update_chat_status(user_id=request.user_info.id, chat_id=chat_id, task_id=task_id, status=status)
    return jsonify({'code': 200, 'message': '状态更新成功'})


@chat_bp.route('/msg/agent_reply', methods=['POST'])
def agent_reply_message_api():
    """
    同步 agent 执行结果到数据库：
    - 写入 ai_task_chat_message.output = agent reply
    - 写入 ai_task_chat.sessionid = agent sessionId
    - 如果 reply 或 sessionId 非空，则将 ai_task_chat_message.status 设为 completed
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    reply = data.get('reply', '')
    session_id = data.get('session_id', None)
    if not get_message_by_id(user_id=request.user_info.id, message_id=message_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': '消息不存在'}), 400

    if not isinstance(reply, str):
        reply = str(reply)
    if session_id is not None and not isinstance(session_id, str):
        session_id = str(session_id)
    if session_id is not None and len(session_id) > 64:
        return jsonify({'code': 400, 'message': 'sessionId最多64个字符'}), 400

    ok_msg = update_message(user_id=request.user_info.id, task_id=task_id, chat_id=chat_id, message_id=message_id, output=reply)
    if not ok_msg:
        return jsonify({'code': 400, 'message': '消息不存在或已删除'}), 400

    if session_id:
        ok_session = update_chat_sessionid(user_id=request.user_info.id, task_id=task_id, chat_id=chat_id, sessionid=session_id)
        if not ok_session:
            return jsonify({'code': 400, 'message': 'Chat不存在或已删除'}), 400
    return jsonify({'code': 200, 'message': '同步成功'})

