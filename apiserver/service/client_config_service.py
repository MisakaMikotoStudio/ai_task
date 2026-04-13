#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端配置同步服务 —— 仓库配置与环境变量的全量同步
"""

import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

from dao.client_dao import (
    apply_client_env_var_sync,
    apply_client_repo_sync,
    get_client_env_vars,
    get_client_repos,
)
from dao.models import ClientEnvVar, ClientRepo
from service.client_service import ClientEnvVarSaveError, ClientRepoSaveError

logger = logging.getLogger(__name__)


def parse_repo_name_from_url(url: str) -> str:
    """
    从仓库 URL 解析仓库名（路径最后一段，去掉 .git）。
    支持 https/http、git@host:path、ssh:// 等形式。
    """
    u = (url or "").strip()
    if not u:
        raise ClientRepoSaveError("仓库 URL 不能为空")

    path = ""

    if u.startswith("git@") or (
        "@" in u and ":" in u and not u.startswith("http") and not u.startswith("ssh://")
    ):
        at = u.find("@")
        colon = u.find(":", at)
        if colon == -1:
            raise ClientRepoSaveError(f"无法从 URL 解析仓库名称: {u}")
        path = u[colon + 1 :]
    elif u.startswith("http://") or u.startswith("https://") or u.startswith("ssh://"):
        parsed = urlparse(u)
        path = parsed.path or ""
    else:
        path = u

    path = path.strip("/")
    if not path:
        raise ClientRepoSaveError(f"无法从 URL 解析仓库名称: {u}")

    parts = [p for p in path.split("/") if p]
    if not parts:
        raise ClientRepoSaveError(f"无法从 URL 解析仓库名称: {u}")

    name = parts[-1]
    if name.endswith(".git"):
        name = name[:-4]
    if not name:
        raise ClientRepoSaveError(f"无法从 URL 解析仓库名称: {u}")
    return name


def _row_fields_equal(existing: ClientRepo, incoming: dict) -> bool:
    ex_tok = "" if existing.token is None else existing.token
    in_tok = incoming.get("token")
    in_tok = "" if in_tok is None else in_tok
    return (
        (existing.desc or "") == (incoming.get("desc") or "")
        and (existing.url or "") == (incoming.get("url") or "")
        and ex_tok == in_tok
        and (existing.default_branch or "") == (incoming.get("default_branch") or "")
        and (existing.branch_prefix or "ai_") == (incoming.get("branch_prefix") or "ai_")
        and bool(existing.docs_repo) == bool(incoming.get("docs_repo", False))
    )


def _validate_docs_repo_policy(repos: List[dict]) -> None:
    docs_repo_count = sum(1 for r in repos if r.get("docs_repo"))
    if docs_repo_count == 0:
        raise ClientRepoSaveError("必须指定一个文档仓库")
    if docs_repo_count > 1:
        raise ClientRepoSaveError("只能指定一个文档仓库")
    non_docs_repo_count = len(repos) - docs_repo_count
    if non_docs_repo_count == 0:
        raise ClientRepoSaveError("除文档仓库外，至少需要一个代码仓库")


def save_client_repos(
    client_id: int,
    repos: List[dict],
    *,
    user_id: int,
    require_docs_repo: bool = True,
) -> bool:
    """
    按「仓库名（由 URL 解析）」全量同步仓库配置：删除输入中不存在的、
    新增未有记录、已存在且字段有变化则更新。

    Returns:
        是否发生了任意持久化变更（用于决定是否 bump 客户端版本等）

    Raises:
        ClientRepoSaveError: 解析失败、提交列表同名冲突、文档仓库策略不满足等
    """
    if require_docs_repo:
        _validate_docs_repo_policy(repos)

    existing_list = get_client_repos(client_id, user_id)
    exist_repos: Dict[str, ClientRepo] = {}
    delete_ids: List[int] = []
    for er in existing_list:
        key = parse_repo_name_from_url(er.url).lower()
        if key in exist_repos:
            delete_ids.append(er.id)
        else:
            exist_repos[key] = er

    input_repos: Dict[str, dict] = {}
    for repo in repos:
        url = (repo.get("url") or "").strip()
        if not url:
            raise ClientRepoSaveError("提交的仓库列表中存在 URL 为空的仓库")
        key = parse_repo_name_from_url(url).lower()
        if key in input_repos:
            raise ClientRepoSaveError("提交的仓库列表中存在重复的仓库（由 URL 解析）：" + key)
        input_repos[key] = repo

    updates: List[dict] = []
    inserts: List[dict] = []

    for key, inc in input_repos.items():
        ex = exist_repos.get(key)
        if ex is None:
            inserts.append(
                {
                    "desc": inc.get("desc", ""),
                    "url": inc.get("url", ""),
                    "token": inc.get("token"),
                    "default_branch": inc.get("default_branch", ""),
                    "branch_prefix": inc.get("branch_prefix", "ai_"),
                    "docs_repo": inc.get("docs_repo", False),
                }
            )
        elif not _row_fields_equal(ex, inc):
            updates.append(
                {
                    "id": ex.id,
                    "desc": inc.get("desc", ""),
                    "url": inc.get("url", ""),
                    "token": inc.get("token"),
                    "default_branch": inc.get("default_branch", ""),
                    "branch_prefix": inc.get("branch_prefix", "ai_"),
                    "docs_repo": inc.get("docs_repo", False),
                }
            )

    if not delete_ids and not updates and not inserts:
        return False

    apply_client_repo_sync(
        client_id=client_id,
        user_id=user_id,
        delete_ids=delete_ids,
        updates=updates,
        inserts=inserts,
    )
    return True


def _env_value_equal(existing: ClientEnvVar, incoming_value: str) -> bool:
    return (existing.value or "") == (incoming_value or "")


def _env_var_env_equal(existing: ClientEnvVar, incoming_env) -> bool:
    """比较 env 字段是否相等（None 和 '' 视为相同）"""
    ex_env = existing.env or None
    in_env = incoming_env or None
    return ex_env == in_env


def save_client_env_vars(client_id: int, env_items: List[dict], *, user_id: int) -> bool:
    """
    以 (env, key) 组合为维度全量同步环境变量。
    - 请求体中不存在的已激活记录做软删除
    - 不存在则新增；已存在且 value/env 有变化则更新
    - 支持 env 字段（test/prod/None）

    Returns:
        是否发生了任意持久化变更（用于决定是否 bump 客户端版本等）

    Raises:
        ClientEnvVarSaveError: 空 key、提交列表中 key 重复等
    """
    # input: {(env, key): value}
    input_env_vars: Dict[tuple, str] = {}
    input_env_map: Dict[tuple, Optional[str]] = {}
    for item in env_items:
        k = (item.get("key") or "").strip()
        if not k:
            raise ClientEnvVarSaveError("环境变量名不能为空")
        v = item.get("value", "")
        if v is None:
            v = ""
        else:
            v = str(v)
        env_val = item.get("env") or None
        dedup_key = (env_val, k)
        if dedup_key in input_env_vars:
            raise ClientEnvVarSaveError("提交的环境变量中存在重复的键（同环境）: " + k)
        input_env_vars[dedup_key] = v
        input_env_map[dedup_key] = env_val

    existing_list = get_client_env_vars(client_id, user_id)
    exist_env_vars: Dict[tuple, ClientEnvVar] = {}
    delete_ids: List[int] = []
    for ev in existing_list:
        k = (ev.key or "").strip()
        if not k:
            delete_ids.append(ev.id)
            continue
        ev_env = ev.env or None
        dedup_key = (ev_env, k)
        if dedup_key in exist_env_vars:
            delete_ids.append(ev.id)
        else:
            exist_env_vars[dedup_key] = ev

    inserts: List[dict] = []
    updates: List[dict] = []

    for dedup_key, v in input_env_vars.items():
        env_val, k = dedup_key
        ex = exist_env_vars.get(dedup_key)
        if ex is None:
            inserts.append({"key": k, "value": v, "env": env_val})
        elif not _env_value_equal(ex, v) or not _env_var_env_equal(ex, env_val):
            updates.append({"id": ex.id, "key": k, "value": v, "env": env_val})

    for dedup_key, ex in exist_env_vars.items():
        if dedup_key not in input_env_vars:
            delete_ids.append(ex.id)

    if not delete_ids and not updates and not inserts:
        return False

    apply_client_env_var_sync(
        client_id=client_id,
        user_id=user_id,
        delete_ids=delete_ids,
        updates=updates,
        inserts=inserts,
    )
    return True
