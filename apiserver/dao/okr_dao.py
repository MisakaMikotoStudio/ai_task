#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OKR 数据访问对象
"""

from typing import Optional, List
from datetime import date, datetime, timezone

from .connection import get_db_session
from .models import Objective, KeyResult


# ========== Objective CRUD ==========

def create_objective(user_id: int, title: str, description: Optional[str] = None,
                     cycle_type: str = 'quarter', cycle_start: Optional[date] = None,
                     cycle_end: Optional[date] = None) -> Objective:
    """创建目标"""
    with get_db_session() as session:
        obj = Objective(
            user_id=user_id,
            title=title,
            description=description,
            status=Objective.STATUS_DRAFT,
            cycle_type=cycle_type,
            cycle_start=cycle_start,
            cycle_end=cycle_end
        )
        session.add(obj)
        session.flush()
        return obj


def get_objectives_by_user(user_id: int, cycle_type: Optional[str] = None,
                           status: Optional[str] = None,
                           cycle_start: Optional[date] = None,
                           cycle_end: Optional[date] = None) -> List[Objective]:
    """获取用户的目标列表，支持按周期范围过滤"""
    with get_db_session() as session:
        query = session.query(Objective).filter(
            Objective.user_id == user_id,
            Objective.deleted_at.is_(None)
        )
        if cycle_type:
            query = query.filter(Objective.cycle_type == cycle_type)
        if status:
            query = query.filter(Objective.status == status)
        # 按周期范围过滤
        if cycle_start:
            query = query.filter(Objective.cycle_start >= cycle_start)
        if cycle_end:
            query = query.filter(Objective.cycle_start <= cycle_end)
        return query.order_by(Objective.sort_order.asc(), Objective.created_at.desc()).all()


def get_objectives_with_krs(user_id: int, cycle_type: Optional[str] = None,
                            cycle_start: Optional[date] = None,
                            cycle_end: Optional[date] = None) -> List[dict]:
    """一次性获取用户指定周期的所有OKR数据（含KRs），避免N+1查询"""
    with get_db_session() as session:
        # 先查询符合条件的目标
        query = session.query(Objective).filter(
            Objective.user_id == user_id,
            Objective.deleted_at.is_(None)
        )
        if cycle_type:
            query = query.filter(Objective.cycle_type == cycle_type)
        if cycle_start:
            query = query.filter(Objective.cycle_start >= cycle_start)
        if cycle_end:
            query = query.filter(Objective.cycle_start <= cycle_end)

        objectives = query.order_by(Objective.sort_order.asc(), Objective.created_at.desc()).all()
        if not objectives:
            return []

        # 收集所有目标ID，一次性查询所有KRs
        obj_ids = [obj.id for obj in objectives]
        all_krs = session.query(KeyResult).filter(
            KeyResult.user_id == user_id,
            KeyResult.objective_id.in_(obj_ids),
            KeyResult.deleted_at.is_(None)
        ).order_by(KeyResult.sort_order.asc(), KeyResult.created_at.asc()).all()

        # 按objective_id分组
        krs_by_obj = {}
        for kr in all_krs:
            if kr.objective_id not in krs_by_obj:
                krs_by_obj[kr.objective_id] = []
            krs_by_obj[kr.objective_id].append(kr.to_dict())

        # 组装结果
        result = []
        for obj in objectives:
            obj_dict = obj.to_dict()
            obj_dict['key_results'] = krs_by_obj.get(obj.id, [])
            obj_dict['key_results_count'] = len(obj_dict['key_results'])
            result.append(obj_dict)

        return result


def get_objective_by_id(objective_id: int, user_id: int) -> Optional[Objective]:
    """获取指定目标"""
    with get_db_session() as session:
        return session.query(Objective).filter(
            Objective.id == objective_id,
            Objective.user_id == user_id,
            Objective.deleted_at.is_(None)
        ).first()


def update_objective(objective_id: int, user_id: int, **kwargs) -> bool:
    """更新目标"""
    with get_db_session() as session:
        update_data = {}
        allowed_fields = ['title', 'description', 'status', 'sort_order',
                          'cycle_type', 'cycle_start', 'cycle_end']
        for field in allowed_fields:
            if field in kwargs and kwargs[field] is not None:
                update_data[getattr(Objective, field)] = kwargs[field]

        if not update_data:
            return True

        affected = session.query(Objective).filter(
            Objective.id == objective_id,
            Objective.user_id == user_id,
            Objective.deleted_at.is_(None)
        ).update(update_data)
        return affected > 0


def delete_objective(objective_id: int, user_id: int) -> bool:
    """删除目标（级联删除KRs）"""
    with get_db_session() as session:
        now = datetime.now(timezone.utc)

        # 软删除目标
        affected = session.query(Objective).filter(
            Objective.id == objective_id,
            Objective.user_id == user_id,
            Objective.deleted_at.is_(None)
        ).update({Objective.deleted_at: now}, synchronize_session=False)
        return affected > 0


# ========== KeyResult CRUD ==========

def create_key_result(objective_id: int, user_id: int, title: str,
                       description: Optional[str] = None) -> KeyResult:
    """创建关键结果"""
    with get_db_session() as session:
        kr = KeyResult(
            objective_id=objective_id,
            user_id=user_id,
            title=title,
            description=description
        )
        session.add(kr)
        session.flush()
        return kr


def get_key_results_by_objective(objective_id: int, user_id: int) -> List[KeyResult]:
    """获取目标下的所有KR"""
    with get_db_session() as session:
        return session.query(KeyResult).filter(
            KeyResult.user_id == user_id,
            KeyResult.objective_id == objective_id,
            KeyResult.deleted_at.is_(None)
        ).order_by(KeyResult.sort_order.asc(), KeyResult.created_at.asc()).all()


def get_key_result_by_id(kr_id: int, user_id: int) -> Optional[KeyResult]:
    """获取指定KR"""
    with get_db_session() as session:
        return session.query(KeyResult).filter(
            KeyResult.id == kr_id,
            KeyResult.user_id == user_id,
            KeyResult.deleted_at.is_(None)
        ).first()


def update_key_result(kr_id: int, user_id: int, **kwargs) -> bool:
    """更新KR"""
    with get_db_session() as session:
        update_data = {}
        allowed_fields = ['title', 'description', 'sort_order']
        for field in allowed_fields:
            if field in kwargs and kwargs[field] is not None:
                update_data[getattr(KeyResult, field)] = kwargs[field]

        if not update_data:
            return True

        affected = session.query(KeyResult).filter(
            KeyResult.id == kr_id,
            KeyResult.user_id == user_id,
            KeyResult.deleted_at.is_(None)
        ).update(update_data)
        return affected > 0


def delete_key_result(kr_id: int, user_id: int) -> bool:
    """删除KR"""
    with get_db_session() as session:
        # 软删除KR
        now = datetime.now(timezone.utc)
        affected = session.query(KeyResult).filter(
            KeyResult.id == kr_id,
            KeyResult.user_id == user_id,
            KeyResult.deleted_at.is_(None)
        ).update({KeyResult.deleted_at: now}, synchronize_session=False)
        return affected > 0


def reorder_objectives(user_id: int, objective_ids: List[int]) -> bool:
    """重新排序目标，根据传入的ID顺序设置sort_order"""
    with get_db_session() as session:
        for idx, obj_id in enumerate(objective_ids):
            session.query(Objective).filter(
                Objective.id == obj_id,
                Objective.user_id == user_id,
                Objective.deleted_at.is_(None)
            ).update({Objective.sort_order: idx})
        return True


def reorder_key_results(objective_id: int, user_id: int, kr_ids: List[int]) -> bool:
    """重新排序关键结果，根据传入的ID顺序设置sort_order"""
    with get_db_session() as session:
        for idx, kr_id in enumerate(kr_ids):
            session.query(KeyResult).filter(
                KeyResult.id == kr_id,
                KeyResult.objective_id == objective_id,
                KeyResult.user_id == user_id,
                KeyResult.deleted_at.is_(None)
            ).update({KeyResult.sort_order: idx})
        return True
