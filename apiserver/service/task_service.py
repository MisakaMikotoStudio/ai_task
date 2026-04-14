#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务业务逻辑服务层
"""

import json
import math
from typing import Optional, Dict, List, Any

from dao.task_dao import (
    create_task as dao_create_task,
    get_tasks_by_user as dao_get_tasks_by_user,
    get_task_by_id as dao_get_task_by_id,
    update_task as dao_update_task,
    delete_task as dao_delete_task
)
from dao.client_dao import get_client_by_id, check_client_usable_for_user
from dao.models import Task


class TaskNotFoundException(Exception):
    """任务不存在异常"""
    pass


class TaskValidationException(Exception):
    """任务参数校验异常"""
    pass


def create_task(user_id: int, title: str, client_id: int,
                status: str = Task.STATUS_PENDING) -> Task:
    """
    创建任务业务逻辑

    Args:
        user_id: 用户ID
        title: 任务标题
        client_id: 客户端ID（必填，且必须为正整数）

    Returns:
        任务信息字典

    Raises:
        TaskValidationException: 参数校验失败时抛出
        RuntimeError: 创建失败时抛出
    """
    title = (title or '').strip()

    if not title:
        raise TaskValidationException('任务标题不能为空')

    if len(title) > 45:
        raise TaskValidationException('任务标题长度不能超过45个字符')

    if client_id <= 0:
        raise TaskValidationException('客户端ID必须为正整数')

    # 校验用户是否可以使用该客户端（仅用户自己创建的）
    if not check_client_usable_for_user(client_id, user_id):
        raise TaskValidationException('客户端不存在或无权使用')

    # 校验 status 参数
    status = status.strip()
    if status not in Task.STATUS_TEXT:
        raise TaskValidationException(f'无效的状态，可选值：{list(Task.STATUS_TEXT.keys())}')

    task = dao_create_task(user_id, title, client_id, status)
    return task


def get_tasks(
    user_id: int,
    statuses: Optional[List[str]] = None,
    page: int = 1,
    page_num: int = 20
) -> Dict[str, Any]:
    """
    获取用户任务列表
    
    Args:
        user_id: 用户ID
        statuses: 任务状态过滤列表（可选，如 pending/running/completed）
        page: 页码，从 1 开始
        page_num: 每页条数

    Returns:
        分页任务列表（flow 已处理为前端格式）
    """
    if page < 1:
        raise TaskValidationException('page 必须大于等于 1')

    if page_num < 1 or page_num > 100:
        raise TaskValidationException('pageNum 必须在 1 到 100 之间')

    normalized_statuses = None
    if statuses is not None:
        normalized_statuses = []
        for status in statuses:
            status = (status or '').strip()
            if not status:
                continue
            if status not in Task.STATUS_TEXT:
                raise TaskValidationException(f'无效的状态，可选值：{list(Task.STATUS_TEXT.keys())}')
            if status not in normalized_statuses:
                normalized_statuses.append(status)

    task_page = dao_get_tasks_by_user(user_id=user_id, statuses=normalized_statuses, page=page, page_num=page_num)
    total = task_page['total']

    return {
        'items': task_page['items'],
        'total': total,
        'page': page,
        'page_num': page_num,
        'total_pages': math.ceil(total / page_num) if total > 0 else 0
    }


def update_status(task_id: int, user_id: int, status: str) -> Dict:
    """
    更新任务状态
    
    Args:
        task_id: 任务ID
        user_id: 用户ID
        status: 新状态
        
    Returns:
        更新后的状态信息
        
    Raises:
        TaskValidationException: 状态值无效时抛出
        TaskNotFoundException: 任务不存在时抛出
    """
    status = (status or '').strip()
    
    if status not in Task.STATUS_TEXT:
        raise TaskValidationException(f'无效的状态，可选值：{list(Task.STATUS_TEXT.keys())}')
    
    # 检查任务是否存在
    if not dao_get_task_by_id(task_id, user_id):
        raise TaskNotFoundException('任务不存在')
    
    # 更新状态
    dao_update_task(task_id, user_id, status=status)
    
    return {
        'status': status,
        'status_text': Task.STATUS_TEXT[status]
    }


def get_task(task_id: int, user_id: int) -> Dict:
    """
    获取任务详情
    
    Args:
        task_id: 任务ID
        user_id: 用户ID
        
    Returns:
        任务信息字典（flow 已处理为前端格式）
        
    Raises:
        TaskNotFoundException: 任务不存在时抛出
    """
    task = dao_get_task_by_id(task_id, user_id)
    if not task:
        raise TaskNotFoundException('任务不存在')

    task_dict = task.to_dict()

    # 补充客户端名称
    if task.client_id:
        client = get_client_by_id(client_id=task.client_id, user_id=user_id)
        if client:
            task_dict['client_name'] = client.name
        else:
            task_dict['client_name'] = str(task.client_id)

    # 解析 extra JSON，提取 develop_doc 和 merge_request
    extra_raw = task.extra or ''
    if extra_raw:
        try:
            extra_data = json.loads(extra_raw)
            task_dict['develop_doc'] = extra_data.get('develop_doc', '')
            task_dict['merge_request'] = extra_data.get('merge_request', [])
        except (json.JSONDecodeError, TypeError):
            task_dict['develop_doc'] = ''
            task_dict['merge_request'] = []
    else:
        task_dict['develop_doc'] = ''
        task_dict['merge_request'] = []

    return task_dict


def delete_task(task_id: int, user_id: int) -> Dict:
    """
    删除任务

    Args:
        task_id: 任务ID
        user_id: 用户ID

    Returns:
        删除结果

    Raises:
        TaskNotFoundException: 任务不存在时抛出
    """
    # 检查任务是否存在
    if not dao_get_task_by_id(task_id, user_id):
        raise TaskNotFoundException('任务不存在')

    dao_delete_task(task_id, user_id)
    return {'success': True, 'message': '任务删除成功'}


def sync_execute(
    task_id: int,
    user_id: int,
    develop_doc: str,
    merge_request: List[Dict[str, Any]]
) -> Dict:
    """
    同步执行差异信息到 ai_task_tasks.extra
    """
    # 检查任务是否存在
    if not dao_get_task_by_id(task_id, user_id):
        raise TaskNotFoundException('任务不存在')

    extra = {
        "develop_doc": develop_doc,
        "merge_request": merge_request
    }
    ok = dao_update_task(task_id=task_id, user_id=user_id, extra=extra)
    return {"success": ok, "message": "同步成功" if ok else "同步失败"}