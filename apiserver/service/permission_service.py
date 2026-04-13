#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
通用鉴权 Service
提供 check 函数，根据权限配置表校验用户权限。

支持的鉴权类型：
- subscribed: 订阅限制，用户存在有效商品即可通过
- count_limit: 总量限制，传入当前数量不能超过配置的 limit
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, List

from dao import permission_dao, order_dao
from dao.models import PermissionConfig

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """鉴权结果"""
    passed: bool
    message: str = ''
    products: List[str] = field(default_factory=list)
    check_type: str = ''
    limit: Optional[int] = None

    def to_response_data(self) -> Optional[dict]:
        """转换为接口响应 data 字段"""
        if self.passed:
            return None
        data = {
            'type': self.check_type,
            'products': self.products,
        }
        if self.limit is not None:
            data['limit'] = self.limit
        return data


def check(user_id: int, key: str, params: Optional[int] = None) -> CheckResult:
    """
    通用鉴权函数

    Args:
        user_id: 用户 ID
        key: 权限 key（对应 permission_configs 表的 key 字段）
        params: 鉴权参数，count_limit 类型时传入当前已达到的数量

    Returns:
        CheckResult 鉴权结果
    """
    configs = permission_dao.get_configs_by_key(key=key)
    if not configs:
        # 无权限配置记录，默认通过（无限制）
        return CheckResult(passed=True)

    check_type = configs[0].type
    product_keys = [c.product_key for c in configs]

    # 查询用户有效订单
    active_orders = order_dao.get_user_active_orders(user_id=user_id)

    # 筛选匹配的有效订单
    if product_keys:
        product_key_set = set(product_keys)
        valid_orders = [o for o in active_orders if o.product_key in product_key_set]
    else:
        valid_orders = list(active_orders)

    # 无有效商品
    if not valid_orders:
        if product_keys:
            message = f'需要有效订阅才能执行此操作，请前往商店购买相关商品'
        else:
            message = '需要有效订阅才能执行此操作，请前往商店购买任一商品'
        return CheckResult(
            passed=False,
            message=message,
            products=product_keys,
            check_type=check_type,
        )

    # subscribed 类型：存在有效商品即通过
    if check_type == PermissionConfig.TYPE_SUBSCRIBED:
        return CheckResult(passed=True, check_type=check_type)

    # count_limit 类型：检查数量限制
    if check_type == PermissionConfig.TYPE_COUNT_LIMIT:
        if params is None:
            logger.warning(
                "count_limit 鉴权缺少 params 参数, user_id=%s, key=%s",
                user_id, key,
            )
            return CheckResult(passed=False, message='鉴权参数缺失', check_type=check_type)

        # 获取用户拥有的有效产品 key 集合
        valid_product_keys = {o.product_key for o in valid_orders}

        # 从匹配的配置中取最大 limit
        max_limit = 0
        for config in configs:
            if config.product_key in valid_product_keys:
                detail = config.config_detail or {}
                limit_val = detail.get('limit', 0)
                if isinstance(limit_val, int) and limit_val > max_limit:
                    max_limit = limit_val

        if max_limit <= 0:
            logger.warning(
                "count_limit 配置中 limit 无效, user_id=%s, key=%s",
                user_id, key,
            )
            return CheckResult(
                passed=False,
                message='权限配置异常，请联系管理员',
                products=product_keys,
                check_type=check_type,
            )

        current_count = int(params)
        if current_count >= max_limit:
            return CheckResult(
                passed=False,
                message=f'已达到数量上限（{max_limit}），请前往商店升级或购买更多额度',
                products=product_keys,
                check_type=check_type,
                limit=max_limit,
            )

        return CheckResult(passed=True, check_type=check_type)

    # 未知类型默认拒绝（fail-closed），避免新增类型时遗漏校验逻辑
    logger.warning("未知的权限校验类型: %s, key=%s", check_type, key)
    return CheckResult(passed=False, message='权限校验配置异常，请联系管理员', check_type=check_type)
