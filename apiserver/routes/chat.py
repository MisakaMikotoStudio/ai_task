#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Chat相关路由
"""

import logging
import os

from flask import Blueprint, request, jsonify, current_app

from dao.models import Chat, ChatMessage
from dao.chat_dao import (
    create_chat, get_chats_by_task, get_chat_by_id, update_chat_status, soft_delete_chat,
    create_chat_message, get_messages_by_chat, get_running_message,
    soft_delete_message, update_message, update_chat_sessionid, get_message_by_id,
    get_standalone_chats
)
from dao.task_dao import get_task_by_id
from dao.client_dao import get_client_by_id

chat_bp = Blueprint('chat', __name__)


# ===== 独立 Chat（task_id=0）接口 =====

@chat_bp.route('/standalone/chats', methods=['GET'])
def list_standalone_chats():
    """获取独立Chat列表（task_id=0），支持分页和状态筛选"""
    page = int(request.args.get('page', 1))
    page_num = int(request.args.get('pageNum', 20))
    status_param = (request.args.get('status') or '').strip()
    statuses = [s.strip() for s in status_param.split(',') if s.strip()] if status_param else None

    data = get_standalone_chats(
        user_id=request.user_info.user_id,
        statuses=statuses,
        page=page,
        page_num=page_num,
    )
    return jsonify({'code': 200, 'message': '获取成功', 'data': data})


@chat_bp.route('/standalone/messages', methods=['POST'])
def create_standalone_chat_and_message_api():
    """自动创建独立Chat并发送消息（task_id=0）"""
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    input_text = data.get('input', '').strip()
    if not input_text:
        return jsonify({'code': 400, 'message': '输入内容不能为空'}), 400

    client_id = data.get('client_id')
    if not client_id:
        return jsonify({'code': 400, 'message': '必须选择一个应用'}), 400

    if not get_client_by_id(client_id=client_id, user_id=request.user_info.user_id):
        return jsonify({'code': 400, 'message': '应用不存在'}), 400

    title = input_text[:32]
    extra = data.get('extra', {})

    chat = create_chat(
        user_id=request.user_info.user_id,
        task_id=0,
        title=title,
        client_id=int(client_id),
    )
    msg = create_chat_message(
        user_id=request.user_info.user_id,
        task_id=0,
        chat_id=chat.id,
        input_text=input_text,
        extra=extra,
    )
    update_chat_status(user_id=request.user_info.user_id, chat_id=chat.id, task_id=0, status='pending')

    return jsonify({
        'code': 201,
        'message': '创建成功',
        'data': {
            'chat': chat.to_dict(),
            'message': msg.to_dict()
        }
    }), 201


# ===== 原有 Task Chat 接口 =====

@chat_bp.route('/task/<int:task_id>/chats', methods=['GET'])
def list_chats(task_id):
    """获取任务的Chat列表"""
    chats = get_chats_by_task(user_id=request.user_info.user_id, task_id=task_id)
    return jsonify({'code': 200, 'message': '获取成功', 'data': chats})


@chat_bp.route('/task/<int:task_id>/chats', methods=['POST'])
def create_chat_api(task_id):
    """创建Chat"""
    if not get_task_by_id(task_id=task_id, user_id=request.user_info.user_id):
        return jsonify({'code': 400, 'message': '任务不存在'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    title = data.get('title', '').strip()
    if not title:
        return jsonify({'code': 400, 'message': '标题不能为空'}), 400
    if len(title) > 32:
        return jsonify({'code': 400, 'message': '标题最多32个字符'}), 400

    chat = create_chat(user_id=request.user_info.user_id, task_id=task_id, title=title)

    return jsonify({'code': 201, 'message': 'Chat创建成功', 'data': chat.to_dict()}), 201


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/status', methods=['PATCH'])
def update_chat_status_api(task_id, chat_id):
    """更新Chat状态"""
    if not get_chat_by_id(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    status = data.get('status', '')
    if status not in [Chat.STATUS_PENDING, Chat.STATUS_RUNNING, Chat.STATUS_COMPLETED, Chat.STATUS_TERMINATED]:
        return jsonify({'code': 400, 'message': '无效的状态值'}), 400

    ok = update_chat_status(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id, status=status)
    if not ok:
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    return jsonify({'code': 200, 'message': '状态更新成功'})


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>', methods=['DELETE'])
def delete_chat_api(task_id, chat_id):
    """删除Chat"""
    if not get_chat_by_id(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400
    ok = soft_delete_chat(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id)
    if not ok:
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    return jsonify({'code': 200, 'message': '删除成功'})


@chat_bp.route('/task/<int:task_id>/messages', methods=['POST'])
def create_chat_and_message_api(task_id):
    """自动创建Chat并发送消息（Chat标题取输入内容前32字符）"""
    if not get_task_by_id(task_id=task_id, user_id=request.user_info.user_id):
        return jsonify({'code': 400, 'message': '任务不存在'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    input_text = data.get('input', '').strip()
    if not input_text:
        return jsonify({'code': 400, 'message': '输入内容不能为空'}), 400

    title = input_text[:32]
    extra = data.get('extra', {})

    chat = create_chat(user_id=request.user_info.user_id, task_id=task_id, title=title)
    msg = create_chat_message(
        user_id=request.user_info.user_id,
        task_id=task_id,
        chat_id=chat.id,
        input_text=input_text,
        extra=extra,
    )
    update_chat_status(user_id=request.user_info.user_id, chat_id=chat.id, task_id=task_id, status='pending')

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
    messages = get_messages_by_chat(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id)
    return jsonify({'code': 200, 'message': '获取成功', 'data': messages})


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/messages', methods=['POST'])
def create_message_api(task_id, chat_id):
    """创建Chat消息（发送输入）"""
    chat = get_chat_by_id(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id)
    if not chat:
        return jsonify({'code': 400, 'message': 'Chat不存在'}), 400

    running = get_running_message(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id)
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
        user_id=request.user_info.user_id,
        task_id=task_id,
        chat_id=chat_id,
        input_text=input_text,
        extra=extra,
    )
    update_chat_status(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id, status='pending')

    return jsonify({'code': 201, 'message': '消息创建成功', 'data': msg.to_dict()}), 201


@chat_bp.route('/task/<int:task_id>/chats/<int:chat_id>/messages/<int:message_id>', methods=['DELETE'])
def delete_message_api(task_id, chat_id, message_id):
    """
    用户终止：软删除该消息（设置 deleted_at），Chat状态重置为completed，
    返回被删除消息的input内容用于前端回填输入框。
    """
    if not get_message_by_id(user_id=request.user_info.user_id, message_id=message_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': '消息不存在'}), 400
    input_text = soft_delete_message(user_id=request.user_info.user_id, message_id=message_id, chat_id=chat_id, task_id=task_id)
    if input_text is None:
        return jsonify({'code': 400, 'message': '消息不存在或已删除'}), 400

    update_chat_status(user_id=request.user_info.user_id, chat_id=chat_id, task_id=task_id, status='completed')

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

    if not get_message_by_id(user_id=request.user_info.user_id, message_id=message_id, chat_id=chat_id, task_id=task_id):
        return jsonify({'code': 400, 'message': '消息不存在'}), 400


    extra = {
        'develop_doc': develop_doc,
        'merge_request': merge_request
    }
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
    - 将 ai_task_chat_message.status 设为 completed，并将 Chat 设为 completed（避免客户端仍从 running 列表拉取）
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


# ===== 图片上传/下载接口 =====

logger = logging.getLogger(__name__)

MAX_CHAT_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}


@chat_bp.route('/upload/image', methods=['POST'])
def upload_chat_image_api():
    """
    上传聊天图片到 OSS（私有读写）。
    登录 + 订阅校验由全局 auth_plugin 中间件统一处理。
    """
    from service import oss_service

    user = request.user_info
    config = current_app.config['APP_CONFIG']

    file = request.files.get('file')
    if not file:
        return jsonify({'code': 400, 'message': '缺少 file 字段'}), 400

    if file.content_type not in ALLOWED_IMAGE_TYPES:
        return jsonify({'code': 400, 'message': '仅支持 jpg/png/gif/webp 格式'}), 400

    # 检查文件大小
    try:
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
    except OSError:
        file_size = None
    if file_size is not None and file_size > MAX_CHAT_IMAGE_BYTES:
        return jsonify({'code': 400, 'message': '图片大小不能超过 10MB'}), 400

    try:
        result = oss_service.upload_chat_image(
            config=config.oss,
            file_storage=file,
            user_id=user.user_id,
        )
    except Exception as e:
        logger.exception("聊天图片上传失败")
        return jsonify({'code': 500, 'message': f'上传失败: {e}'}), 500

    return jsonify({'code': 200, 'message': '上传成功', 'data': result})


@chat_bp.route('/image/presign', methods=['GET'])
def get_chat_image_presign_api():
    """
    生成聊天图片的预签名下载 URL（前端直接从 COS 下载，不经过 apiserver 代理）。
    校验：登录 + 路径归属当前用户。
    """
    from service import oss_service

    user = request.user_info
    config = current_app.config['APP_CONFIG']

    oss_path = request.args.get('path', '').strip()
    if not oss_path:
        return jsonify({'code': 400, 'message': '缺少 path 参数'}), 400

    # 防越权：路径必须包含当前用户的 user_id
    expected_prefix = f'chat/images/{user.user_id}/'
    if not oss_path.startswith(expected_prefix):
        return jsonify({'code': 403, 'message': '无权访问该图片'}), 403

    try:
        presigned_url = oss_service.generate_presigned_url(
            config=config.oss,
            oss_path=oss_path,
            expired=600,
        )
    except Exception as e:
        logger.exception("生成预签名 URL 失败: path=%s", oss_path)
        return jsonify({'code': 500, 'message': '生成预签名 URL 失败'}), 500

    return jsonify({
        'code': 200,
        'data': {
            'url': presigned_url,
        },
    })

