#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OKR 业务逻辑服务层
"""

from typing import Optional, Dict, List, Any
from datetime import date

from dao.okr_dao import (
    create_objective as dao_create_objective,
    get_objectives_by_user as dao_get_objectives,
    get_objectives_with_krs as dao_get_objectives_with_krs,
    get_objective_by_id as dao_get_objective,
    update_objective as dao_update_objective,
    delete_objective as dao_delete_objective,
    create_key_result as dao_create_kr,
    get_key_results_by_objective as dao_get_krs,
    get_key_result_by_id as dao_get_kr,
    update_key_result as dao_update_kr,
    delete_key_result as dao_delete_kr,
    reorder_objectives as dao_reorder_objectives,
    reorder_key_results as dao_reorder_key_results
)
from dao.models import Objective, KeyResult


class OKRNotFoundException(Exception):
    """OKR不存在异常"""
    pass


class OKRValidationException(Exception):
    """OKR参数校验异常"""
    pass


# ========== Objective Service ==========

def create_objective(user_id: int, title: str, description: Optional[str] = None,
                     cycle_type: str = 'week', cycle_start: Optional[str] = None,
                     cycle_end: Optional[str] = None) -> Dict:
    """创建目标"""
    title = (title or '').strip()
    # 允许空标题，支持前端直接编辑模式
    if len(title) > 255:
        raise OKRValidationException('目标标题长度不能超过255个字符')

    if cycle_type not in Objective.CYCLE_TYPES:
        raise OKRValidationException(f'无效的周期类型，可选值：{Objective.CYCLE_TYPES}')

    start_date = None
    end_date = None
    if cycle_start:
        try:
            start_date = date.fromisoformat(cycle_start)
        except ValueError:
            raise OKRValidationException('周期开始日期格式无效，应为 YYYY-MM-DD')
    if cycle_end:
        try:
            end_date = date.fromisoformat(cycle_end)
        except ValueError:
            raise OKRValidationException('周期结束日期格式无效，应为 YYYY-MM-DD')

    obj = dao_create_objective(user_id, title, description, cycle_type, start_date, end_date)
    return obj.to_dict()


def get_objectives(user_id: int, cycle_type: Optional[str] = None,
                   status: Optional[str] = None,
                   cycle_start: Optional[str] = None,
                   cycle_end: Optional[str] = None) -> List[Dict]:
    """获取目标列表（含KRs详情，用于瀑布流渲染）

    优化：如果传入周期范围，使用一次性查询避免N+1问题
    """
    if cycle_type and cycle_type not in Objective.CYCLE_TYPES:
        raise OKRValidationException(f'无效的周期类型，可选值：{Objective.CYCLE_TYPES}')
    if status and status not in Objective.STATUS_TEXT:
        raise OKRValidationException(f'无效的状态，可选值：{list(Objective.STATUS_TEXT.keys())}')

    # 转换日期
    start_date = None
    end_date = None
    if cycle_start:
        try:
            start_date = date.fromisoformat(cycle_start)
        except ValueError:
            raise OKRValidationException('cycle_start 格式无效，应为 YYYY-MM-DD')
    if cycle_end:
        try:
            end_date = date.fromisoformat(cycle_end)
        except ValueError:
            raise OKRValidationException('cycle_end 格式无效，应为 YYYY-MM-DD')

    # 如果传入周期范围，使用优化后的一次性查询
    if start_date or end_date:
        return dao_get_objectives_with_krs(user_id, cycle_type, start_date, end_date)

    # 兼容旧逻辑（无周期范围时）
    objectives = dao_get_objectives(user_id, cycle_type, status)
    result = []
    for obj in objectives:
        obj_dict = obj.to_dict()
        krs = dao_get_krs(obj.id, user_id)
        obj_dict['key_results_count'] = len(krs)
        obj_dict['key_results'] = [kr.to_dict() for kr in krs]
        result.append(obj_dict)
    return result


def get_objective(objective_id: int, user_id: int) -> Dict:
    """获取目标详情（含KRs和关联任务）"""
    obj = dao_get_objective(objective_id, user_id)
    if not obj:
        raise OKRNotFoundException('目标不存在')

    obj_dict = obj.to_dict()
    krs = dao_get_krs(objective_id, user_id)
    kr_list = []
    for kr in krs:
        kr_dict = kr.to_dict()
        # Current schema doesn't define a KeyResult -> Task relationship.
        # Keep the response shape stable for the frontend.
        kr_dict['tasks'] = []
        kr_list.append(kr_dict)
    obj_dict['key_results'] = kr_list
    return obj_dict


def update_objective(objective_id: int, user_id: int, **kwargs) -> Dict:
    """更新目标"""
    obj = dao_get_objective(objective_id, user_id)
    if not obj:
        raise OKRNotFoundException('目标不存在')

    # 验证字段
    if 'title' in kwargs:
        title = (kwargs['title'] or '').strip()
        if not title:
            raise OKRValidationException('目标标题不能为空')
        if len(title) > 255:
            raise OKRValidationException('目标标题长度不能超过255个字符')
        kwargs['title'] = title

    if 'status' in kwargs and kwargs['status'] not in Objective.STATUS_TEXT:
        raise OKRValidationException(f'无效的状态，可选值：{list(Objective.STATUS_TEXT.keys())}')

    if 'cycle_type' in kwargs and kwargs['cycle_type'] not in Objective.CYCLE_TYPES:
        raise OKRValidationException(f'无效的周期类型，可选值：{Objective.CYCLE_TYPES}')

    # 转换日期字段
    for date_field in ['cycle_start', 'cycle_end']:
        if date_field in kwargs and kwargs[date_field]:
            try:
                kwargs[date_field] = date.fromisoformat(kwargs[date_field])
            except ValueError:
                raise OKRValidationException(f'{date_field}格式无效，应为 YYYY-MM-DD')

    dao_update_objective(objective_id, user_id, **kwargs)
    return {'success': True, 'message': '目标更新成功'}


def delete_objective(objective_id: int, user_id: int) -> Dict:
    """删除目标"""
    obj = dao_get_objective(objective_id, user_id)
    if not obj:
        raise OKRNotFoundException('目标不存在')

    dao_delete_objective(objective_id, user_id)
    return {'success': True, 'message': '目标删除成功'}


# ========== KeyResult Service ==========

def create_key_result(objective_id: int, user_id: int, title: str,
                      description: Optional[str] = None) -> Dict:
    """创建关键结果"""
    # 验证目标存在
    obj = dao_get_objective(objective_id, user_id)
    if not obj:
        raise OKRNotFoundException('目标不存在')

    title = (title or '').strip()
    # 允许空标题，支持前端直接编辑模式
    if len(title) > 255:
        raise OKRValidationException('KR标题长度不能超过255个字符')

    kr = dao_create_kr(objective_id, user_id, title, description)
    return kr.to_dict()


def update_key_result(kr_id: int, user_id: int, **kwargs) -> Dict:
    """更新KR"""
    kr = dao_get_kr(kr_id, user_id)
    if not kr:
        raise OKRNotFoundException('关键结果不存在')

    # 验证KR所属目标属于当前用户
    obj = dao_get_objective(kr.objective_id, user_id)
    if not obj:
        raise OKRNotFoundException('关键结果不存在')

    # 验证字段
    if 'title' in kwargs:
        title = (kwargs['title'] or '').strip()
        if not title:
            raise OKRValidationException('KR标题不能为空')
        if len(title) > 255:
            raise OKRValidationException('KR标题长度不能超过255个字符')
        kwargs['title'] = title

    dao_update_kr(kr_id, user_id, **kwargs)
    return {'success': True, 'message': 'KR更新成功'}


def delete_key_result(kr_id: int, user_id: int) -> Dict:
    """删除KR"""
    kr = dao_get_kr(kr_id, user_id)
    if not kr:
        raise OKRNotFoundException('关键结果不存在')

    # 验证KR所属目标属于当前用户
    obj = dao_get_objective(kr.objective_id, user_id)
    if not obj:
        raise OKRNotFoundException('关键结果不存在')

    dao_delete_kr(kr_id, user_id)
    return {'success': True, 'message': 'KR删除成功'}


# ========== Reorder Service ==========

def reorder_objectives(user_id: int, objective_ids: List[int]) -> Dict:
    """重新排序目标"""
    if not objective_ids:
        raise OKRValidationException('目标ID列表不能为空')

    dao_reorder_objectives(user_id, objective_ids)
    return {'success': True, 'message': '目标排序更新成功'}


def reorder_key_results(objective_id: int, user_id: int, kr_ids: List[int]) -> Dict:
    """重新排序关键结果"""
    # 验证目标存在
    obj = dao_get_objective(objective_id, user_id)
    if not obj:
        raise OKRNotFoundException('目标不存在')

    if not kr_ids:
        raise OKRValidationException('KR ID列表不能为空')

    dao_reorder_key_results(objective_id, user_id, kr_ids)
    return {'success': True, 'message': 'KR排序更新成功'}
