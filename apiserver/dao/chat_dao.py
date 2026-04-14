#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Chat数据访问对象
"""

from typing import Optional, List, Dict
from datetime import datetime, timezone

from .connection import get_db_session
from .models import Chat, ChatMessage, Task, Client


def create_chat(user_id: int, task_id: int, title: str, sessionid: Optional[str] = None,
                client_id: Optional[int] = None) -> Chat:
    """创建Chat"""
    with get_db_session() as session:
        chat = Chat(
            user_id=user_id,
            task_id=task_id,
            client_id=client_id,
            title=title,
            status=Chat.STATUS_COMPLETED,
            sessionid=sessionid,
            deleted_at=None
        )
        session.add(chat)
        session.flush()
        return chat


def get_chats_by_task(user_id: int, task_id: int) -> List[Dict]:
    """获取任务下未删除的Chat列表"""
    with get_db_session() as session:
        chats = session.query(Chat).filter(
            Chat.user_id == user_id,
            Chat.task_id == task_id,
            Chat.deleted_at.is_(None)
        ).order_by(Chat.updated_at.desc()).all()
        return [c.to_dict() for c in chats]


def get_chat_by_id(user_id: int, chat_id: int, task_id: int) -> Optional[Chat]:
    """获取指定未删除的Chat"""
    with get_db_session() as session:
        return session.query(Chat).filter(
            Chat.id == chat_id,
            Chat.task_id == task_id,
            Chat.user_id == user_id,
            Chat.deleted_at.is_(None)
        ).first()


def update_chat_status(user_id: int, chat_id: int, task_id: int, status: str) -> bool:
    """更新Chat状态"""
    with get_db_session() as session:
        affected = session.query(Chat).filter(
            Chat.id == chat_id,
            Chat.task_id == task_id,
            Chat.user_id == user_id,
            Chat.deleted_at.is_(None)
        ).update({Chat.status: status})
        return affected > 0


def update_chat_sessionid(user_id: int, task_id: int, chat_id: int, sessionid: Optional[str]) -> bool:
    """更新 Chat 的 sessionid"""
    with get_db_session() as session:
        affected = session.query(Chat).filter(
            Chat.id == chat_id,
            Chat.task_id == task_id,
            Chat.user_id == user_id,
            Chat.deleted_at.is_(None)
        ).update({Chat.sessionid: sessionid})
        return affected > 0


def soft_delete_chat(user_id: int, chat_id: int, task_id: int) -> bool:
    """软删除Chat（设置 deleted_at）"""
    with get_db_session() as session:
        affected = session.query(Chat).filter(
            Chat.id == chat_id,
            Chat.task_id == task_id,
            Chat.user_id == user_id,
            Chat.deleted_at.is_(None)
        ).update({Chat.deleted_at: datetime.now(timezone.utc)})
        return affected > 0


def create_chat_message(user_id: int, task_id: int, chat_id: int, input_text: str,
                        extra: Optional[dict] = None) -> ChatMessage:
    """创建Chat消息"""
    with get_db_session() as session:
        msg = ChatMessage(
            user_id=user_id,
            task_id=task_id,
            chat_id=chat_id,
            status=ChatMessage.STATUS_PENDING,
            input=input_text,
            output=None,
            extra=extra or {},
            deleted_at=None
        )
        session.add(msg)
        session.flush()
        return msg


def get_messages_by_chat(user_id: int, chat_id: int, task_id: int) -> List[Dict]:
    """获取Chat下未删除的消息列表（按时间升序）"""
    with get_db_session() as session:
        msgs = session.query(ChatMessage).filter(
            ChatMessage.user_id == user_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.task_id == task_id,
            ChatMessage.deleted_at.is_(None)
        ).order_by(ChatMessage.created_at.asc()).all()
        return [m.to_dict() for m in msgs]

def get_message_by_id(user_id: int, message_id: int, chat_id: int, task_id: int) -> Optional[ChatMessage]:
    """获取指定ID的未删除的消息"""
    with get_db_session() as session:
        return session.query(ChatMessage).filter(
            ChatMessage.id == message_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.task_id == task_id,
            ChatMessage.user_id == user_id,
            ChatMessage.deleted_at.is_(None)
        ).first()

def get_running_message(user_id: int, chat_id: int, task_id: int) -> Optional[ChatMessage]:
    """获取Chat中正在执行的消息（未删除）"""
    with get_db_session() as session:
        return session.query(ChatMessage).filter(
            ChatMessage.chat_id == chat_id,
            ChatMessage.task_id == task_id,
            ChatMessage.user_id == user_id,
            ChatMessage.deleted_at.is_(None),
            ChatMessage.status.in_([ChatMessage.STATUS_PENDING, ChatMessage.STATUS_RUNNING])
        ).first()

def update_message(
    user_id: int,
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
            ChatMessage.user_id == user_id,
            ChatMessage.deleted_at.is_(None)
        ).update(update_data)
        return affected > 0

def soft_delete_message(user_id: int, message_id: int, chat_id: int, task_id: int) -> Optional[str]:
    """
    软删除消息（设置 deleted_at），返回被删除消息的input内容。
    用于用户点击「终止」时，将该条消息撤回并把input回填到输入框。
    """
    with get_db_session() as session:
        msg = session.query(ChatMessage).filter(
            ChatMessage.id == message_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.task_id == task_id,
            ChatMessage.user_id == user_id,
            ChatMessage.deleted_at.is_(None)
        ).first()
        if not msg:
            return None
        input_text = msg.input or ''
        msg.deleted_at = datetime.now(timezone.utc)
        session.flush()
        return input_text

def get_standalone_chats(
    user_id: int,
    statuses: Optional[List[str]] = None,
    page: int = 1,
    page_num: int = 20
) -> Dict:
    """获取 task_id=0 的独立 Chat 列表（含 client 名称），支持分页和状态筛选"""
    with get_db_session() as session:
        query = session.query(
            Chat,
            Client.name.label('client_name'),
        ).outerjoin(
            Client, Chat.client_id == Client.id
        ).filter(
            Chat.user_id == user_id,
            Chat.task_id == 0,
            Chat.deleted_at.is_(None),
        )

        if statuses:
            query = query.filter(Chat.status.in_(statuses))

        total = query.count()

        rows = query.order_by(Chat.updated_at.desc()).offset((page - 1) * page_num).limit(page_num).all()

        result = []
        for chat, client_name in rows:
            d = chat.to_dict()
            d['client_name'] = client_name or ''
            result.append(d)
        return {
            'total': total,
            'page': page,
            'page_num': page_num,
            'items': result,
        }


def get_running_chat_messages_by_client(user_id: int, client_id: int) -> List[Dict]:
    """获取指定客户端下需要处理的对话消息（按 task/chat 聚合）
    包含两类：
    1. task_id > 0：通过 Task.client_id 关联
    2. task_id = 0：通过 Chat.client_id 关联（独立 Chat）
    """
    from sqlalchemy import literal_column
    with get_db_session() as session:
        # 查询1：归属 task 的 chat（原有逻辑）
        task_rows = session.query(
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
            Task.deleted_at.is_(None),
            Chat.user_id == user_id,
            Chat.deleted_at.is_(None),
            ChatMessage.user_id == user_id,
            ChatMessage.deleted_at.is_(None),
            ChatMessage.status.in_([ChatMessage.STATUS_PENDING, ChatMessage.STATUS_RUNNING])
        ).order_by(
            Task.id.asc(),
            Chat.id.asc(),
            ChatMessage.created_at.asc(),
            ChatMessage.id.asc()
        ).all()

        # 查询2：独立 chat（task_id=0，通过 chat.client_id 关联）
        standalone_rows = session.query(
            literal_column('0').label('task_id'),
            literal_column("''").label('task_title'),
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
            ChatMessage, ChatMessage.chat_id == Chat.id
        ).filter(
            Chat.user_id == user_id,
            Chat.task_id == 0,
            Chat.client_id == client_id,
            Chat.deleted_at.is_(None),
            ChatMessage.user_id == user_id,
            ChatMessage.deleted_at.is_(None),
            ChatMessage.status.in_([ChatMessage.STATUS_PENDING, ChatMessage.STATUS_RUNNING])
        ).order_by(
            Chat.id.asc(),
            ChatMessage.created_at.asc(),
            ChatMessage.id.asc()
        ).all()

        all_rows = list(task_rows) + list(standalone_rows)

        grouped: Dict[tuple, Dict] = {}
        for row in all_rows:
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
