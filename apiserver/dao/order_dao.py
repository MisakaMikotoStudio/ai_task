#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
订单 DAO - 纯数据库操作
"""

from datetime import datetime, timezone
from typing import Optional, List, Tuple

from sqlalchemy import or_

from .connection import get_session
from .models import Order


def create_order(user_id: int, product_id: int, product_key: str,
                 out_trade_no: str, amount: float, order_type: str,
                 expire_at: Optional[datetime]) -> Order:
    """创建订单"""
    session = get_session()
    order = Order(
        user_id=user_id,
        product_id=product_id,
        product_key=product_key,
        out_trade_no=out_trade_no,
        status=Order.STATUS_PENDING,
        amount=amount,
        order_type=order_type,
        expire_at=expire_at,
    )
    session.add(order)
    session.flush()
    return order


def get_order_by_out_trade_no(out_trade_no: str) -> Optional[Order]:
    """按商户订单号查询订单"""
    session = get_session()
    return session.query(Order).filter(Order.out_trade_no == out_trade_no).first()


def get_order_by_id(order_id: int) -> Optional[Order]:
    """按订单 ID 查询订单"""
    session = get_session()
    return session.query(Order).filter(Order.id == order_id).first()


def mark_order_paid(out_trade_no: str, trade_no: str) -> bool:
    """将订单标记为已支付（幂等：仅更新 pending 状态的订单）"""
    session = get_session()
    rows = (session.query(Order)
            .filter(Order.out_trade_no == out_trade_no,
                    Order.status == Order.STATUS_PENDING)
            .update({'status': Order.STATUS_PAID, 'trade_no': trade_no}))
    return rows > 0


def mark_order_refunded(out_trade_no: str) -> bool:
    """将订单标记为已退款（幂等：仅更新 paid 状态的订单）"""
    session = get_session()
    rows = (session.query(Order)
            .filter(Order.out_trade_no == out_trade_no,
                    Order.status == Order.STATUS_PAID)
            .update({'status': Order.STATUS_REFUNDED}))
    return rows > 0


def get_user_active_orders(user_id: int) -> List[Order]:
    """获取用户当前有效的服务订单（已支付且未过期或永久有效），按商品 key 排序后取每个商品到期最晚的订单"""
    session = get_session()
    now = datetime.now(timezone.utc)
    return (session.query(Order)
            .filter(
                Order.user_id == user_id,
                Order.status == Order.STATUS_PAID,
                or_(Order.expire_at.is_(None), Order.expire_at > now)
            )
            .order_by(Order.product_key, Order.expire_at.desc())
            .all())


def list_orders(page: int = 1, page_size: int = 20,
                user_id: Optional[int] = None,
                status: Optional[str] = None) -> Tuple[List[Order], int]:
    """分页查询订单列表，返回 (orders, total)"""
    session = get_session()
    query = session.query(Order)

    if user_id is not None:
        query = query.filter(Order.user_id == user_id)
    if status:
        query = query.filter(Order.status == status)

    total = query.count()
    orders = (query.order_by(Order.created_at.desc())
              .offset((page - 1) * page_size)
              .limit(page_size)
              .all())
    return orders, total
