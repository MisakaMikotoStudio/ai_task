#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
管理后台路由（所有路由需要 X-Admin-Token 认证，由 auth_plugin 统一处理）
- POST /api/admin/product              - 新增商品
- GET  /api/admin/products             - 商品列表（含已下架）
- POST /api/admin/product/<id>/offline - 商品下架（软删除）
- GET  /api/admin/orders               - 查询购买记录
- POST /api/admin/upload/icon          - 上传商品封面图到 OSS
"""

import logging

from flask import Blueprint, request, jsonify, current_app

from dao import get_db_session
from dao import product_dao, order_dao
from service import oss_service

logger = logging.getLogger(__name__)
admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/product', methods=['POST'])
def create_product():
    """新增商品"""
    data = request.get_json(silent=True) or {}

    key = (data.get('key') or '').strip()
    title = (data.get('title') or '').strip()
    desc = data.get('desc') or ''
    price = data.get('price')
    expire_time = data.get('expire_time')   # 秒，可为 null（永久）
    support_continue = bool(data.get('support_continue', False))
    icon = (data.get('icon') or '').strip() or None

    if not key or not title or price is None:
        return jsonify({'code': 400, 'message': 'key、title、price 为必填项', 'data': None}), 400

    try:
        price = float(price)
        if price <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'code': 400, 'message': 'price 必须是正数', 'data': None}), 400

    if expire_time is not None:
        try:
            expire_time = int(expire_time)
            if expire_time <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({'code': 400, 'message': 'expire_time 必须是正整数（秒）', 'data': None}), 400

    # 检查 key 唯一
    existing = product_dao.get_product_by_key(key=key)
    if existing:
        return jsonify({'code': 409, 'message': f'商品 key "{key}" 已存在', 'data': None}), 409

    with get_db_session():
        product = product_dao.create_product(
            key=key,
            title=title,
            desc=desc,
            price=price,
            expire_time=expire_time,
            support_continue=support_continue,
            icon=icon,
        )

    return jsonify({'code': 200, 'message': 'ok', 'data': product.to_dict()})


@admin_bp.route('/products', methods=['GET'])
def list_products_admin():
    """管理端商品列表（含已下架）"""
    products = product_dao.list_all_products_admin()
    data = []
    for p in products:
        d = p.to_dict()
        d['offline'] = p.deleted_at is not None
        data.append(d)
    return jsonify({'code': 200, 'message': 'ok', 'data': data})


@admin_bp.route('/product/<int:product_id>/offline', methods=['POST'])
def offline_product(product_id: int):
    """下架商品（软删除，前台不再展示）"""
    with get_db_session():
        ok = product_dao.soft_delete_product(product_id)
    if not ok:
        return jsonify({'code': 404, 'message': '商品不存在或已下架', 'data': None}), 404
    return jsonify({'code': 200, 'message': 'ok', 'data': None})


@admin_bp.route('/orders', methods=['GET'])
def list_orders():
    """查询用户购买记录，支持分页和过滤"""
    page = int(request.args.get('page', 1))
    page_size = min(int(request.args.get('page_size', 20)), 100)
    user_id_str = request.args.get('user_id')
    status = request.args.get('status')

    user_id = int(user_id_str) if user_id_str else None

    orders, total = order_dao.list_orders(
        page=page,
        page_size=page_size,
        user_id=user_id,
        status=status,
    )
    return jsonify({'code': 200, 'message': 'ok', 'data': {
        'total': total,
        'page': page,
        'page_size': page_size,
        'items': [o.to_dict() for o in orders],
    }})


@admin_bp.route('/orders/<int:order_id>/refund', methods=['POST'])
def refund_order(order_id: int):
    """管理员退款：当前实现为内部标记退款（不调用第三方网关）"""
    order = order_dao.get_order_by_id(order_id=order_id)
    if not order:
        return jsonify({'code': 404, 'message': '订单不存在', 'data': None}), 404

    if order.status == 'refunded':
        return jsonify({'code': 200, 'message': 'ok', 'data': order.to_dict()})

    if order.status != 'paid':
        return jsonify({'code': 400, 'message': '仅支持对已支付订单退款', 'data': None}), 400

    with get_db_session():
        updated = order_dao.mark_order_refunded(out_trade_no=order.out_trade_no)
        if not updated:
            return jsonify({'code': 500, 'message': '退款标记失败，请重试', 'data': None}), 500

    latest = order_dao.get_order_by_id(order_id=order_id)
    return jsonify({'code': 200, 'message': 'ok', 'data': latest.to_dict() if latest else None})


@admin_bp.route('/upload/icon', methods=['POST'])
def upload_icon():
    """上传商品封面图到 OSS，返回公开访问链接"""
    config = current_app.config['APP_CONFIG']

    if not config.oss.enabled:
        return jsonify({'code': 400, 'message': 'OSS 未启用，请先在配置中开启', 'data': None}), 400

    file = request.files.get('file')
    if not file:
        return jsonify({'code': 400, 'message': '缺少 file 字段', 'data': None}), 400

    allowed_types = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
    if file.content_type not in allowed_types:
        return jsonify({'code': 400, 'message': '仅支持 jpg/png/gif/webp 格式', 'data': None}), 400

    try:
        url = oss_service.upload_image(config=config.oss, file_storage=file)
    except Exception as e:
        logger.exception("OSS 上传失败")
        return jsonify({'code': 500, 'message': f'上传失败: {e}', 'data': None}), 500

    return jsonify({'code': 200, 'message': 'ok', 'data': {'url': url}})
