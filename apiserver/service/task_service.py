#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务业务逻辑服务层
"""

import json
from typing import Optional, Dict, List, Any

from dao.task_dao import (
    create_task as dao_create_task,
    get_tasks_by_user as dao_get_tasks_by_user,
    get_task_by_id as dao_get_task_by_id,
    update_task_status as dao_update_task_status,
    update_task_flow as dao_update_task_flow,
    update_task_desc as dao_update_task_desc,
    delete_task as dao_delete_task,
    update_task_client as dao_update_task_client
)
from dao.client_dao import get_client_by_id, get_client_by_id_no_user_check, check_client_usable_for_task
from dao.models import Task

from dao.task_dao import update_task_extra


def process_flow_for_frontend(flow: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理 flow 数据，根据 pre_node 关系生成 edges 供前端 React Flow 渲染
    
    Args:
        flow: 原始 flow 数据，包含 nodes 列表
        
    Returns:
        处理后的 flow 数据，包含 nodes、edges 和 error（如果有）
    """
    if not flow or not isinstance(flow, dict):
        return {'nodes': [], 'edges': []}
    
    nodes = flow.get('nodes', [])
    error = flow.get('error')  # 保留 error 字段
    
    if not nodes:
        result = {'nodes': [], 'edges': []}
        if error:
            result['error'] = error
        return result
    
    # 构建节点 id 映射
    node_map = {node.get('id'): node for node in nodes}
    
    # 根据 pre_node 生成 edges
    edges = []
    for node in nodes:
        pre_node_id = node.get('pre_node')
        if pre_node_id and pre_node_id in node_map:
            edge_id = f"e_{pre_node_id}_{node.get('id')}"
            edges.append({
                'id': edge_id,
                'source': pre_node_id,
                'target': node.get('id')
            })
    
    # 按照 pre_node 关系排序节点（拓扑排序）
    sorted_nodes = _topological_sort_nodes(nodes)
    
    result = {
        'nodes': sorted_nodes,
        'edges': edges
    }
    if error:
        result['error'] = error
    return result


def _topological_sort_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    对节点进行拓扑排序，pre_node 为空的节点排在前面
    
    Args:
        nodes: 节点列表
        
    Returns:
        排序后的节点列表
    """
    if not nodes:
        return []
    
    # 构建节点 id 到节点的映射
    node_map = {node.get('id'): node for node in nodes}
    
    # 找出所有 pre_node 为空的根节点
    root_nodes = [n for n in nodes if not n.get('pre_node')]
    
    # 构建子节点映射 (pre_node_id -> [child_nodes])
    children_map: Dict[str, List[Dict[str, Any]]] = {}
    for node in nodes:
        pre_node_id = node.get('pre_node')
        if pre_node_id:
            if pre_node_id not in children_map:
                children_map[pre_node_id] = []
            children_map[pre_node_id].append(node)
    
    # BFS 遍历生成排序后的节点列表
    sorted_nodes = []
    visited = set()
    queue = root_nodes.copy()
    
    while queue:
        node = queue.pop(0)
        node_id = node.get('id')
        
        if node_id in visited:
            continue
        
        visited.add(node_id)
        sorted_nodes.append(node)
        
        # 将子节点加入队列
        children = children_map.get(node_id, [])
        for child in children:
            if child.get('id') not in visited:
                queue.append(child)
    
    # 添加未访问的节点（处理孤立节点）
    for node in nodes:
        if node.get('id') not in visited:
            sorted_nodes.append(node)
    
    return sorted_nodes


def process_task_dict_with_flow(task_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理单个任务字典，将 flow 转换为前端需要的格式
    
    Args:
        task_dict: 任务字典
        
    Returns:
        处理后的任务字典
    """
    if 'flow' in task_dict:
        task_dict['flow'] = process_flow_for_frontend(task_dict['flow'])
    return task_dict


class TaskNotFoundException(Exception):
    """任务不存在异常"""
    pass


class TaskValidationException(Exception):
    """任务参数校验异常"""
    pass


