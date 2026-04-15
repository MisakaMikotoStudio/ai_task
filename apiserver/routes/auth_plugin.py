#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
认证模块 —— 按路由分类统一鉴权

三类路由各自的鉴权策略：
  /api/admin/*  — Token 登录验证 → 必须是 admin 用户
  /api/app/*    — Token 登录验证 → 写操作需订阅校验（@skip_subscribe 可跳过）
  /api/open/*   — Secret 秘钥验证

非 API 命名空间（见 register_global_auth 的 api_prefix）路径不再默认放行：须挂 @skip_auth 才能通过鉴权。

两个跳过注解：
  @skip_auth       — 跳过身份验证（无需确认用户身份）
  @skip_subscribe  — 跳过订阅验证（无需订阅商品即可写操作）
"""

import json
import logging

from flask import request, jsonify, current_app

from dao import session_dao, user_dao
from dao.secret_dao import update_secret_last_used_at
from dao.user_dao import update_last_access
from service.user_service import get_user_by_secret
from service import permission_service

logger = logging.getLogger(__name__)


# ===================== 跳过注解 =====================

def skip_auth(f):
    """标记接口跳过身份验证（无需确认用户身份）"""
    setattr(f, '_skip_auth', True)
    return f


def skip_subscribe(f):
    """标记接口跳过订阅验证（仍需身份鉴权）"""
    setattr(f, '_skip_subscribe', True)
    return f


# ===================== 内部工具 =====================

def _is_skip_auth_endpoint() -> bool:
    endpoint = request.endpoint
    if not endpoint:
        return False
    view_func = current_app.view_functions.get(endpoint)
    return bool(view_func and getattr(view_func, '_skip_auth', False))


def _is_skip_subscribe_endpoint() -> bool:
    endpoint = request.endpoint
    if not endpoint:
        return False
    view_func = current_app.view_functions.get(endpoint)
    return bool(view_func and getattr(view_func, '_skip_subscribe', False))


_SENSITIVE_LOG_KEYWORDS = (
    'password', 'token', 'secret', 'private_key', 'encrypt_key', 'access_key',
)


def _is_sensitive_key(k: str) -> bool:
    lower = k.lower()
    return any(kw in lower for kw in _SENSITIVE_LOG_KEYWORDS)


def _redact_sensitive(obj):
    if isinstance(obj, dict):
        return {
            k: ('***' if _is_sensitive_key(k) else _redact_sensitive(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_sensitive(item) for item in obj]
    return obj


def _request_body_for_log():
    if request.method in ('GET', 'HEAD', 'OPTIONS', 'TRACE'):
        args = dict(request.args)
        return _redact_sensitive(args) if args else None
    ct = (request.content_type or '') or ''
    if 'multipart/form-data' in ct:
        fields = request.form.to_dict()
        return _redact_sensitive({'_multipart': True, 'fields': fields}) if fields else {'_multipart': True}
    if request.is_json:
        body = request.get_json(silent=True)
        return _redact_sensitive(body) if body else None
    if request.form:
        return _redact_sensitive(request.form.to_dict())
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
        return _redact_sensitive(json.loads(text))
    except Exception:
        return text


def _log_request(trace_id: str):
    """记录请求日志"""
    try:
        body = _request_body_for_log()
        body_repr = json.dumps(body, ensure_ascii=False, default=str) if body is not None else ''
    except Exception as e:
        body_repr = f'<body_log_error: {e}>'
    if len(body_repr) > 8192:
        body_repr = body_repr[:8192] + '...(truncated)'
    logger.info(
        '需要登录验证的请求 traceId=%s method=%s path=%s body=%s',
        trace_id,
        request.method,
        request.path,
        body_repr,
        extra={'trace_id': trace_id},
    )


# ===================== Token 登录验证 =====================

def _authenticate_by_token(trace_id: str):
    """通过 Bearer Token 验证身份，成功后设置 request.user_info。
    返回 None 表示成功，否则返回错误 response。
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"code": 401, "message": "缺少认证token"}), 401

    if not auth_header.startswith('Bearer '):
        return jsonify({"code": 401, "message": "Token格式错误"}), 401

    token = auth_header.split(' ')[1]
    if not token:
        return jsonify({"code": 401, "message": "缺少认证token"}), 401

    try:
        user_session = session_dao.get_session_by_token(token)
        if not user_session:
            return jsonify({"code": 401, "message": "无效的认证信息"}), 401

        user_info = user_dao.get_user_by_id(user_session.user_id)
        if not user_info:
            return jsonify({"code": 401, "message": "无效的认证信息"}), 401
    except Exception as e:
        logger.error("Token验证异常: %s", e, extra={'trace_id': trace_id}, exc_info=True)
        return jsonify({"code": 500, "message": "认证服务异常"}), 500

    request.user_info = user_info

    try:
        update_last_access(user_info.id)
    except Exception as e:
        logger.error("更新用户最近访问时间失败: %s", e, extra={'trace_id': trace_id}, exc_info=True)

    return None


# ===================== Secret 秘钥验证 =====================

def _authenticate_by_secret(trace_id: str):
    """通过 X-Client-Secret 验证身份，成功后设置 request.user_info。
    返回 None 表示成功，否则返回错误 response。
    """
    secret = request.headers.get('X-Client-Secret')
    if not secret:
        return jsonify({"code": 401, "message": "缺少认证秘钥"}), 401

    try:
        user_info = get_user_by_secret(secret)
        if not user_info:
            return jsonify({"code": 401, "message": "无效的秘钥"}), 401
    except Exception as e:
        logger.error("秘钥验证异常: %s", e, extra={'trace_id': trace_id}, exc_info=True)
        return jsonify({"code": 500, "message": "认证服务异常"}), 500

    request.user_info = user_info

    try:
        update_secret_last_used_at(secret)
    except Exception as e:
        logger.error("更新秘钥最近使用时间失败: %s", e, extra={'trace_id': trace_id}, exc_info=True)

    return None


# ===================== 订阅校验 =====================

def _check_subscription(trace_id: str):
    """对非 GET/HEAD/OPTIONS 请求做订阅校验。
    返回 None 表示通过，否则返回 403 response。
    """
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None

    if _is_skip_subscribe_endpoint():
        return None

    user_info = getattr(request, 'user_info', None)
    if not user_info:
        return None

    try:
        result = permission_service.check(user_id=user_info.user_id, key='subscribed')
        if not result.passed:
            logger.warning(
                "订阅鉴权失败 user_id=%s path=%s method=%s",
                user_info.user_id, request.path, request.method,
                extra={'trace_id': trace_id},
            )
            return jsonify({
                'code': 403,
                'message': result.message,
                'data': result.to_response_data(),
            }), 403
    except Exception as e:
        logger.error(
            "订阅鉴权异常: %s", str(e),
            extra={'trace_id': trace_id},
            exc_info=True,
        )
    return None


# ===================== 各类路由鉴权入口 =====================

def _auth_admin(trace_id: str):
    """admin 路由：Token 登录 → 必须是 admin 用户"""
    err = _authenticate_by_token(trace_id)
    if err:
        return err

    user = getattr(request, 'user_info', None)
    if not user or getattr(user, 'name', None) != 'admin':
        return jsonify({'code': 403, 'message': '需要管理员权限', 'data': None}), 403

    return None


def _auth_app(trace_id: str):
    """app 路由：Token 登录 → 写操作订阅校验"""
    err = _authenticate_by_token(trace_id)
    if err:
        return err

    return _check_subscription(trace_id)


def _auth_open(trace_id: str):
    """open 路由：Secret 秘钥验证"""
    return _authenticate_by_secret(trace_id)


# ===================== 全局鉴权注册 =====================

def register_global_auth(app, api_prefix: str = '/api'):
    """注册全局鉴权 before_request 和响应 traceId 注入 after_request。"""

    @app.after_request
    def _inject_trace_id(response):
        trace_id = getattr(request, 'trace_id', None)
        if trace_id:
            response.headers['traceId'] = trace_id
        return response

    @app.before_request
    def _global_auth_guard():
        if request.method == 'OPTIONS':
            return None

        trace_id = _ensure_trace_id()
        if _is_skip_auth_endpoint():
            return None

        path = request.path
        if not path.startswith(api_prefix):
            logger.warning("访问未注册路径: %s", path, extra={'trace_id': trace_id})
            return jsonify({"code": 404, "message": "接口不存在"}), 404

        _log_request(trace_id)

        admin_prefix = f'{api_prefix}/admin'
        app_prefix = f'{api_prefix}/app'
        open_prefix = f'{api_prefix}/open'

        if path.startswith(admin_prefix):
            return _auth_admin(trace_id)
        elif path.startswith(app_prefix):
            return _auth_app(trace_id)
        elif path.startswith(open_prefix):
            return _auth_open(trace_id)
        else:
            # 未匹配的 /api 路径（如 /api/health 应通过 @skip_auth 放行）
            logger.warning("访问未注册API路径: %s", path, extra={'trace_id': trace_id})
            return jsonify({"code": 404, "message": "接口不存在"}), 404


def _ensure_trace_id() -> str:
    """获取或生成 traceId"""
    if hasattr(request, 'trace_id') and request.trace_id:
        return request.trace_id

    trace_id = request.headers.get('traceId')
    if not trace_id:
        import uuid
        trace_id = f"auto-{uuid.uuid4()}"
        logger.warning("请求缺少 traceId，自动生成: %s", trace_id)

    request.trace_id = trace_id
    return trace_id
