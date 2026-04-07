#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
商品 DAO - 纯数据库操作
"""

from datetime import datetime, timezone
from typing import Optional, List

from .connection import get_session
from .models import Product


def get_all_products() -> List[Product]:
    """查询所有未删除商品"""
    session = get_session()
    return session.query(Product).filter(Product.deleted_at.is_(None)).all()


def list_all_products_admin() -> List[Product]:
    """管理端：含已下架（软删除）商品，按 id 倒序"""
    session = get_session()
    return session.query(Product).order_by(Product.id.desc()).all()


def get_product_by_id(product_id: int) -> Optional[Product]:
    """按 ID 查询商品"""
    session = get_session()
    return (session.query(Product)
            .filter(Product.id == product_id, Product.deleted_at.is_(None))
            .first())


def get_product_by_key(key: str) -> Optional[Product]:
    """按 key 查询商品"""
    session = get_session()
    return (session.query(Product)
            .filter(Product.key == key, Product.deleted_at.is_(None))
            .first())


def create_product(key: str, title: str, desc: str, price: float,
                   expire_time: Optional[int], support_continue: bool,
                   icon: Optional[str]) -> Product:
    """新增商品"""
    session = get_session()
    product = Product(
        key=key,
        title=title,
        desc=desc,
        price=price,
        expire_time=expire_time,
        support_continue=support_continue,
        icon=icon,
    )
    session.add(product)
    session.flush()
    return product


def update_product_icon(product_id: int, icon_url: str) -> bool:
    """更新商品封面图"""
    session = get_session()
    rows = (session.query(Product)
            .filter(Product.id == product_id, Product.deleted_at.is_(None))
            .update({'icon': icon_url}))
    return rows > 0


def soft_delete_product(product_id: int) -> bool:
    """软删除（下架）：仅处理当前上架中的商品"""
    session = get_session()
    now = datetime.now(timezone.utc)
    rows = (session.query(Product)
            .filter(Product.id == product_id, Product.deleted_at.is_(None))
            .update({'deleted_at': now}))
    return rows > 0