def create_task(user_id: int, title: str, task_type: str, client_id: Optional[int] = None,
                desc: Optional[str] = None, status: Optional[str] = None) -> Dict:
    """
    创建任务业务逻辑

    Args:
        user_id: 用户ID
        title: 任务标题
        task_type: 任务类型
        client_id: 客户端ID（可选，不设置时任务可被任意client处理）
        desc: 任务描述（可选）

    Returns:
        任务信息字典

    Raises:
        TaskValidationException: 参数校验失败时抛出
        RuntimeError: 创建失败时抛出
    """
    title = (title or '').strip()
    task_type = (task_type or '').strip()

    # 设置默认值：任务类型默认为 'default'，客户端ID默认为 0（表示不绑定客户端）
    if not task_type:
        task_type = 'default'
    if not client_id:
        client_id = 0

    if not title:
        raise TaskValidationException('任务标题不能为空')

    if len(title) > 45:
        raise TaskValidationException('任务标题长度不能超过45个字符')

    if task_type and len(task_type) > 64:
        raise TaskValidationException('任务类型长度不能超过64个字符')

    # 如果指定了 client_id（非0），验证客户端有效性
    if client_id and client_id > 0:
        # 校验用户是否可以使用该客户端（用户自己创建的或当前公开的）
        if not check_client_usable_for_task(client_id, user_id):
            raise TaskValidationException('客户端不存在或无权使用')

        # 获取客户端信息用于验证任务类型
        client = get_client_by_id(client_id, user_id)
        if client:
            # 验证任务类型是否在客户端支持的类型列表中（仅当任务类型非默认值时）
            if task_type and task_type != 'default':
                client_types = client.types or []
                if task_type not in client_types:
                    raise TaskValidationException('所选任务类型不在客户端支持的类型列表中')

    # 校验 status 参数
    if status is not None:
        status = status.strip()
        if status and status not in Task.STATUS_TEXT:
            raise TaskValidationException(f'无效的状态，可选值：{list(Task.STATUS_TEXT.keys())}')

    task = dao_create_task(user_id, title, task_type, client_id, desc, status if status else None)
    return task


def get_tasks(user_id: int, status: Optional[str] = None, client_id: Optional[int] = None) -> List[Dict]:
    """
    获取用户任务列表
    
    Args:
        user_id: 用户ID
        status: 任务状态过滤（可选，如 pending/running/completed）
        client_id: 客户端ID过滤（可选，0 表示未分配客户端的任务）
        
    Returns:
        任务列表（flow 已处理为前端格式）
    """
    # 验证状态值
    if status and status not in Task.STATUS_TEXT:
        raise TaskValidationException(f'无效的状态，可选值：{list(Task.STATUS_TEXT.keys())}')
    
    tasks = dao_get_tasks_by_user(user_id, status, client_id)
    return tasks


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
    dao_update_task_status(task_id, user_id, status)
    
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

    # 补充客户端名称和类型列表
    if task.client_id:
        client = get_client_by_id_no_user_check(task.client_id)
        if client:
            task_dict['client_name'] = client.name
            task_dict['client_types'] = client.types or []
        else:
            task_dict['client_name'] = None
            task_dict['client_types'] = []
    else:
        task_dict['client_types'] = []

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


def update_flow(task_id: int, user_id: int, flow: Optional[Dict] = None, flow_status: Optional[str] = None) -> Dict:
    """
    更新任务流程（只更新非 None 的字段）
    
    Args:
        task_id: 任务ID
        user_id: 用户ID
        flow: 流程数据（可选）
        flow_status: 流程状态（可选）
        
    Returns:
        更新结果
        
    Raises:
        TaskNotFoundException: 任务不存在时抛出
        TaskValidationException: 流程数据无效时抛出
    """
    # 验证任务是否存在
    if not dao_get_task_by_id(task_id, user_id):
        raise TaskNotFoundException('任务不存在')
    
    # 验证流程数据基本结构
    if flow is not None:
        if not isinstance(flow, dict):
            raise TaskValidationException('流程数据必须是对象类型')
        
        # 如果有节点，验证节点结构
        if 'nodes' in flow:
            if not isinstance(flow['nodes'], list):
                raise TaskValidationException('流程节点必须是数组类型')
    
    # 验证 flow_status 长度
    if flow_status is not None and len(flow_status) > 32:
        raise TaskValidationException('流程状态长度不能超过32个字符')
    
    # 更新流程（只更新非 None 的字段）
    dao_update_task_flow(task_id, user_id, flow, flow_status)

    return {'success': True, 'message': '流程更新成功'}


