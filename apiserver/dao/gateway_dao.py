#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网关相关数据访问对象
"""

import secrets
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import text

from .connection import get_db_session
from .models import GatewayVirtualKey, GatewayModelPrice


# ──────────────────────────────────────────────────────────────────────────────
# 虚拟秘钥 CRUD
# ──────────────────────────────────────────────────────────────────────────────

def list_virtual_keys() -> List[GatewayVirtualKey]:
    """获取所有未删除的虚拟秘钥，按创建时间倒序"""
    with get_db_session() as session:
        return session.query(GatewayVirtualKey).filter(
            GatewayVirtualKey.deleted_at.is_(None)
        ).order_by(GatewayVirtualKey.created_at.desc()).all()


def create_virtual_key(provider: str, real_key: str, target_url: str, daily_limit: float) -> GatewayVirtualKey:
    """创建一个新的虚拟秘钥（自动生成 vk-xxx 格式的虚拟 Key）"""
    with get_db_session() as session:
        virtual_key_value = 'vk-' + secrets.token_hex(24)
        vk = GatewayVirtualKey(
            provider=provider,
            real_key=real_key,
            virtual_key=virtual_key_value,
            target_url=target_url,
            daily_limit=daily_limit,
        )
        session.add(vk)
        session.flush()
        return vk


def delete_virtual_key(key_id: int) -> bool:
    """软删除虚拟秘钥"""
    with get_db_session() as session:
        vk = session.query(GatewayVirtualKey).filter(
            GatewayVirtualKey.id == key_id,
            GatewayVirtualKey.deleted_at.is_(None)
        ).first()
        if not vk:
            return False
        vk.deleted_at = datetime.now()
        return True


# ──────────────────────────────────────────────────────────────────────────────
# 模型价格 CRUD
# ──────────────────────────────────────────────────────────────────────────────

def list_model_prices() -> List[GatewayModelPrice]:
    """获取所有模型价格配置，按供应商+模型名称排序"""
    with get_db_session() as session:
        return session.query(GatewayModelPrice).order_by(
            GatewayModelPrice.provider,
            GatewayModelPrice.model_name
        ).all()


def create_model_price(provider: str, model_name: str, input_price: float, output_price: float) -> GatewayModelPrice:
    """新增模型价格配置"""
    with get_db_session() as session:
        price = GatewayModelPrice(
            provider=provider,
            model_name=model_name,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
        )
        session.add(price)
        session.flush()
        return price


def delete_model_price(price_id: int) -> bool:
    """删除模型价格配置"""
    with get_db_session() as session:
        affected = session.query(GatewayModelPrice).filter(
            GatewayModelPrice.id == price_id
        ).delete()
        return affected > 0


# ──────────────────────────────────────────────────────────────────────────────
# 监控数据查询
# ──────────────────────────────────────────────────────────────────────────────

def get_usage_stats(days: int = 30) -> list:
    """
    获取最近 N 天的使用量统计，按（日期 × 虚拟秘钥）分组。

    Returns:
        列表，每项包含 stat_date / provider / virtual_key / real_key_masked /
        input_tokens / output_tokens / input_cost / output_cost / total_cost
    """
    with get_db_session() as session:
        start_date = (datetime.now() - timedelta(days=days)).date()

        rows = session.execute(text("""
            SELECT
                ul.stat_date,
                vk.provider,
                vk.virtual_key,
                vk.real_key,
                SUM(ul.input_tokens)  AS input_tokens,
                SUM(ul.output_tokens) AS output_tokens,
                SUM(ul.input_cost)    AS input_cost,
                SUM(ul.output_cost)   AS output_cost
            FROM ai_task_gateway_usage_logs ul
            JOIN ai_task_gateway_virtual_keys vk ON ul.virtual_key_id = vk.id
            WHERE ul.stat_date >= :start_date
            GROUP BY ul.stat_date, vk.id, vk.provider, vk.virtual_key, vk.real_key
            ORDER BY ul.stat_date DESC, vk.virtual_key
        """), {'start_date': str(start_date)}).fetchall()

        result = []
        for row in rows:
            real_key = row.real_key or ''
            masked = real_key[:8] + '****' if len(real_key) > 8 else '****'
            in_cost = float(row.input_cost or 0)
            out_cost = float(row.output_cost or 0)
            result.append({
                'stat_date': str(row.stat_date),
                'provider': row.provider,
                'virtual_key': row.virtual_key,
                'real_key_masked': masked,
                'input_tokens': int(row.input_tokens or 0),
                'output_tokens': int(row.output_tokens or 0),
                'input_cost': round(in_cost, 6),
                'output_cost': round(out_cost, 6),
                'total_cost': round(in_cost + out_cost, 6),
            })
        return result
