#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
商品 Service - 业务逻辑
"""

from typing import List, Optional

from dao import product_dao
from dao.models import Product


def get_products() -> List[Product]:
    return product_dao.get_all_products()


def get_product(product_id: int) -> Optional[Product]:
    return product_dao.get_product_by_id(product_id=product_id)
