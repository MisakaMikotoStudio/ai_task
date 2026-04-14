#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户相关路由（Open 第三方服务调用）
"""

from flask import Blueprint, request, jsonify

user_bp = Blueprint('open_user', __name__)


@user_bp.route('/me', methods=['GET'])
def get_current_user():
    """获取当前认证用户信息（供客户端验证 Secret 有效性）"""
    return jsonify({'code': 200, 'message': '获取当前用户信息成功', 'data': request.user_info.to_dict()})
