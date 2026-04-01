#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Chat相关路由
"""

from flask import Blueprint, request, jsonify

from routes.auth_plugin import login_required
from dao.models import Chat, ChatMessage
from dao.chat_dao import (
    create_chat, get_chats_by_task, get_chat_by_id, update_chat_status, delete_chat,
    create_chat_message, get_messages_by_chat, get_running_message, 
    soft_delete_message, update_message, update_chat_sessionid, get_message_by_id
)
from dao.task_dao import get_task_by_id

chat_bp = Blueprint('chat', __name__)

@chat_bp.route('/task/<int:task_id>/chats', methods=['GET'])
@login_required
def list_chats(task_id):
    """获取任务的Chat列表"""
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    chats = get_chats_by_task(task_id)
    return jsonify({'code': 200, 'message': '获取成功', 'data': chats})


@chat_bp.route('/task/<int:task_id>/chats', methods=['POST'])
@login_required
def create_chat_api(task_id):
    """创建Chat"""
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    title = data.get('title', '').strip()
    if not title:
        return jsonify({'code': 400, 'message': '标题不能为空'}), 400
    if len(title) > 32:
        return jsonify({'code': 400, 'message': '标题最多32个字符'}), 400

    sessionid = data.get('sessionid', '')
    if sessionid and len(sessionid) > 64:
        return jsonify({'code': 400, 'message': 'sessionid最多64个字符'}), 400

    try:
        chat = create_chat(task_id, title, sessionid or None)
    except Exception as e:
        if 'Duplicate' in str(e) or 'UNIQUE' in str(e):
            return jsonify({'code': 400, 'message': '该任务下已存在同名Chat'}), 400
        return jsonify({'code': 500, 'message': str(e)}), 500

    return jsonify({'code': 201, 'message': 'Chat创建成功', 'data': chat.to_dict()}), 201


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>', methods=['GET'])
@login_required
def get_chat_api(task_id, chat_id):
    """获取Chat详情"""
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    chat = get_chat_by_id(chat_id, task_id)
    if not chat:
        return jsonify({'code': 404, 'message': 'Chat不存在'}), 404

    running_msg = get_running_message(chat_id, task_id)
    data = chat.to_dict()
    data['has_running_message'] = running_msg is not None

    return jsonify({'code': 200, 'message': '获取成功', 'data': data})


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/status', methods=['PATCH'])
@login_required
def update_chat_status_api(task_id, chat_id):
    """更新Chat状态"""
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    status = data.get('status', '')
    from dao.models import Chat
    if status not in [Chat.STATUS_PENDING, Chat.STATUS_RUNNING, Chat.STATUS_COMPLETED, Chat.STATUS_TERMINATED]:
        return jsonify({'code': 400, 'message': '无效的状态值'}), 400

    ok = update_chat_status(chat_id, task_id, status)
    if not ok:
        return jsonify({'code': 404, 'message': 'Chat不存在'}), 404

    return jsonify({'code': 200, 'message': '状态更新成功'})


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>', methods=['DELETE'])
@login_required
def delete_chat_api(task_id, chat_id):
    """删除Chat"""
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    ok = delete_chat(chat_id, task_id)
    if not ok:
        return jsonify({'code': 404, 'message': 'Chat不存在'}), 404

    return jsonify({'code': 200, 'message': '删除成功'})


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/messages', methods=['GET'])
@login_required
def list_messages(task_id, chat_id):
    """获取Chat的消息列表"""
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    chat = get_chat_by_id(chat_id, task_id)
    if not chat:
        return jsonify({'code': 404, 'message': 'Chat不存在'}), 404

    messages = get_messages_by_chat(chat_id, task_id)
    return jsonify({'code': 200, 'message': '获取成功', 'data': messages})


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/messages', methods=['POST'])
@login_required
def create_message_api(task_id, chat_id):
    """创建Chat消息（发送输入）"""
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    chat = get_chat_by_id(chat_id, task_id)
    if not chat:
        return jsonify({'code': 404, 'message': 'Chat不存在'}), 404

    running = get_running_message(chat_id, task_id)
    if running:
        return jsonify({'code': 400, 'message': '当前Chat有正在执行的消息，请等待完成或终止后再发送'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    input_text = data.get('input', '').strip()
    if not input_text:
        return jsonify({'code': 400, 'message': '输入内容不能为空'}), 400

    extra = data.get('extra', {})

    try:
        msg = create_chat_message(task_id, chat_id, input_text, extra)
        update_chat_status(chat_id, task_id, 'pending')
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500

    return jsonify({'code': 201, 'message': '消息创建成功', 'data': msg.to_dict()}), 201


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/messages/<int:message_id>', methods=['DELETE'])
@login_required
def delete_message_api(task_id, chat_id, message_id):
    """
    用户终止：软删除该消息（deleted=1），Chat状态重置为completed，
    返回被删除消息的input内容用于前端回填输入框。
    """
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    input_text = soft_delete_message(message_id, chat_id, task_id)
    if input_text is None:
        return jsonify({'code': 404, 'message': '消息不存在或已删除'}), 404

    update_chat_status(chat_id, task_id, 'completed')

    return jsonify({
        'code': 200,
        'message': '消息已撤销',
        'data': {'input': input_text}
    })


@chat_bp.route('/msg/sync_execute', methods=['POST'])
@login_required
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

    if not task_id:
        return jsonify({'code': 400, 'message': 'task_id不能为空'}), 400
    if not chat_id:
        return jsonify({'code': 400, 'message': 'chat_id不能为空'}), 400
    if not message_id:
        return jsonify({'code': 400, 'message': 'message_id不能为空'}), 400
    if merge_request is None or not isinstance(merge_request, list):
        return jsonify({'code': 400, 'message': 'merge_request必须是数组'}), 400

    # 校验任务归属
    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 400, 'message': '任务不存在'}), 400

    extra = {
        'develop_doc': develop_doc,
        'merge_request': merge_request
    }
    update_message(task_id=int(task_id), chat_id=int(chat_id), message_id=int(message_id), extra=extra, status=ChatMessage.STATUS_COMPLETED)
    update_chat_status(chat_id=int(chat_id), task_id=int(task_id), status=Chat.STATUS_COMPLETED)

    return jsonify({'code': 200, 'message': '同步成功'})


@chat_bp.route('/update_chat_status', methods=['POST'])
@login_required
def update_chat_status_by_client_api():
    """
    客户端更新 Chat 状态（running / completed / terminated）
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    chat_id = data.get('chat_id')
    status = data.get('status', '')

    if not task_id:
        return jsonify({'code': 400, 'message': 'task_id不能为空'}), 400
    if not chat_id:
        return jsonify({'code': 400, 'message': 'chat_id不能为空'}), 400

    from dao.models import Chat
    if status not in [Chat.STATUS_PENDING, Chat.STATUS_RUNNING, Chat.STATUS_COMPLETED, Chat.STATUS_TERMINATED]:
        return jsonify({'code': 400, 'message': '无效的状态值'}), 400

    task = get_task_by_id(task_id=task_id, user_id=request.user_info.id)
    if not task:
        return jsonify({'code': 404, 'message': '任务不存在'}), 404

    ok = update_chat_status(int(chat_id), int(task_id), status)
    if not ok:
        return jsonify({'code': 404, 'message': 'Chat不存在'}), 404

    return jsonify({'code': 200, 'message': '状态更新成功'})


