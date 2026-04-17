#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Chat相关路由（Client RPC 调用）
"""

import logging

from flask import Blueprint, request, jsonify

from dao.models import Chat, ChatMessage
from dao.chat_dao import (
    get_chat_by_id, update_chat_status,
    get_message_by_id, update_message, update_chat_sessionid,
)
from service.deploy_service import auto_create_test_deploy_on_message_sync

chat_bp = Blueprint('open_chat', __name__)

logger = logging.getLogger(__name__)


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

    msg = get_message_by_id(user_id=request.user_info.user_id, message_id=message_id, chat_id=chat_id, task_id=task_id)
    if not msg:
        return jsonify({'code': 400, 'message': '消息不存在'}), 400

    extra = dict(msg.extra or {})
    extra['develop_doc'] = develop_doc
    extra['merge_request'] = merge_request
    update_message(
        user_id=request.user_info.user_id,
        task_id=int(task_id),
        chat_id=int(chat_id),
        message_id=int(message_id),
        extra=extra,
        status=ChatMessage.STATUS_COMPLETED,
    )
    update_chat_status(
        user_id=request.user_info.user_id,
        chat_id=int(chat_id),
        task_id=int(task_id),
        status=Chat.STATUS_COMPLETED,
    )

    # after_execute 同步 merge_request 时，自动 upsert 一条测试环境发布记录
    # （merge_request 为空或无 diff 时会被 service 层自行跳过）
    try:
        auto_create_test_deploy_on_message_sync(
            user_id=request.user_info.user_id,
            task_id=int(task_id or 0),
            chat_id=int(chat_id),
            message_id=int(message_id),
            merge_request=merge_request or [],
        )
    except Exception:
        # 自动发布失败不阻塞客户端同步流程，仅记录日志
        logger.exception(
            'auto test deploy failed on msg sync_execute: task_id=%s chat_id=%s msg_id=%s',
            task_id, chat_id, message_id,
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
    if not get_chat_by_id(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    if status not in Chat.STATUS_TEXT:
        return jsonify({'code': 400, 'message': '无效的状态值'}), 400

    update_chat_status(user_id=request.user_info.user_id, chat_id=int(chat_id), task_id=int(task_id), status=status)
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
    if not get_message_by_id(user_id=request.user_info.user_id, message_id=message_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': '消息不存在'}), 400
    update_message(user_id=request.user_info.user_id, task_id=task_id, chat_id=chat_id, message_id=message_id, status=status)
    update_chat_status(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id, status=status)
    return jsonify({'code': 200, 'message': '状态更新成功'})


@chat_bp.route('/msg/agent_reply', methods=['POST'])
def agent_reply_message_api():
    """
    同步 agent 执行结果到数据库：
    - 写入 ai_task_chat_message.output = agent reply
    - 写入 ai_task_chat.sessionid = agent sessionId
    - 将 ai_task_chat_message.status 设为 completed，并将 Chat 设为 completed
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    chat_id = data.get('chat_id')
    message_id = data.get('message_id')
    reply = data.get('reply', '')
    session_id = data.get('session_id', None)
    if not get_message_by_id(user_id=request.user_info.user_id, message_id=message_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': '消息不存在'}), 400

    if not isinstance(reply, str):
        reply = str(reply)
    if session_id is not None and not isinstance(session_id, str):
        session_id = str(session_id)
    if session_id is not None and len(session_id) > 64:
        return jsonify({'code': 400, 'message': 'sessionId最多64个字符'}), 400

    ok_msg = update_message(
        user_id=request.user_info.user_id,
        task_id=task_id,
        chat_id=chat_id,
        message_id=message_id,
        output=reply,
        status=ChatMessage.STATUS_COMPLETED,
    )
    if not ok_msg:
        return jsonify({'code': 400, 'message': '消息不存在或已删除'}), 400

    update_chat_status(
        user_id=request.user_info.user_id,
        chat_id=int(chat_id),
        task_id=int(task_id),
        status=Chat.STATUS_COMPLETED,
    )

    if session_id:
        ok_session = update_chat_sessionid(user_id=request.user_info.user_id, task_id=task_id, chat_id=chat_id, sessionid=session_id)
        if not ok_session:
            return jsonify({'code': 400, 'message': 'Chat不存在或已删除'}), 400
    return jsonify({'code': 200, 'message': '同步成功'})
