#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
团队业务逻辑层
"""

import logging
from typing import Dict, List, Optional

from dao import team_dao
from dao.models import TeamMember
from dao.user_dao import get_user_by_public_user_id

logger = logging.getLogger(__name__)


MAX_TEAM_NAME_LEN = 32
MAX_SEARCH_LIMIT = 10


class TeamServiceError(Exception):
    """团队业务异常（校验失败、权限不足等）"""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


def create_team(creator_user_id: int, name: str) -> Dict:
    """创建团队，创建者自动成为管理员"""
    name = (name or '').strip()
    if not name:
        raise TeamServiceError('团队名称不能为空')
    if len(name) > MAX_TEAM_NAME_LEN:
        raise TeamServiceError(f'团队名称长度不能超过{MAX_TEAM_NAME_LEN}个字符')

    if team_dao.exists_team_by_creator_and_name(creator_user_id=creator_user_id, name=name):
        raise TeamServiceError('同名团队已存在', code=409)

    team = team_dao.create_team_with_admin(name=name, creator_user_id=creator_user_id)
    logger.info('team.create success creator_user_id=%s team_id=%s name=%s',
                creator_user_id, team.id, name)
    result = team.to_dict()
    result['role'] = TeamMember.ROLE_ADMIN
    return result


def search_my_teams(user_id: int, keyword: Optional[str]) -> List[Dict]:
    """搜索当前用户所在的团队，最多返回 MAX_SEARCH_LIMIT 条"""
    teams = team_dao.search_teams_for_user(
        user_id=user_id,
        keyword=keyword or '',
        limit=MAX_SEARCH_LIMIT,
    )

    # 批量查询角色：遍历 team 并查当前用户在该 team 的成员身份
    results = []
    for team in teams:
        member = team_dao.get_team_member(team_id=team.id, user_id=user_id)
        item = team.to_dict()
        item['role'] = member.role if member else None
        item['role_text'] = TeamMember.ROLE_TEXT.get(item['role'], item['role']) if item['role'] else ''
        results.append(item)
    return results


def list_team_members(operator_user_id: int, team_id: int) -> Dict:
    """获取团队成员列表（调用者必须是该团队成员）"""
    team = team_dao.get_team_by_id(team_id=team_id)
    if not team:
        raise TeamServiceError('团队不存在', code=404)

    operator_member = team_dao.get_team_member(team_id=team_id, user_id=operator_user_id)
    if not operator_member:
        raise TeamServiceError('你不是该团队成员', code=403)

    members = team_dao.list_members(team_id=team_id)
    user_map = team_dao.get_users_by_public_ids([m.user_id for m in members])

    member_list = []
    for m in members:
        member_list.append({
            'user_id': m.user_id,
            'name': user_map.get(m.user_id, ''),
            'role': m.role,
            'role_text': TeamMember.ROLE_TEXT.get(m.role, m.role),
            'joined_at': m.to_dict().get('created_at'),
        })

    return {
        'team': team.to_dict(),
        'my_role': operator_member.role,
        'members': member_list,
    }


def add_member(operator_user_id: int, team_id: int, target_user_id: int) -> Dict:
    """管理员添加成员"""
    if not isinstance(target_user_id, int) or target_user_id <= 0:
        raise TeamServiceError('user_id 参数不合法')

    team = team_dao.get_team_by_id(team_id=team_id)
    if not team:
        raise TeamServiceError('团队不存在', code=404)

    operator_member = team_dao.get_team_member(team_id=team_id, user_id=operator_user_id)
    if not operator_member or operator_member.role != TeamMember.ROLE_ADMIN:
        raise TeamServiceError('仅管理员可操作', code=403)

    target_user = get_user_by_public_user_id(public_user_id=target_user_id)
    if not target_user:
        raise TeamServiceError('用户不存在', code=404)

    existing = team_dao.get_team_member(team_id=team_id, user_id=target_user_id)
    if existing:
        raise TeamServiceError('用户已在团队中', code=409)

    member = team_dao.add_member(
        team_id=team_id,
        user_id=target_user_id,
        role=TeamMember.ROLE_MEMBER,
    )
    logger.info('team.add_member success operator=%s team_id=%s target=%s',
                operator_user_id, team_id, target_user_id)
    return {
        'user_id': target_user_id,
        'name': target_user.name,
        'role': member.role,
        'role_text': TeamMember.ROLE_TEXT.get(member.role, member.role),
    }


def delete_member(operator_user_id: int, team_id: int, target_user_id: int) -> None:
    """管理员删除成员（禁止删除唯一管理员）"""
    team = team_dao.get_team_by_id(team_id=team_id)
    if not team:
        raise TeamServiceError('团队不存在', code=404)

    operator_member = team_dao.get_team_member(team_id=team_id, user_id=operator_user_id)
    if not operator_member or operator_member.role != TeamMember.ROLE_ADMIN:
        raise TeamServiceError('仅管理员可操作', code=403)

    target_member = team_dao.get_team_member(team_id=team_id, user_id=target_user_id)
    if not target_member:
        raise TeamServiceError('该用户不是团队成员', code=404)

    if target_member.role == TeamMember.ROLE_ADMIN:
        admin_count = team_dao.count_admins(team_id=team_id)
        if admin_count <= 1:
            raise TeamServiceError('团队需至少保留一个管理员')

    success = team_dao.soft_delete_member(team_id=team_id, user_id=target_user_id)
    if not success:
        raise TeamServiceError('删除成员失败')

    logger.info('team.delete_member success operator=%s team_id=%s target=%s',
                operator_user_id, team_id, target_user_id)


def search_user_by_uid(user_id: int) -> Dict:
    """通过对外 user_id 精确查询用户基础信息"""
    if not isinstance(user_id, int) or user_id <= 0:
        raise TeamServiceError('user_id 参数不合法')

    user = get_user_by_public_user_id(public_user_id=user_id)
    if not user:
        raise TeamServiceError('用户不存在', code=404)
    return {
        'user_id': user.user_id,
        'name': user.name,
    }
