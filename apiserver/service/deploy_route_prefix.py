#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""部署路由前缀：校验与规范化，供保存配置与远程 nginx 生成共用。"""

import re
from typing import Iterable, List, Tuple

# 路径段：字母数字、点、下划线、连字符
_SEGMENT = r'[a-zA-Z0-9._-]+'
_PATH_RE = re.compile(rf'^/(/{_SEGMENT})*$')


def normalize_deploy_route_prefix(raw: str) -> str:
    """
    规范为 nginx 使用的逻辑前缀：
    - 空串或仅「/」→ 根路径「/」
    - 否则必须以 / 开头，无尾随斜杠，如 /api、/api/v1
    """
    s = (raw or '').strip()
    if not s or s == '/':
        return '/'
    if not s.startswith('/'):
        s = '/' + s
    while '//' in s:
        s = s.replace('//', '/')
    s = s.rstrip('/')
    if not s or s == '/':
        return '/'
    if not _PATH_RE.match(s):
        raise ValueError(f'路由前缀格式无效（仅允许以 / 开头的路径段，字符限于字母数字 ._-）：{raw!r}')
    return s


def validate_unique_route_prefixes(normalized: Iterable[str]) -> None:
    """同一应用下前缀不可重复；重复时抛出 ValueError。"""
    seen: dict[str, int] = {}
    for i, p in enumerate(normalized):
        if p in seen:
            raise ValueError(f'路由前缀冲突：{p!r}（第 {seen[p] + 1} 条与第 {i + 1} 条部署）')
        seen[p] = i


def pairs_from_deploys(
    deploys: List[object],
    container_names: List[str],
) -> List[Tuple[str, str]]:
    """
    根据 ClientDeploy 列表与对应容器名生成 (规范前缀, 容器名) 列表。

    deploys 与 container_names 须等长且顺序一致。
    """
    if len(deploys) != len(container_names):
        raise ValueError('内部错误：部署配置数量与容器数量不一致')
    prefixes: List[str] = []
    for d in deploys:
        raw = getattr(d, 'route_prefix', None)
        prefixes.append(normalize_deploy_route_prefix(raw if raw is not None else ''))
    validate_unique_route_prefixes(prefixes)
    return list(zip(prefixes, container_names))
