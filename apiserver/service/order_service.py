#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
订单 Service - 业务逻辑
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from dao import order_dao
from dao.models import Order, Product

logger = logging.getLogger(__name__)


def _generate_out_trade_no() -> str:
    """生成唯一商户订单号：时间戳 + UUID"""
    ts = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    uid = uuid.uuid4().hex[:12].upper()
    return f'{ts}{uid}'


def create_order(user_id: int, product: Product, order_type: str) -> Order:
    """
    创建订单
    - 计算到期时间（如果商品有 expire_time）
    - 生成唯一 out_trade_no
    """
    out_trade_no = _generate_out_trade_no()

    expire_at: Optional[datetime] = None
    if product.expire_time:
        expire_at = datetime.now(timezone.utc) + timedelta(seconds=product.expire_time)

    order = order_dao.create_order(
        user_id=user_id,
        product_id=product.id,
        product_key=product.key,
        out_trade_no=out_trade_no,
        amount=float(product.price),
        order_type=order_type,
        expire_at=expire_at,
    )
    logger.info("订单创建: user_id=%s, product_key=%s, out_trade_no=%s",
                user_id, product.key, out_trade_no)
    return order


def confirm_paid(out_trade_no: str, trade_no: str):
    """
    确认订单支付成功（幂等）
    仅更新 pending 状态的订单，已 paid 的忽略
    """
    updated = order_dao.mark_order_paid(out_trade_no=out_trade_no, trade_no=trade_no)
    if updated:
        logger.info("订单支付确认: out_trade_no=%s, trade_no=%s", out_trade_no, trade_no)
    else:
        logger.info("订单已处理或不存在（幂等）: out_trade_no=%s", out_trade_no)


def call_third_party_refund(order: Order) -> None:
    """
    调用第三方平台退款（默认未实现）
    业务方可在此接入支付宝/微信/其他平台退款接口。
    """
    raise NotImplementedError("请实现第三方平台退款逻辑：service.order_service.call_third_party_refund")


def handle_refund_post_business(order: Order) -> None:
    """
    退款成功后的业务处理（默认未实现）
    例如：权益收回、回滚发放记录、通知等。
    """
    raise NotImplementedError("请实现退款后的业务逻辑：service.order_service.handle_refund_post_business")


def refund_order(out_trade_no: str) -> Order:
    """
    管理后台发起退款
    - 仅允许对 paid 状态订单退款
    - 成功后标记订单为 refunded，并执行退款后业务逻辑
    """
    order = order_dao.get_order_by_out_trade_no(out_trade_no=out_trade_no)
    if not order:
        raise ValueError("订单不存在")

    if order.status == Order.STATUS_REFUNDED:
        return order

    if order.status != Order.STATUS_PAID:
        raise ValueError("仅支持对已支付订单发起退款")

    call_third_party_refund(order=order)

    updated = order_dao.mark_order_refunded(out_trade_no=out_trade_no)
    if not updated:
        order = order_dao.get_order_by_out_trade_no(out_trade_no=out_trade_no)
        if not order or order.status != Order.STATUS_REFUNDED:
            raise RuntimeError("退款标记失败，请重试")
        return order

    order = order_dao.get_order_by_out_trade_no(out_trade_no=out_trade_no)
    if not order:
        raise RuntimeError("订单读取失败")

    handle_refund_post_business(order=order)
    return order
