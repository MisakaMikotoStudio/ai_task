#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
云客户端相关路由
"""

from flask import Blueprint, request, jsonify

from dao.user_dao import get_user_by_secret
from dao.client_dao import get_cloud_clients_with_secret

cloud_bp = Blueprint('cloud', __name__)


@cloud_bp.route('/startup-config', methods=['GET'])
def get_cloud_startup_config():
    """
    云客户端启动配置接口

    验证方式：
        1. 请求头 X-Client-Secret 必须是有效秘钥
        2. 秘钥对应的用户名必须为 "admin"

    Response:
        成功 (200):
            {
                "code": 200,
                "data": [
                    {
                        "client_id": int,   # cloud 类型客户端ID
                        "secret": str       # 该客户端所属用户的云客户端专用秘钥
                    },
                    ...
                ]
            }
        未认证 (401):
            {"code": 401, "message": "错误信息"}
    """
    secret = request.headers.get('X-Client-Secret')
    if not secret:
        return jsonify({'code': 401, 'message': '缺少认证秘钥'}), 401

    user = get_user_by_secret(secret)
    if not user:
        return jsonify({'code': 401, 'message': '无效的秘钥'}), 401

    if user.name != 'admin':
        return jsonify({'code': 401, 'message': '无权限，仅 admin 用户可访问'}), 401

    result = get_cloud_clients_with_secret()
    return jsonify({'code': 200, 'data': result})
