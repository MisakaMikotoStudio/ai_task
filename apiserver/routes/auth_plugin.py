#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
认证模块
支持两种认证方式：
1. Token认证（Bearer token）- 用于 Web 前端
2. Secret认证（X-Client-Secret）- 用于客户端
"""

from functools import wraps
from flask import request, jsonify

from dao import session_dao, user_dao
from dao.user_dao import update_last_access, get_user_by_secret
from dao.heartbeat_dao import check_instance_uuid_valid
import logging

logger = logging.getLogger(__name__)


def secret_required(f):
    """
    Secret 秘钥认证装饰器
    通过请求头 X-Client-Secret 进行认证
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        trace_id = get_trace_id()
        request.trace_id = trace_id
        
        secret = request.headers.get('X-Client-Secret')
        if not secret:
            logger.error("请求缺少认证秘钥", extra={'trace_id': trace_id})
            return jsonify({"code": 401, "message": "缺少认证秘钥"}), 401
        
        try:
            user_info = get_user_by_secret(secret)
            if not user_info:
                logger.error("无效的秘钥", extra={'trace_id': trace_id})
                return jsonify({"code": 401, "message": "无效的秘钥"}), 401
        except Exception as e:
            logger.error(f"秘钥验证失败: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
            return jsonify({"code": 401, "message": "秘钥验证失败"}), 401
        
        request.user_info = user_info
        
        # 更新用户最近访问时间
        try:
            update_last_access(user_info.id)
        except Exception as e:
            logger.error(f"更新用户最近访问时间失败: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
        
        return f(*args, **kwargs)
    
    return decorated_function


def login_required(f):
    """需要认证的装饰器（支持 Token 和 Secret 两种方式）"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        trace_id = get_trace_id()
        request.trace_id = trace_id
        
        # 优先检查 Secret 认证
        secret = request.headers.get('X-Client-Secret')
        if secret:
            try:
                user_info = get_user_by_secret(secret)
                if user_info:
                    # 检查实例UUID是否一致（如果提供了的话）
                    instance_uuid = request.headers.get('X-Instance-UUID')
                    client_id_str = request.headers.get('X-Client-ID')
                    if instance_uuid and client_id_str:
                        try:
                            client_id = int(client_id_str)
                            if not check_instance_uuid_valid(user_info.id, client_id, instance_uuid):
                                logger.error(f"重复客户端实例: client_id={client_id}, instance_uuid={instance_uuid}", extra={'trace_id': trace_id})
                                return jsonify({"code": 409, "message": "重复客户端，请确认只有一个客户端实例在运行，或者等待一分钟后重试"}), 409
                        except ValueError:
                            pass  # client_id格式错误，忽略检查

                    request.user_info = user_info
                    # 更新用户最近访问时间
                    try:
                        update_last_access(user_info.id)
                    except Exception as e:
                        logger.error(f"更新用户最近访问时间失败: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
                    return f(*args, **kwargs)
                else:
                    logger.error("无效的秘钥", extra={'trace_id': trace_id})
                    return jsonify({"code": 401, "message": "无效的秘钥"}), 401
            except Exception as e:
                logger.error(f"秘钥验证失败: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
                return jsonify({"code": 401, "message": "秘钥验证失败"}), 401
        
        # 回退到 Token 认证
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            logger.error("请求缺少认证token", extra={'trace_id': trace_id})
            return jsonify({"code": 401, "message": "缺少认证token"}), 401
        
        if not auth_header.startswith('Bearer '):
            logger.error("Token格式错误", extra={'trace_id': trace_id})
            return jsonify({"code": 401, "message": "Token格式错误"}), 401
        
        token = auth_header.split(' ')[1]
        if not token:
            logger.error("认证token为空", extra={'trace_id': trace_id})
            return jsonify({"code": 401, "message": "缺少认证token"}), 401
        
        try:
            user_id = session_dao.get_session_by_token(token).user_id
            user_info = user_dao.get_user_by_id(user_id)
            if not user_info:
                logger.error(f"无效的Token: {token}", extra={'trace_id': trace_id})
                return jsonify({"code": 401, "message": "无效的认证信息"}), 401
        except Exception as e:
            logger.error(f"Token验证失败: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
            return jsonify({"code": 401, "message": "Token验证失败"}), 401
        
        request.user_info = user_info
        
        # 更新用户最近访问时间
        try:
            update_last_access(user_info.id)
        except Exception as e:
            logger.error(f"更新用户最近访问时间失败: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
        
        return f(*args, **kwargs)
    
    return decorated_function

def get_trace_id():
    # 检查trace_id是否已存在于请求上下文中
    if hasattr(request, 'trace_id') and request.trace_id:
        return request.trace_id

    # 尝试从请求头或URL参数中获取
    trace_id = request.headers.get('traceId')
    if not trace_id:
        # 如果没有 traceId，生成一个默认的，而不是抛出异常
        import uuid
        trace_id = f"auto-{uuid.uuid4()}"
        logger.warning(f"请求缺少 traceId，自动生成: {trace_id}")
    # 将trace_id附加到请求对象，以便后续使用
    request.trace_id = trace_id
    return trace_id