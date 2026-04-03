#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
认证模块
支持两种认证方式：
1. Token认证（Bearer token）- 用于 Web 前端
2. Secret认证（X-Client-Secret）- 用于客户端
"""

import json

from flask import request, jsonify, current_app

from dao import session_dao, user_dao
from dao.secret_dao import update_secret_last_used_at
from dao.user_dao import update_last_access
from service.user_service import get_user_by_secret
import logging

logger = logging.getLogger(__name__)

def skip_auth(f):
    """标记接口跳过全局鉴权"""
    setattr(f, '_skip_auth', True)
    return f


def _is_skip_auth_endpoint() -> bool:
    """当前请求对应的endpoint是否显式跳过鉴权"""
    endpoint = request.endpoint
    if not endpoint:
        return False
    view_func = current_app.view_functions.get(endpoint)
    return bool(view_func and getattr(view_func, '_skip_auth', False))


def _request_body_for_log():
    """提取用于日志的请求体/参数摘要（不记录文件内容）。"""
    if request.method in ('GET', 'HEAD', 'OPTIONS', 'TRACE'):
        args = dict(request.args)
        return args if args else None
    ct = (request.content_type or '') or ''
    if 'multipart/form-data' in ct:
        fields = request.form.to_dict()
        return {'_multipart': True, 'fields': fields} if fields else {'_multipart': True}
    if request.is_json:
        return request.get_json(silent=True)
    if request.form:
        return request.form.to_dict()
    raw = request.get_data(cache=True)
    if not raw:
        return None
    try:
        text = raw.decode('utf-8', errors='replace')
    except Exception:
        return f'<binary {len(raw)} bytes>'
    if len(text) > 4096:
        return text[:4096] + '...(truncated)'
    try:
        return json.loads(text)
    except Exception:
        return text


def _do_auth_check():
    """执行鉴权逻辑（支持 Token 和 Secret 两种方式）"""
    trace_id = get_trace_id()
    request.trace_id = trace_id

    try:
        body = _request_body_for_log()
        body_repr = json.dumps(body, ensure_ascii=False, default=str) if body is not None else ''
    except Exception as e:
        body_repr = f'<body_log_error: {e}>'
    if len(body_repr) > 8192:
        body_repr = body_repr[:8192] + '...(truncated)'
    logger.info(
        '需要登录验证的请求 method=%s path=%s body=%s',
        request.method,
        request.path,
        body_repr,
        extra={'trace_id': trace_id},
    )

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
                return None
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
        user_session = session_dao.get_session_by_token(token)
        if not user_session:
            logger.error("Token无效或已过期", extra={'trace_id': trace_id})
            return jsonify({"code": 401, "message": "无效的认证信息"}), 401

        user_id = user_session.user_id
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

    return None


def register_global_auth(app, api_prefix: str = '/api'):
    """注册全局鉴权：默认所有 API 接口需要登录，可通过 @skip_auth 放行。"""
    @app.before_request
    def _global_auth_guard():
        # CORS 预检请求放行
        if request.method == 'OPTIONS':
            return None

        # 非 API 路径放行（例如静态资源）
        if not request.path.startswith(api_prefix):
            return None

        # 标记了跳过鉴权的 endpoint 放行
        if _is_skip_auth_endpoint():
            return None

        return _do_auth_check()

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