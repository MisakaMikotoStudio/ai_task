#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OKR 相关路由
"""

from flask import Blueprint, request, jsonify

from service.okr_service import (
    create_objective, get_objectives, get_objective, update_objective, delete_objective,
    create_key_result, update_key_result, delete_key_result,
    reorder_objectives, reorder_key_results
)

okr_bp = Blueprint('okr', __name__)


# ========== Objective Routes ==========

@okr_bp.route('/objectives', methods=['POST'])
def create_objective_api():
    """创建目标"""
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    result = create_objective(
        user_id=request.user_info.user_id,
        title=data.get('title', ''),
        description=data.get('description'),
        cycle_type=data.get('cycle_type', 'quarter'),
        cycle_start=data.get('cycle_start'),
        cycle_end=data.get('cycle_end')
    )

    return jsonify({'code': 201, 'message': '目标创建成功', 'data': result}), 201


@okr_bp.route('/objectives', methods=['GET'])
def list_objectives():
    """获取目标列表

    支持周期范围过滤（优化查询性能）：
    - cycle_start: 周期开始日期 (YYYY-MM-DD)
    - cycle_end: 周期结束日期 (YYYY-MM-DD)
    """
    cycle_type = request.args.get('cycle_type')
    status = request.args.get('status')
    cycle_start = request.args.get('cycle_start')
    cycle_end = request.args.get('cycle_end')

    objectives = get_objectives(request.user_info.user_id, cycle_type, status, cycle_start, cycle_end)

    return jsonify({'code': 200, 'message': '获取目标列表成功', 'data': objectives})


@okr_bp.route('/objectives/<int:objective_id>', methods=['GET'])
def get_objective_api(objective_id):
    """获取目标详情"""
    objective = get_objective(objective_id, request.user_info.user_id)

    return jsonify({'code': 200, 'message': '获取目标成功', 'data': objective})


@okr_bp.route('/objectives/<int:objective_id>', methods=['PUT'])
def update_objective_api(objective_id):
    """更新目标"""
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    result = update_objective(objective_id, request.user_info.user_id, **data)

    return jsonify({'code': 200, 'message': '目标更新成功', 'data': result})


@okr_bp.route('/objectives/<int:objective_id>', methods=['DELETE'])
def delete_objective_api(objective_id):
    """删除目标"""
    result = delete_objective(objective_id, request.user_info.user_id)

    return jsonify({'code': 200, 'message': '目标删除成功', 'data': result})


# ========== KeyResult Routes ==========

@okr_bp.route('/objectives/<int:objective_id>/key-results', methods=['POST'])
def create_key_result_api(objective_id):
    """创建关键结果"""
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    result = create_key_result(
        objective_id=objective_id,
        user_id=request.user_info.user_id,
        title=data.get('title', ''),
        description=data.get('description')
    )

    return jsonify({'code': 201, 'message': 'KR创建成功', 'data': result}), 201


@okr_bp.route('/key-results/<int:kr_id>', methods=['PUT'])
def update_key_result_api(kr_id):
    """更新KR"""
    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    result = update_key_result(kr_id, request.user_info.user_id, **data)

    return jsonify({'code': 200, 'message': 'KR更新成功', 'data': result})


@okr_bp.route('/key-results/<int:kr_id>', methods=['DELETE'])
def delete_key_result_api(kr_id):
    """删除KR"""
    result = delete_key_result(kr_id, request.user_info.user_id)

    return jsonify({'code': 200, 'message': 'KR删除成功', 'data': result})


# ========== Reorder Routes ==========

@okr_bp.route('/objectives/reorder', methods=['POST'])
def reorder_objectives_api():
    """重新排序目标"""
    data = request.get_json()
    if not data or 'objective_ids' not in data:
        return jsonify({'code': 400, 'message': '请提供 objective_ids 列表'}), 400

    result = reorder_objectives(request.user_info.user_id, data['objective_ids'])

    return jsonify({'code': 200, 'message': '排序更新成功', 'data': result})


@okr_bp.route('/objectives/<int:objective_id>/key-results/reorder', methods=['POST'])
def reorder_key_results_api(objective_id):
    """重新排序KR"""
    data = request.get_json()
    if not data or 'kr_ids' not in data:
        return jsonify({'code': 400, 'message': '请提供 kr_ids 列表'}), 400

    result = reorder_key_results(objective_id, request.user_info.user_id, data['kr_ids'])

    return jsonify({'code': 200, 'message': 'KR排序更新成功', 'data': result})
