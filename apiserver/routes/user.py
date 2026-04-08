#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户相关路由
"""

from flask import Blueprint, request, jsonify

from routes.auth_plugin import skip_auth
from service.user_service import register_user, login_user
from dao.secret_dao import get_user_secrets, create_user_secret, delete_user_secret

user_bp = Blueprint('user', __name__)


# ========== 用户管理 ==========

@user_bp.route('/register', methods=['POST'])
@skip_auth
def register():
    """
    用户注册接口
    
    Request Body:
        {
            "name": str,           # 用户名
            "password_hash": str   # 前端SHA256哈希后的密码
        }
        
    Response:
        成功 (201):
            {"code": 201, "message": "注册成功", "data": {"user_id": int, "name": str, "token": str}}
        失败 (400):
            {"code": 400, "message": "错误信息"}
    """
    data = request.get_json()
    
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400
    
    user = register_user(data.get('name', ''), data.get('password_hash', ''))
    return jsonify({'code': 201, 'message': '注册成功', 'data': user.to_dict()}), 201


@user_bp.route('/login', methods=['POST'])
@skip_auth
def login():
    """
    用户登录接口
    
    Request Body:
        {
            "name": str,           # 用户名
            "password_hash": str   # 前端SHA256哈希后的密码
        }
        
    Response:
        成功 (200):
            {"code": 200, "message": "登录成功", "data": {"user_id": int, "name": str, "token": str}}
        失败 (400):
            {"code": 400, "message": "错误信息"}
    """
    data = request.get_json()
    
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400
    
    result = login_user(data.get('name', ''), data.get('password_hash', ''))
    return jsonify({
        'code': 200,
        'message': '登录成功',
        'data': result.to_dict()
    })


@user_bp.route('/me', methods=['GET'])
def get_current_user():
    """
    获取当前登录用户信息
    
    Headers:
        Authorization: Bearer <token>
        traceId: str  # 请求追踪ID
        
    Response:
        成功 (200):
            {"code": 200, "message": "获取当前用户信息成功", "data": {"user_id": int, "name": str, "created_at": str, "last_access_at": str}}
        失败 (400):
            {"code": 400, "message": "错误信息"}
        未认证 (401):
            {"code": 401, "message": "无效的认证信息"}
    """
    return jsonify({'code': 200, 'message': '获取当前用户信息成功', 'data': request.user_info.to_dict()})


# ========== 秘钥管理 ==========

@user_bp.route('/secrets', methods=['GET'])
def list_secrets():
    """获取当前用户秘钥列表"""
    secrets_list = get_user_secrets(user_id=request.user_info.user_id)
    return jsonify({
        'code': 200,
        'data': [s.to_dict() for s in secrets_list]
    })


@user_bp.route('/secrets', methods=['POST'])
def create_secret():
    """创建新秘钥（随机生成64位字符串）"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'code': 400, 'message': '秘钥名称不能为空'}), 400

    if len(name) > 64:
        return jsonify({'code': 400, 'message': '秘钥名称长度不能超过64个字符'}), 400

    user_secret = create_user_secret(user_id=request.user_info.user_id, name=name)
    return jsonify({
        'code': 201,
        'message': '秘钥创建成功',
        'data': user_secret.to_dict()
    }), 201


@user_bp.route('/secrets/<int:secret_id>', methods=['DELETE'])
def delete_secret(secret_id):
    """删除秘钥"""
    if not delete_user_secret(secret_id=secret_id, user_id=request.user_info.user_id):
        return jsonify({'code': 404, 'message': '秘钥不存在'}), 404

    return jsonify({'code': 200, 'message': '秘钥删除成功'})
