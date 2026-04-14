#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
商业化路由
- GET  /api/commercial/products       - 商品列表（公开）
- POST /api/commercial/buy            - 生成支付宝支付链接（需登录）
- POST /api/commercial/alipay/notify  - 支付宝异步回调（无需登录，验签）
"""

import logging

from flask import Blueprint, request, jsonify, current_app

from dao import get_db_session
from dao import product_dao, order_dao
from dao.models import to_iso_utc
from routes.auth_plugin import skip_auth, skip_subscribe
from service import order_service, alipay_service

logger = logging.getLogger(__name__)
commercial_bp = Blueprint('commercial', __name__)


@commercial_bp.route('/products', methods=['GET'])
@skip_auth
def list_products():
    """查询商品列表（公开接口）"""
    products = product_dao.get_all_products()
    return jsonify({'code': 200, 'message': 'ok', 'data': [p.to_dict() for p in products]})


@commercial_bp.route('/buy', methods=['POST'])
@skip_subscribe
def buy():
    """生成支付宝支付链接"""
    user = request.user_info
    data = request.get_json(silent=True) or {}

    product_id = data.get('product_id')
    order_type = data.get('order_type', 'purchase')
    # device: pc / mobile，未传则根据 User-Agent 自动判断
    device = data.get('device') or _detect_device(request.headers.get('User-Agent', ''))

    if not product_id:
        return jsonify({'code': 400, 'message': '缺少 product_id', 'data': None}), 400

    if order_type not in ('purchase', 'renew'):
        return jsonify({'code': 400, 'message': 'order_type 必须是 purchase 或 renew', 'data': None}), 400

    product = product_dao.get_product_by_id(product_id=product_id)
    if not product:
        return jsonify({'code': 404, 'message': '商品不存在', 'data': None}), 404

    if order_type == 'renew' and not product.support_continue:
        return jsonify({'code': 400, 'message': '该商品不支持续费', 'data': None}), 400

    config = current_app.config['APP_CONFIG']

    with get_db_session():
        order = order_service.create_order(
            user_id=user.user_id,
            product=product,
            order_type=order_type,
        )

    try:
        pay_url = alipay_service.build_pay_url(
            config=config.alipay,
            product=product,
            order=order,
            device=device,
        )
    except Exception as e:
        logger.exception("生成支付链接失败")
        return jsonify({'code': 500, 'message': f'生成支付链接失败: {e}', 'data': None}), 500

    return jsonify({'code': 200, 'message': 'ok', 'data': {
        'pay_url': pay_url,
        'out_trade_no': order.out_trade_no,
    }})


@commercial_bp.route('/alipay/notify', methods=['POST'])
@skip_auth
def alipay_notify():
    """接收支付宝异步回调通知"""
    config = current_app.config['APP_CONFIG']

    # 支付宝 POST 参数可能是 form-data
    post_data = dict(request.form) if request.form else {}
    if not post_data and request.is_json:
        post_data = request.get_json(silent=True) or {}

    # 转换 list 值为单值（flask form.getlist 问题）
    post_data = {k: v[0] if isinstance(v, list) else v for k, v in post_data.items()}

    logger.info("支付宝回调: %s", post_data)

    # 验签
    if not alipay_service.verify_notify(config=config.alipay, post_data=post_data):
        logger.warning("支付宝回调验签失败")
        return 'fail', 400

    trade_status = post_data.get('trade_status', '')
    out_trade_no = post_data.get('out_trade_no', '')
    trade_no = post_data.get('trade_no', '')

    if trade_status in ('TRADE_SUCCESS', 'TRADE_FINISHED'):
        with get_db_session():
            order_service.confirm_paid(out_trade_no=out_trade_no, trade_no=trade_no)

    # 支付宝要求返回字符串 "success"
    return 'success', 200


@commercial_bp.route('/my-orders', methods=['GET'])
def my_orders():
    """查询当前登录用户的订单列表（分页）"""
    user = request.user_info
    try:
        page = max(1, int(request.args.get('page', 1)))
        page_size = min(max(1, int(request.args.get('page_size', 20))), 50)
    except (ValueError, TypeError):
        return jsonify({'code': 400, 'message': '分页参数无效', 'data': None}), 400

    orders, total = order_dao.list_orders(user_id=user.id, page=page, page_size=page_size)

    # 批量查商品名称（按 key 去重避免重复查询）
    product_keys = list({o.product_key for o in orders})
    products_map = {}
    for key in product_keys:
        product = product_dao.get_product_by_key(key=key)
        if product:
            products_map[key] = product.title

    order_list = []
    for order in orders:
        od = order.to_dict()
        od['product_title'] = products_map.get(order.product_key, order.product_key)
        order_list.append(od)

    logger.info("用户订单查询: user_id=%s, page=%s, total=%s", user.id, page, total)
    return jsonify({'code': 200, 'message': 'ok', 'data': {
        'orders': order_list,
        'total': total,
        'page': page,
        'page_size': page_size,
    }})


@commercial_bp.route('/my-services', methods=['GET'])
def my_services():
    """查询当前登录用户正在生效的服务（已支付且未过期或永久）"""
    user = request.user_info

    active_orders = order_dao.get_user_active_orders(user_id=user.id)

    # 按商品 key 去重，保留每个商品到期时间最晚（expire_at 最大）的那条
    seen_keys: dict = {}
    for order in active_orders:
        key = order.product_key
        if key not in seen_keys:
            seen_keys[key] = order

    services = []
    for key, order in seen_keys.items():
        product = product_dao.get_product_by_key(key=key)
        services.append({
            'product_key': key,
            'product_title': product.title if product else key,
            'product_icon': product.icon if product else None,
            'order_id': order.id,
            'expire_at': to_iso_utc(order.expire_at),
            'is_permanent': order.expire_at is None,
        })

    logger.info("用户服务查询: user_id=%s, active_count=%s", user.id, len(services))
    return jsonify({'code': 200, 'message': 'ok', 'data': services})


def _detect_device(user_agent: str) -> str:
    """根据 User-Agent 判断 PC 还是移动端"""
    ua = user_agent.lower()
    mobile_keywords = ('mobile', 'android', 'iphone', 'ipad', 'windows phone')
    if any(kw in ua for kw in mobile_keywords):
        return 'mobile'
    return 'pc'
