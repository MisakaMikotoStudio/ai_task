#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
模型管理路由（仅 Admin 可访问）
包含：虚拟秘钥管理、模型价格配置、使用量监控
"""

from flask import Blueprint, request, jsonify

from routes.auth_plugin import admin_required
from dao.gateway_dao import (
    list_virtual_keys, create_virtual_key, delete_virtual_key,
    list_model_prices, create_model_price, delete_model_price,
    get_usage_stats,
)

model_bp = Blueprint('model', __name__)

# 已知供应商 → 目标 API 基础地址
PROVIDER_TARGET_URLS = {
    'Anthropic': 'https://api.anthropic.com',
    'OpenAI': 'https://api.openai.com',
    'DeepSeek': 'https://api.deepseek.com',
    'Gemini': 'https://generativelanguage.googleapis.com',
    'Qwen': 'https://dashscope.aliyuncs.com',
}


# ──────────────────────────────────────────────────────────────────────────────
# 虚拟秘钥接口
# ──────────────────────────────────────────────────────────────────────────────

@model_bp.route('/virtual-keys', methods=['GET'])
@admin_required
def get_virtual_keys():
    """获取虚拟秘钥列表"""
    keys = list_virtual_keys()
    return jsonify({'code': 200, 'data': [k.to_dict() for k in keys]})


@model_bp.route('/virtual-keys', methods=['POST'])
@admin_required
def create_key():
    """
    创建虚拟秘钥

    Body:
        provider (str): 供应商名称（如 Anthropic）
        real_key (str): 真实 API Key
        daily_limit (float, optional): 单日限额 RMB，默认 -1（无限制）
        target_url (str, optional): 覆盖默认目标地址
    """
    data = request.get_json() or {}
    provider = (data.get('provider') or '').strip()
    real_key = (data.get('real_key') or '').strip()
    daily_limit = float(data.get('daily_limit', -1))

    if not provider:
        return jsonify({'code': 400, 'message': '供应商不能为空'}), 400
    if not real_key:
        return jsonify({'code': 400, 'message': '真实秘钥不能为空'}), 400

    target_url = (data.get('target_url') or '').strip()
    if not target_url:
        target_url = PROVIDER_TARGET_URLS.get(provider, '')
    if not target_url:
        return jsonify({'code': 400, 'message': f'未知供应商 "{provider}"，请通过 target_url 参数指定目标地址'}), 400

    try:
        vk = create_virtual_key(provider, real_key, target_url, daily_limit)
        return jsonify({'code': 201, 'message': '虚拟秘钥创建成功', 'data': vk.to_dict()}), 201
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500


@model_bp.route('/virtual-keys/<int:key_id>', methods=['DELETE'])
@admin_required
def delete_key(key_id):
    """删除虚拟秘钥（软删除）"""
    if not delete_virtual_key(key_id):
        return jsonify({'code': 404, 'message': '虚拟秘钥不存在'}), 404
    return jsonify({'code': 200, 'message': '删除成功'})


@model_bp.route('/providers', methods=['GET'])
@admin_required
def get_providers():
    """返回支持的供应商列表及其目标地址"""
    providers = [
        {'name': name, 'target_url': url}
        for name, url in PROVIDER_TARGET_URLS.items()
    ]
    return jsonify({'code': 200, 'data': providers})


# ──────────────────────────────────────────────────────────────────────────────
# 模型价格接口
# ──────────────────────────────────────────────────────────────────────────────

@model_bp.route('/prices', methods=['GET'])
@admin_required
def get_prices():
    """获取模型价格列表"""
    prices = list_model_prices()
    return jsonify({'code': 200, 'data': [p.to_dict() for p in prices]})


@model_bp.route('/prices', methods=['POST'])
@admin_required
def create_price():
    """
    新增模型价格配置

    Body:
        provider (str): 供应商
        model_name (str): 模型名称（支持前缀匹配）
        input_price_per_million (float): 输入 Token 单价（RMB/百万）
        output_price_per_million (float): 输出 Token 单价（RMB/百万）
    """
    data = request.get_json() or {}
    provider = (data.get('provider') or '').strip()
    model_name = (data.get('model_name') or '').strip()
    input_price = float(data.get('input_price_per_million', 0))
    output_price = float(data.get('output_price_per_million', 0))

    if not provider:
        return jsonify({'code': 400, 'message': '供应商不能为空'}), 400
    if not model_name:
        return jsonify({'code': 400, 'message': '模型名称不能为空'}), 400

    try:
        price = create_model_price(provider, model_name, input_price, output_price)
        return jsonify({'code': 201, 'message': '价格配置创建成功', 'data': price.to_dict()}), 201
    except Exception as e:
        return jsonify({'code': 500, 'message': str(e)}), 500


@model_bp.route('/prices/<int:price_id>', methods=['DELETE'])
@admin_required
def delete_price(price_id):
    """删除模型价格配置"""
    if not delete_model_price(price_id):
        return jsonify({'code': 404, 'message': '价格配置不存在'}), 404
    return jsonify({'code': 200, 'message': '删除成功'})


# ──────────────────────────────────────────────────────────────────────────────
# 监控接口
# ──────────────────────────────────────────────────────────────────────────────

@model_bp.route('/monitor', methods=['GET'])
@admin_required
def get_monitor():
    """
    获取使用量统计数据

    Query Params:
        days (int, optional): 查询最近 N 天，默认 30
    """
    try:
        days = int(request.args.get('days', 30))
        days = max(1, min(days, 365))
    except ValueError:
        days = 30

    stats = get_usage_stats(days)
    return jsonify({'code': 200, 'data': stats})