@chat_bp.route('/msg/update_message_status', methods=['POST'])
@login_required
def update_message_status_by_client_api():
    """
    客户端更新 Message 状态（running / completed / terminated）
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    status = data.get('status', '')
    if not _check_releation(user_id=request.user_info.id, task_id=task_id, chat_id=chat_id, message_id=message_id):
        return jsonify({'code': 400, 'message': '参数校验不通过，请检查task_id、chat_id、message_id是否正确'}), 400

    valid_statuses = [ChatMessage.STATUS_RUNNING, ChatMessage.STATUS_COMPLETED, ChatMessage.STATUS_TERMINATED]
    if status not in valid_statuses:
        return jsonify({'code': 400, 'message': '无效的状态值'}), 400

    ok = update_message(task_id=task_id, chat_id=chat_id, message_id=message_id, status=status)
    if not ok:
        return jsonify({'code': 400, 'message': '消息不存在或已删除'}), 400
    ok = update_chat_status(chat_id=chat_id, task_id=task_id, status=status)
    if not ok:
        return jsonify({'code': 400, 'message': 'Chat不存在或已删除'}), 400

    return jsonify({'code': 200, 'message': '状态更新成功'})


@chat_bp.route('/msg/agent_reply', methods=['POST'])
@login_required
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
    if not _check_releation(user_id=request.user_info.id, task_id=task_id, chat_id=chat_id, message_id=message_id):
        return jsonify({'code': 400, 'message': '参数校验不通过，请检查task_id、chat_id、message_id是否正确'}), 400

    if not isinstance(reply, str):
        reply = str(reply)
    if session_id is not None and not isinstance(session_id, str):
        session_id = str(session_id)
    if session_id is not None and len(session_id) > 64:
        return jsonify({'code': 400, 'message': 'sessionId最多64个字符'}), 400

    ok_msg = update_message(task_id=task_id, chat_id=chat_id, message_id=message_id, output=reply)
    if not ok_msg:
        return jsonify({'code': 400, 'message': '消息不存在或已删除'}), 400

    if session_id:
        ok_session = update_chat_sessionid(task_id=task_id, chat_id=chat_id, sessionid=session_id)
        if not ok_session:
            return jsonify({'code': 400, 'message': 'Chat不存在或已删除'}), 400
    return jsonify({'code': 200, 'message': '同步成功'})

def _check_releation(user_id: int, task_id: int | None, chat_id: int | None, message_id: int | None) -> bool:
    """
    校验任务、Chat、消息的归属，返回True或False
    """
    if task_id:
        if not get_task_by_id(task_id=task_id, user_id=user_id):
            return False
    if chat_id:
        if not task_id:
            raise ValueError('task_id不能为空')
        if not get_chat_by_id(chat_id=chat_id, task_id=task_id):
            return False
    if message_id:
        if not chat_id:
            raise ValueError('chat_id不能为空')
        if not get_message_by_id(message_id=message_id, chat_id=chat_id):
            return False
    return True
