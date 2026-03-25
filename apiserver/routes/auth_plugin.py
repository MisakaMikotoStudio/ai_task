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
from dao.secret_dao import update_secret_last_used_at
from dao.user_dao import update_last_access
from service.user_service import get_user_by_secret
import logging

logger = logging.getLogger(__name__)

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
                    request.user_info = user_info
                    # 更新秘钥最近使用时间
                    try:
                        update_secret_last_used_at(secret)
                    except Exception as e:
                        logger.error(f"更新秘钥最近使用时间失败: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
                    return f(*args, **kwargs)
                else:
                    logger.error("无效的秘钥", extra={'trace_id': trace_id})
                    return jsonify({"code": 401, "message": "无效的秘钥"}), 401
            except Exception as e:
                logger.error(f"秘钥验证异常: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
                return jsonify({"code": 500, "message": "秘钥验证异常"}), 500
        
        # 回退到前后端通用 Token 认证
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
            logger.error(f"Token验证异常: {str(e)}", extra={'trace_id': trace_id}, exc_info=True)
            return jsonify({"code": 500, "message": "Token验证异常"}), 500
        
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