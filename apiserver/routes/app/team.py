#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
团队管理路由（Web 前端调用）
"""

from flask import Blueprint, request, jsonify

from service.team_service import (
    TeamServiceError,
    create_team,
    search_my_teams,
    list_team_members,
    add_member,
    delete_member,
    search_user_by_uid,
)

team_bp = Blueprint('app_team', __name__)


@team_bp.route('', methods=['POST'])
def create_team_api():
    """新建团队，创建者默认为管理员"""
    data = request.get_json() or {}
    name = data.get('name', '')
    try:
        team = create_team(creator_user_id=request.user_info.user_id, name=name)
    except TeamServiceError as e:
        return jsonify({'code': e.code, 'message': e.message}), e.code
    return jsonify({'code': 201, 'message': '团队创建成功', 'data': team}), 201


@team_bp.route('', methods=['GET'])
def search_teams_api():
    """按关键字搜索当前用户所在的团队（最多 10 条）"""
    keyword = request.args.get('keyword', '', type=str)
    teams = search_my_teams(user_id=request.user_info.user_id, keyword=keyword)
    return jsonify({'code': 200, 'message': '查询成功', 'data': teams})


@team_bp.route('/<int:team_id>/members', methods=['GET'])
def list_members_api(team_id):
    """获取团队成员列表"""
    try:
        payload = list_team_members(operator_user_id=request.user_info.user_id, team_id=team_id)
    except TeamServiceError as e:
        return jsonify({'code': e.code, 'message': e.message}), e.code
    return jsonify({'code': 200, 'message': '查询成功', 'data': payload})


@team_bp.route('/<int:team_id>/members', methods=['POST'])
def add_member_api(team_id):
    """管理员通过 uid 添加成员"""
    data = request.get_json() or {}
    try:
        target_user_id = int(data.get('user_id') or 0)
    except (TypeError, ValueError):
        return jsonify({'code': 400, 'message': 'user_id 参数不合法'}), 400

    try:
        member = add_member(
            operator_user_id=request.user_info.user_id,
            team_id=team_id,
            target_user_id=target_user_id,
        )
    except TeamServiceError as e:
        return jsonify({'code': e.code, 'message': e.message}), e.code
    return jsonify({'code': 201, 'message': '成员添加成功', 'data': member}), 201


@team_bp.route('/<int:team_id>/members/<int:target_user_id>', methods=['DELETE'])
def delete_member_api(team_id, target_user_id):
    """管理员删除成员"""
    try:
        delete_member(
            operator_user_id=request.user_info.user_id,
            team_id=team_id,
            target_user_id=target_user_id,
        )
    except TeamServiceError as e:
        return jsonify({'code': e.code, 'message': e.message}), e.code
    return jsonify({'code': 200, 'message': '成员已删除'})


@team_bp.route('/users/search', methods=['GET'])
def search_user_api():
    """通过 uid 精确查询用户基础信息（用于成员添加前校验）"""
    raw = request.args.get('user_id', '', type=str).strip()
    try:
        target_user_id = int(raw)
    except (TypeError, ValueError):
        return jsonify({'code': 400, 'message': 'user_id 参数不合法'}), 400

    try:
        user = search_user_by_uid(user_id=target_user_id)
    except TeamServiceError as e:
        return jsonify({'code': e.code, 'message': e.message}), e.code
    return jsonify({'code': 200, 'message': '查询成功', 'data': user})
