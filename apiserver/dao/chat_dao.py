#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Chat数据访问对象
"""

from typing import Optional, List, Dict

from .connection import get_db_session
from .models import Chat, ChatMessage, Task


def create_chat(task_id: int, title: str, sessionid: Optional[str] = None) -> Chat:
    """创建Chat"""
    with get_db_session() as session:
        chat = Chat(
            task_id=task_id,
            title=title,
            status=Chat.STATUS_COMPLETED,
            sessionid=sessionid,
            deleted=0
        )
        session.add(chat)
        session.flush()
        return chat


def get_chats_by_task(task_id: int) -> List[Dict]:
    """获取任务下未删除的Chat列表"""
    with get_db_session() as session:
        chats = session.query(Chat).filter(
            Chat.task_id == task_id,
            Chat.deleted == 0
        ).order_by(Chat.updated_at.desc()).all()
        return [c.to_dict() for c in chats]


def get_chat_by_id(chat_id: int, task_id: int) -> Optional[Chat]:
    """获取指定未删除的Chat"""
    with get_db_session() as session:
        return session.query(Chat).filter(
            Chat.id == chat_id,
            Chat.task_id == task_id,
            Chat.deleted == 0
        ).first()


def update_chat_status(chat_id: int, task_id: int, status: str) -> bool:
    """更新Chat状态"""
    with get_db_session() as session:
        affected = session.query(Chat).filter(
            Chat.id == chat_id,
            Chat.task_id == task_id,
            Chat.deleted == 0
        ).update({Chat.status: status})
        return affected > 0


def update_chat_sessionid(task_id: int, chat_id: int, sessionid: Optional[str]) -> bool:
    """更新 Chat 的 sessionid"""
    with get_db_session() as session:
        affected = session.query(Chat).filter(
            Chat.id == chat_id,
            Chat.task_id == task_id,
            Chat.deleted == 0
        ).update({Chat.sessionid: sessionid})
        return affected > 0


def soft_delete_chat(chat_id: int, task_id: int) -> bool:
    """软删除Chat（deleted=1）"""
    with get_db_session() as session:
        affected = session.query(Chat).filter(
            Chat.id == chat_id,
            Chat.task_id == task_id
        ).update({Chat.deleted: 1})
        return affected > 0


def delete_chat(chat_id: int, task_id: int) -> bool:
    """软删除Chat（对外接口，行为同soft_delete_chat）"""
    return soft_delete_chat(chat_id, task_id)


def create_chat_message(task_id: int, chat_id: int, input_text: str,
                        extra: Optional[dict] = None) -> ChatMessage:
    """创建Chat消息"""
    with get_db_session() as session:
        msg = ChatMessage(
            task_id=task_id,
            chat_id=chat_id,
            status=ChatMessage.STATUS_PENDING,
            input=input_text,
            output=None,
            extra=extra or {},
            deleted=0
        )
        session.add(msg)
        session.flush()
        return msg


def get_messages_by_chat(chat_id: int, task_id: int) -> List[Dict]:
    """获取Chat下未删除的消息列表（按时间升序）"""
    with get_db_session() as session:
        msgs = session.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.task_id == task_id,
            ChatMessage.deleted == 0
        ).order_by(ChatMessage.created_at.asc()).all()
        return [m.to_dict() for m in msgs]

def get_message_by_id(message_id: int, chat_id: int) -> Optional[ChatMessage]:
    """获取指定ID的未删除的消息"""
    with get_db_session() as session:
        return session.query(ChatMessage).filter(
            ChatMessage.id == message_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.deleted == 0
        ).first()

def get_running_message(chat_id: int, task_id: int) -> Optional[ChatMessage]:
    """获取Chat中正在执行的消息（未删除）"""
    with get_db_session() as session:
        return session.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.task_id == task_id,
            ChatMessage.deleted == 0,
            ChatMessage.status.in_([ChatMessage.STATUS_PENDING, ChatMessage.STATUS_RUNNING])
        ).first()

def update_message(
    task_id: int,
    chat_id: int,
    message_id: int,
    output: Optional[str] = None,
    extra: Optional[dict] = None,
    status: Optional[str] = None
) -> bool:
    """
    更新 ChatMessage 的 output/extra/status 字段。
    """
    with get_db_session() as session:
        update_data = {}
        if extra is not None:
            update_data[ChatMessage.extra] = extra
        if status is not None:
            update_data[ChatMessage.status] = status
        if output is not None:
            update_data[ChatMessage.output] = output
        if not update_data:
            return False
        affected = session.query(ChatMessage).filter(
            ChatMessage.id == message_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.task_id == task_id,
            ChatMessage.deleted == 0
        ).update(update_data)
        return affected > 0

def soft_delete_message(message_id: int, chat_id: int, task_id: int) -> Optional[str]:
    """
    软删除消息（deleted=1），返回被删除消息的input内容。
    用于用户点击「终止」时，将该条消息撤回并把input回填到输入框。
    """
    with get_db_session() as session:
        msg = session.query(ChatMessage).filter(
            ChatMessage.id == message_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.task_id == task_id,
            ChatMessage.deleted == 0
        ).first()
        if not msg:
            return None
        input_text = msg.input or ''
        msg.deleted = 1
        session.flush()
        return input_text


def get_running_chats_by_client(user_id: int, client_id: int) -> List[Dict]:
    """获取指定客户端下仍有运行中消息的 Chat 列表"""
    with get_db_session() as session:
        rows = session.query(
            Chat.id.label('chat_id'),
            Chat.task_id.label('task_id'),
            Chat.title.label('chat_title'),
            Chat.sessionid.label('sessionid'),
            Chat.status.label('chat_status'),
            ChatMessage.id.label('message_id'),
            ChatMessage.status.label('message_status'),
            ChatMessage.input.label('message_input'),
            ChatMessage.created_at.label('message_created_at')
        ).join(
            Task, Task.id == Chat.task_id
        ).join(
            ChatMessage, ChatMessage.chat_id == Chat.id
        ).filter(
            Task.user_id == user_id,
            Task.client_id == client_id,
            Task.status == Task.STATUS_RUNNING,
            Task.deleted == 0,
            Chat.deleted == 0,
            ChatMessage.deleted == 0,
            ChatMessage.status.in_([ChatMessage.STATUS_PENDING, ChatMessage.STATUS_RUNNING])
        ).order_by(
            ChatMessage.created_at.asc()
        ).all()

        result = []
        for row in rows:
            result.append({
                'task_id': row.task_id,
                'chat_id': row.chat_id,
                'chat_title': row.chat_title or '',
                'chat_status': row.chat_status or '',
                'sessionid': row.sessionid or '',
                'message_id': row.message_id,
                'message_status': row.message_status or '',
                'message_input': row.message_input or '',
                'message_created_at': str(row.message_created_at) if row.message_created_at else None
            })
        return result


def get_running_chat_messages_by_client(user_id: int, client_id: int) -> List[Dict]:
    """获取指定客户端下需要处理的对话消息（按 task/chat 聚合）"""
    with get_db_session() as session:
        rows = session.query(
            Task.id.label('task_id'),
            Task.title.label('task_title'),
            Chat.id.label('chat_id'),
            Chat.title.label('chat_title'),
            Chat.sessionid.label('chat_sessionid'),
            ChatMessage.id.label('message_id'),
            ChatMessage.status.label('message_status'),
            ChatMessage.input.label('message_input'),
            ChatMessage.output.label('message_output'),
            ChatMessage.extra.label('message_extra'),
            ChatMessage.created_at.label('message_created_at')
        ).join(
            Chat, Chat.task_id == Task.id
        ).join(
            ChatMessage, ChatMessage.chat_id == Chat.id
        ).filter(
            Task.user_id == user_id,
            Task.client_id == client_id,
            Task.status == Task.STATUS_RUNNING,
            Task.deleted == 0,
            Chat.deleted == 0,
            ChatMessage.deleted == 0,
            ChatMessage.status.in_([ChatMessage.STATUS_PENDING, ChatMessage.STATUS_RUNNING])
        ).order_by(
            Task.id.asc(),
            Chat.id.asc(),
            ChatMessage.created_at.asc(),
            ChatMessage.id.asc()
        ).all()

        grouped: Dict[tuple, Dict] = {}
        for row in rows:
            group_key = (row.task_id, row.chat_id)
            if group_key not in grouped:
                grouped[group_key] = {
                    'task_id': row.task_id,
                    'task_title': row.task_title or '',
                    'chat_id': row.chat_id,
                    'chat_title': row.chat_title or '',
                    'session_id': row.chat_sessionid or '',
                    'chat_messages': []
                }
            grouped[group_key]['chat_messages'].append({
                'id': row.message_id,
                'status': row.message_status or '',
                'input': row.message_input or '',
                'output': row.message_output or '',
                'extra': row.message_extra or {}
            })

        return list(grouped.values())
