#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
团队数据访问对象
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import or_

from .connection import get_db_session
from .models import Team, TeamMember, User


def create_team_with_admin(name: str, creator_user_id: int) -> Team:
    """创建团队并将创建者设为管理员（同一事务）"""
    with get_db_session() as session:
        team = Team(name=name, creator_user_id=creator_user_id)
        session.add(team)
        session.flush()  # 分配 team.id

        member = TeamMember(
            team_id=team.id,
            user_id=creator_user_id,
            role=TeamMember.ROLE_ADMIN,
        )
        session.add(member)
        session.flush()
        return team


def exists_team_by_creator_and_name(creator_user_id: int, name: str) -> bool:
    """同一创建者是否已存在同名活跃团队"""
    with get_db_session() as session:
        count = session.query(Team).filter(
            Team.creator_user_id == creator_user_id,
            Team.name == name,
            Team.deleted_at.is_(None),
        ).count()
        return count > 0


def get_team_by_id(team_id: int) -> Optional[Team]:
    """按 id 获取活跃团队"""
    with get_db_session() as session:
        return session.query(Team).filter(
            Team.id == team_id,
            Team.deleted_at.is_(None),
        ).first()


def search_teams_for_user(user_id: int, keyword: str, limit: int = 10) -> List[Team]:
    """
    搜索当前用户所在的团队，最多 limit 条。

    - 若 keyword 为纯数字：优先按 team_id 精确匹配，再按名称模糊匹配
    - 否则：仅按名称模糊匹配
    - keyword 为空：返回当前用户最近加入的 limit 个团队
    """
    with get_db_session() as session:
        base_query = (
            session.query(Team)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .filter(
                Team.deleted_at.is_(None),
                TeamMember.deleted_at.is_(None),
                TeamMember.user_id == user_id,
            )
        )

        keyword = (keyword or '').strip()
        if not keyword:
            teams = base_query.order_by(TeamMember.created_at.desc()).limit(limit).all()
            return list(teams)

        results: List[Team] = []
        seen_ids = set()

        # 纯数字：先精确 team_id
        if keyword.isdigit():
            try:
                tid = int(keyword)
            except ValueError:
                tid = None
            if tid is not None:
                exact = base_query.filter(Team.id == tid).limit(limit).all()
                for t in exact:
                    if t.id not in seen_ids:
                        results.append(t)
                        seen_ids.add(t.id)

        if len(results) < limit:
            like_pattern = f"%{keyword}%"
            remaining = limit - len(results)
            name_matches = (
                base_query.filter(Team.name.like(like_pattern))
                .order_by(Team.created_at.desc())
                .limit(remaining + len(results))
                .all()
            )
            for t in name_matches:
                if t.id in seen_ids:
                    continue
                results.append(t)
                seen_ids.add(t.id)
                if len(results) >= limit:
                    break

        return results[:limit]


def get_team_member(team_id: int, user_id: int) -> Optional[TeamMember]:
    """获取指定用户在团队中的成员记录"""
    with get_db_session() as session:
        return session.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
            TeamMember.deleted_at.is_(None),
        ).first()


def list_members(team_id: int) -> List[TeamMember]:
    """列出团队成员（活跃记录）"""
    with get_db_session() as session:
        members = session.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.deleted_at.is_(None),
        ).order_by(TeamMember.created_at.asc()).all()
        return list(members)


def count_admins(team_id: int) -> int:
    """统计团队中管理员数量"""
    with get_db_session() as session:
        return session.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.role == TeamMember.ROLE_ADMIN,
            TeamMember.deleted_at.is_(None),
        ).count()


def add_member(team_id: int, user_id: int, role: str = TeamMember.ROLE_MEMBER) -> TeamMember:
    """添加成员（调用方需先校验未重复）"""
    with get_db_session() as session:
        member = TeamMember(team_id=team_id, user_id=user_id, role=role)
        session.add(member)
        session.flush()
        return member


def soft_delete_member(team_id: int, user_id: int) -> bool:
    """软删除团队成员"""
    with get_db_session() as session:
        now = datetime.now(timezone.utc)
        affected = session.query(TeamMember).filter(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
            TeamMember.deleted_at.is_(None),
        ).update({TeamMember.deleted_at: now}, synchronize_session=False)
        return affected > 0


def get_users_by_public_ids(public_user_ids: List[int]) -> dict:
    """批量根据对外 user_id 获取用户基础信息，返回 {user_id: name}"""
    if not public_user_ids:
        return {}
    with get_db_session() as session:
        users = session.query(User).filter(User.user_id.in_(public_user_ids)).all()
        return {u.user_id: u.name for u in users}
