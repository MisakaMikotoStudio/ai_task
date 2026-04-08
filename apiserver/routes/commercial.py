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
from routes.auth_plugin import skip_auth
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
            user_id=user.id,
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


def _detect_device(user_agent: str) -> str:
    """根据 User-Agent 判断 PC 还是移动端"""
    ua = user_agent.lower()
    mobile_keywords = ('mobile', 'android', 'iphone', 'ipad', 'windows phone')
    if any(kw in ua for kw in mobile_keywords):
        return 'mobile'
    return 'pc'