def update_desc(task_id: int, user_id: int, desc: str, status: Optional[str] = None) -> Dict:
    """
    更新任务描述

    Args:
        task_id: 任务ID
        user_id: 用户ID
        desc: 新的任务描述
        status: 任务状态（可选）

    Returns:
        更新结果

    Raises:
        TaskNotFoundException: 任务不存在时抛出
        TaskValidationException: 状态值无效时抛出
    """
    # 检查任务是否存在
    if not dao_get_task_by_id(task_id, user_id):
        raise TaskNotFoundException('任务不存在')

    # 校验 status 参数
    if status is not None:
        status = status.strip()
        if status and status not in Task.STATUS_TEXT:
            raise TaskValidationException(f'无效的状态，可选值：{list(Task.STATUS_TEXT.keys())}')

    dao_update_task_desc(task_id, user_id, desc, status if status else None)
    return {'success': True, 'message': '任务更新成功'}


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


def update_client(task_id: int, user_id: int, client_id: int) -> Dict:
    """
    更新任务关联的客户端

    Args:
        task_id: 任务ID
        user_id: 用户ID
        client_id: 新的客户端ID

    Returns:
        更新结果

    Raises:
        TaskNotFoundException: 任务不存在时抛出
        TaskValidationException: 客户端无效时抛出
    """
    # 检查任务是否存在
    if not dao_get_task_by_id(task_id, user_id):
        raise TaskNotFoundException('任务不存在')

    # 验证客户端有效性
    if client_id and client_id > 0:
        if not check_client_usable_for_task(client_id, user_id):
            raise TaskValidationException('客户端不存在或无权使用')

    dao_update_task_client(task_id, user_id, client_id)
    return {'success': True, 'message': '客户端更新成功'}


def review_task(task_id: int, user_id: int, action: str, feedback: Optional[str] = None) -> Dict:
    """
    审核任务
    
    Args:
        task_id: 任务ID
        user_id: 用户ID
        action: 审核动作，'approve' 审核通过，'revise' 修订
        feedback: 用户反馈内容（修订时必填）
        
    Returns:
        更新结果
        
    Raises:
        TaskNotFoundException: 任务不存在时抛出
        TaskValidationException: 参数校验失败时抛出
    """
    # 验证 action 参数
    if action not in ['approve', 'revise']:
        raise TaskValidationException('无效的审核动作，可选值：approve, revise')
    
    # 获取任务
    task = dao_get_task_by_id(task_id, user_id)
    if not task:
        raise TaskNotFoundException('任务不存在')
    
    # 验证任务当前状态（只有 reviewing 或 done 状态可以审核）
    current_flow_status = task.flow_status or ''
    if current_flow_status not in ['reviewing', 'done']:
        raise TaskValidationException(f'当前流程状态 [{current_flow_status}] 不允许审核操作')
    
    if action == 'approve':
        # 审核通过：只有 reviewing 状态可以通过
        if current_flow_status != 'reviewing':
            raise TaskValidationException('只有待审核状态的任务可以通过审核')
        # 更新 flow_status 为 reviewed
        dao_update_task_flow(task_id, user_id, flow=None, flow_status='reviewed')
        return {'success': True, 'message': '审核通过', 'flow_status': 'reviewed'}
    
    else:  # action == 'revise'
        # 修订：需要提供反馈内容
        if not feedback or not feedback.strip():
            raise TaskValidationException('修订时必须提供反馈内容')
        
        # 在 flow['nodes'] 中添加 user_feedback 节点
        flow = task.flow or {}
        nodes = flow.get('nodes', [])
        
        # 添加 user_feedback 节点
        feedback_node = {
            'type': 'user_feedback',
            'content': feedback.strip()
        }
        nodes.append(feedback_node)
        flow['nodes'] = nodes
        
        # 更新 flow 和 flow_status 为 revising
        dao_update_task_flow(task_id, user_id, flow=flow, flow_status='revising')
        return {'success': True, 'message': '已提交修订反馈', 'flow_status': 'revising'}


def sync_execute(
    task_id: int,
    user_id: int,
    develop_doc: str,
    merge_request: List[Dict[str, Any]]
) -> Dict:
    """
    同步执行差异信息到 ai_task_tasks.extra
    """
    extra = {
        "develop_doc": develop_doc or "",
        "merge_request": merge_request or []
    }
    ok = update_task_extra(task_id=task_id, user_id=user_id, extra=extra)
    return {"success": ok, "message": "同步成功" if ok else "同步失败"}