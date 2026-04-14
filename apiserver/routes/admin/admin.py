#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
管理后台路由（需要登录，并且用户身份必须为管理员）
- POST /api/admin/product              - 新增商品
- GET  /api/admin/products             - 商品列表（含已下架）
- POST /api/admin/product/<id>/offline - 商品下架（软删除）
- POST /api/admin/product/<id>/online  - 商品上架（恢复已下架商品）
- GET  /api/admin/orders               - 查询购买记录
- POST /api/admin/upload/icon          - 上传商品封面图到 OSS
- 管理秘钥/应用（admin 专用）：
  - GET/POST  /api/admin/secrets
  - DELETE     /api/admin/secrets/<secret_id>
  - GET/POST  /api/admin/clients
  - GET/PUT    /api/admin/clients/<client_id>
  - DELETE     /api/admin/clients/<client_id>
"""

import logging
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from flask import Blueprint, request, jsonify, current_app

from dao import get_db_session
from dao import product_dao, order_dao
from dao import permission_dao
from dao.secret_dao import get_user_secrets, create_user_secret, delete_user_secret
from dao.client_dao import get_clients_by_user, delete_client, check_client_name_exists
from dao.heartbeat_dao import get_heartbeats_by_user

from dao.models import PermissionConfig, Resource
from dao import resource_dao
from service import oss_service, order_service
from service.client_service import AVAILABLE_AGENTS, get_client_detail, save_client, ClientSaveError
from service.resource_mysql_service import ResourceMySQLError, list_databases, create_database_for_user

logger = logging.getLogger(__name__)
admin_bp = Blueprint('admin', __name__)

MAX_PRODUCT_EXPIRE_SECONDS = 10 ** 8
MAX_PRODUCT_DESC_LEN = 10000
MAX_PRODUCT_TITLE_LEN = 128
MAX_PRODUCT_KEY_LEN = 64
MAX_PRODUCT_ICON_BYTES = 10 * 1024 * 1024  # 与 Flask MAX_CONTENT_LENGTH、前端一致


def require_admin(f):
    """要求当前登录用户为管理员（name == 'admin'）。"""

    @wraps(f)
    def _wrapper(*args, **kwargs):
        user = getattr(request, 'user_info', None)
        if not user or getattr(user, 'name', None) != 'admin':
            return jsonify({'code': 403, 'message': '需要管理员权限', 'data': None}), 403
        return f(*args, **kwargs)

    return _wrapper


@admin_bp.route('/product', methods=['POST'])
@require_admin
def create_product():
    """新增商品"""
    data = request.get_json(silent=True) or {}

    key = (data.get('key') or '').strip()
    title = (data.get('title') or '').strip()
    desc = data.get('desc')
    if desc is None:
        desc = ''
    elif not isinstance(desc, str):
        return jsonify({'code': 400, 'message': 'desc 须为字符串', 'data': None}), 400
    price = data.get('price')
    expire_time = data.get('expire_time')   # 秒，可为 null（永久）
    support_continue = bool(data.get('support_continue', False))
    icon_raw = data.get('icon')
    if icon_raw is not None and not isinstance(icon_raw, str):
        return jsonify({'code': 400, 'message': 'icon 须为字符串 URL', 'data': None}), 400
    icon = (icon_raw or '').strip() or None

    if not key or not title or price is None:
        return jsonify({'code': 400, 'message': 'key、title、price 为必填项', 'data': None}), 400

    if len(key) > MAX_PRODUCT_KEY_LEN or not re.match(r'^[a-zA-Z0-9_-]+$', key):
        return jsonify({'code': 400, 'message': 'key 仅允许字母数字下划线与短横线，且长度不超过 64', 'data': None}), 400

    if len(title) > MAX_PRODUCT_TITLE_LEN:
        return jsonify({'code': 400, 'message': f'title 长度不能超过 {MAX_PRODUCT_TITLE_LEN}', 'data': None}), 400

    if len(desc) > MAX_PRODUCT_DESC_LEN:
        return jsonify({'code': 400, 'message': f'描述长度不能超过 {MAX_PRODUCT_DESC_LEN}', 'data': None}), 400

    try:
        price_dec = Decimal(str(price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return jsonify({'code': 400, 'message': 'price 须为数字，且最多保留两位小数', 'data': None}), 400

    if price_dec <= 0:
        return jsonify({'code': 400, 'message': 'price 必须大于 0', 'data': None}), 400

    price = float(price_dec)

    if expire_time is not None:
        try:
            expire_time = int(expire_time)
            if expire_time <= 0 or expire_time > MAX_PRODUCT_EXPIRE_SECONDS:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({
                'code': 400,
                'message': f'expire_time 须为 1～{MAX_PRODUCT_EXPIRE_SECONDS} 的正整数（秒）',
                'data': None,
            }), 400

    if icon:
        if len(icon) > 512:
            return jsonify({'code': 400, 'message': '封面链接过长', 'data': None}), 400
        if not (icon.startswith('https://') or icon.startswith('http://')):
            return jsonify({'code': 400, 'message': '封面须为 http(s) 链接', 'data': None}), 400

    # 检查 key 唯一
    existing = product_dao.get_product_by_key(key=key)
    if existing:
        return jsonify({'code': 409, 'message': f'商品 key "{key}" 已存在', 'data': None}), 409

    with get_db_session():
        product = product_dao.create_product(
            key=key,
            title=title,
            desc=desc,
            price=price,
            expire_time=expire_time,
            support_continue=support_continue,
            icon=icon,
        )

    return jsonify({'code': 200, 'message': 'ok', 'data': product.to_dict()})


@admin_bp.route('/products', methods=['GET'])
@require_admin
def list_products_admin():
    """管理端商品列表（含已下架）"""
    products = product_dao.list_all_products_admin()
    data = []
    for p in products:
        d = p.to_dict()
        d['offline'] = p.deleted_at is not None
        data.append(d)
    return jsonify({'code': 200, 'message': 'ok', 'data': data})


@admin_bp.route('/product/<int:product_id>/offline', methods=['POST'])
@require_admin
def offline_product(product_id: int):
    """下架商品（软删除，前台不再展示）"""
    with get_db_session():
        ok = product_dao.soft_delete_product(product_id=product_id)
    if not ok:
        return jsonify({'code': 404, 'message': '商品不存在或已下架', 'data': None}), 404
    return jsonify({'code': 200, 'message': 'ok', 'data': None})


@admin_bp.route('/product/<int:product_id>/online', methods=['POST'])
@require_admin
def online_product(product_id: int):
    """上架商品（恢复已下架商品，前台重新展示）"""
    with get_db_session():
        ok = product_dao.restore_product(product_id=product_id)
    if not ok:
        return jsonify({'code': 404, 'message': '商品不存在或已在上架中', 'data': None}), 404
    logger.info("商品上架: product_id=%s", product_id)
    return jsonify({'code': 200, 'message': 'ok', 'data': None})


@admin_bp.route('/orders', methods=['GET'])
@require_admin
def list_orders():
    """查询用户购买记录，支持分页和过滤"""
    page = int(request.args.get('page', 1))
    page_size = min(int(request.args.get('page_size', 20)), 100)
    user_id_str = request.args.get('user_id')
    status = request.args.get('status')

    user_id = None
    if user_id_str is not None and str(user_id_str).strip() != '':
        s = str(user_id_str).strip()
        if not s.isdigit() or len(s) != 6 or s[0] == '0':
            return jsonify({'code': 400, 'message': 'user_id 须为 6 位数字且首位不能为 0', 'data': None}), 400
        user_id = int(s)

    orders, total = order_dao.list_orders(
        page=page,
        page_size=page_size,
        user_id=user_id,
        status=status,
    )
    return jsonify({'code': 200, 'message': 'ok', 'data': {
        'total': total,
        'page': page,
        'page_size': page_size,
        'items': [o.to_dict() for o in orders],
    }})


@admin_bp.route('/orders/<int:order_id>/refund', methods=['POST'])
@require_admin
def refund_order(order_id: int):
    """管理员退款：调用支付宝退款接口，成功后更新本地订单状态"""
    order = order_dao.get_order_by_id(order_id=order_id)
    if not order:
        return jsonify({'code': 404, 'message': '订单不存在', 'data': None}), 404

    if order.status == 'refunded':
        return jsonify({'code': 200, 'message': 'ok', 'data': order.to_dict()})

    if order.status != 'paid':
        return jsonify({'code': 400, 'message': '仅支持对已支付订单退款', 'data': None}), 400

    config = current_app.config['APP_CONFIG']

    with get_db_session():
        try:
            latest = order_service.refund_order(
                out_trade_no=order.out_trade_no,
                alipay_config=config.alipay,
            )
        except ValueError as e:
            return jsonify({'code': 400, 'message': str(e), 'data': None}), 400
        except RuntimeError as e:
            logger.exception("退款失败: order_id=%s", order_id)
            return jsonify({'code': 502, 'message': f'第三方退款失败: {e}', 'data': None}), 502
        except Exception as e:
            logger.exception("退款异常: order_id=%s", order_id)
            return jsonify({'code': 500, 'message': f'退款异常: {e}', 'data': None}), 500

    return jsonify({'code': 200, 'message': 'ok', 'data': latest.to_dict() if latest else None})


@admin_bp.route('/upload/icon', methods=['POST'])
@require_admin
def upload_icon():
    """上传商品封面图到 OSS，返回公开访问链接"""
    config = current_app.config['APP_CONFIG']

    file = request.files.get('file')
    if not file:
        return jsonify({'code': 400, 'message': '缺少 file 字段', 'data': None}), 400

    allowed_types = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
    if file.content_type not in allowed_types:
        return jsonify({'code': 400, 'message': '仅支持 jpg/png/gif/webp 格式', 'data': None}), 400

    try:
        file.seek(0, os.SEEK_END)
        icon_size = file.tell()
        file.seek(0)
    except OSError:
        icon_size = None
    if icon_size is not None and icon_size > MAX_PRODUCT_ICON_BYTES:
        return jsonify({'code': 400, 'message': '图片大小不能超过 10MB', 'data': None}), 400

    try:
        url = oss_service.upload_image(config=config.oss, file_storage=file)
    except Exception as e:
        logger.exception("OSS 上传失败")
        return jsonify({'code': 500, 'message': f'上传失败: {e}', 'data': None}), 500

    return jsonify({'code': 200, 'message': 'ok', 'data': {'url': url}})


# ========== 管理秘钥（admin 专用） ==========


@admin_bp.route('/secrets', methods=['GET'])
@require_admin
def admin_list_secrets():
    secrets_list = get_user_secrets(user_id=request.user_info.user_id)
    return jsonify({'code': 200, 'data': [s.to_dict() for s in secrets_list]})


@admin_bp.route('/secrets', methods=['POST'])
@require_admin
def admin_create_secret():
    data = request.get_json() or {}
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'code': 400, 'message': '秘钥名称不能为空', 'data': None}), 400
    if len(name) > 64:
        return jsonify({'code': 400, 'message': '秘钥名称长度不能超过64个字符', 'data': None}), 400

    user_secret = create_user_secret(user_id=request.user_info.user_id, name=name)
    return jsonify({'code': 201, 'message': '秘钥创建成功', 'data': user_secret.to_dict()}), 201


@admin_bp.route('/secrets/<int:secret_id>', methods=['DELETE'])
@require_admin
def admin_delete_secret(secret_id: int):
    if not delete_user_secret(secret_id=secret_id, user_id=request.user_info.user_id):
        return jsonify({'code': 404, 'message': '秘钥不存在', 'data': None}), 404

    return jsonify({'code': 200, 'message': '秘钥删除成功', 'data': None})


# ========== 管理应用（admin 专用） ==========


@admin_bp.route('/clients/agents', methods=['GET'])
@require_admin
def admin_get_available_agents():
    return jsonify({'code': 200, 'data': AVAILABLE_AGENTS})


@admin_bp.route('/clients', methods=['GET'])
@require_admin
def admin_list_clients():
    result = get_clients_by_user(user_id=request.user_info.user_id)

    # 合并心跳时间（与 /api/client 一致，避免前端二次请求）
    heartbeats = get_heartbeats_by_user(user_id=request.user_info.user_id)
    heartbeat_map = {hb.get('client_id'): hb.get('last_sync_at') for hb in heartbeats}
    for client in result:
        if client.get('id') in heartbeat_map:
            client['last_sync_at'] = heartbeat_map[client.get('id')]

    return jsonify({'code': 200, 'message': '获取客户端列表成功', 'data': result})


@admin_bp.route('/clients', methods=['POST'])
@require_admin
def admin_create_client():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空', 'data': None}), 400

    try:
        client_id = save_client(
            user_id=request.user_info.user_id,
            data=data,
            client_id=None,
        )
        response_data = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
        if not response_data:
            return jsonify({'code': 500, 'message': '客户端保存成功但读取详情失败', 'data': None}), 500
        return jsonify({'code': 201, 'message': '客户端创建成功', 'data': response_data}), 201
    except ClientSaveError as e:
        return jsonify({'code': 400, 'message': e.message, 'data': None}), 400
    except Exception as e:
        logger.exception('admin_create_client failed')
        return jsonify({'code': 500, 'message': str(e), 'data': None}), 500


@admin_bp.route('/clients/<int:client_id>', methods=['GET'])
@require_admin
def admin_get_client_detail(client_id: int):
    payload = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
    if not payload:
        return jsonify({'code': 400, 'message': '客户端不存在', 'data': None}), 400
    return jsonify({'code': 200, 'message': '获取客户端详情成功', 'data': payload})


@admin_bp.route('/clients/<int:client_id>', methods=['PUT'])
@require_admin
def admin_update_client(client_id: int):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空', 'data': None}), 400

    try:
        save_client(
            user_id=request.user_info.user_id,
            data=data,
            client_id=client_id,
        )
    except ClientSaveError as e:
        return jsonify({'code': 400, 'message': e.message, 'data': None}), 400
    except Exception as e:
        logger.exception('admin_update_client failed')
        return jsonify({'code': 500, 'message': str(e), 'data': None}), 500

    response_data = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
    if not response_data:
        return jsonify({'code': 500, 'message': '客户端更新成功但读取详情失败', 'data': None}), 500

    return jsonify({'code': 200, 'message': '客户端更新成功', 'data': response_data})


@admin_bp.route('/clients/<int:client_id>', methods=['DELETE'])
@require_admin
def admin_delete_client(client_id: int):
    if not delete_client(client_id, request.user_info.user_id):
        return jsonify({'code': 404, 'message': '客户端不存在', 'data': None}), 404

    return jsonify({'code': 200, 'message': '客户端删除成功', 'data': None})


@admin_bp.route('/clients/<int:client_id>/copy', methods=['POST'])
@require_admin
def admin_copy_client(client_id: int):
    """复制客户端（admin 专用）"""
    source_detail = get_client_detail(client_id=client_id, user_id=request.user_info.user_id)
    if not source_detail:
        return jsonify({'code': 400, 'message': '客户端不存在', 'data': None}), 400

    source_name = source_detail['name']
    suffix_plain = '_副本'
    copy_name = source_name[:16 - len(suffix_plain)] + suffix_plain

    import secrets as _secrets
    import string as _string

    retries = 0
    while check_client_name_exists(request.user_info.user_id, copy_name):
        if retries >= 3:
            return jsonify({'code': 400, 'message': '副本名称生成失败，请手动重命名后重试', 'data': None}), 400
        rand = ''.join(_secrets.choice(_string.ascii_letters + _string.digits) for _ in range(4))
        suffix = suffix_plain + rand
        copy_name = source_name[:16 - len(suffix)] + suffix
        retries += 1

    source_detail.pop('id', None)
    cid = save_client(user_id=request.user_info.user_id, data=source_detail, client_id=None)
    payload = get_client_detail(client_id=cid, user_id=request.user_info.user_id)

    return jsonify({'code': 201, 'message': '客户端复制成功', 'data': payload}), 201


# ========== 权限配置管理（admin 专用） ==========

MAX_PERMISSION_KEY_LEN = 64
MAX_PERMISSION_PRODUCT_KEY_LEN = 64


@admin_bp.route('/permissions', methods=['GET'])
@require_admin
def list_permissions():
    """获取所有权限配置，按 key 分组返回三层结构"""
    configs = permission_dao.get_all_configs()
    # 按 key 分组
    grouped = {}
    for c in configs:
        if c.key not in grouped:
            grouped[c.key] = {
                'key': c.key,
                'type': c.type,
                'products': [],
            }
        grouped[c.key]['products'].append({
            'id': c.id,
            'product_key': c.product_key,
            'config_detail': c.config_detail or {},
            'created_at': c.to_dict()['created_at'],
            'updated_at': c.to_dict()['updated_at'],
        })
    return jsonify({'code': 200, 'message': 'ok', 'data': list(grouped.values())})


@admin_bp.route('/permission', methods=['POST'])
@require_admin
def create_permission():
    """新增权限配置"""
    data = request.get_json(silent=True) or {}

    key = (data.get('key') or '').strip()
    perm_type = (data.get('type') or '').strip()
    product_key = (data.get('product_key') or '').strip()
    config_detail = data.get('config_detail')

    if not key or not perm_type or not product_key:
        return jsonify({'code': 400, 'message': 'key、type、product_key 为必填项', 'data': None}), 400

    if len(key) > MAX_PERMISSION_KEY_LEN or not re.match(r'^[a-zA-Z0-9_-]+$', key):
        return jsonify({'code': 400, 'message': 'key 仅允许字母数字下划线与短横线，且长度不超过 64', 'data': None}), 400

    if perm_type not in PermissionConfig.VALID_TYPES:
        return jsonify({
            'code': 400,
            'message': f'type 仅支持: {", ".join(PermissionConfig.VALID_TYPES)}',
            'data': None,
        }), 400

    if len(product_key) > MAX_PERMISSION_PRODUCT_KEY_LEN or not re.match(r'^[a-zA-Z0-9_-]+$', product_key):
        return jsonify({'code': 400, 'message': 'product_key 仅允许字母数字下划线与短横线，且长度不超过 64', 'data': None}), 400

    # count_limit 类型必须有 limit 字段
    if perm_type == PermissionConfig.TYPE_COUNT_LIMIT:
        if not config_detail or not isinstance(config_detail, dict) or 'limit' not in config_detail:
            return jsonify({'code': 400, 'message': 'count_limit 类型必须在 config_detail 中包含 limit 字段', 'data': None}), 400
        limit_val = config_detail.get('limit')
        if not isinstance(limit_val, int) or limit_val <= 0:
            return jsonify({'code': 400, 'message': 'limit 必须为正整数', 'data': None}), 400

    # 检查重复
    if permission_dao.check_duplicate(key=key, product_key=product_key):
        return jsonify({'code': 409, 'message': f'权限配置 key="{key}" + product_key="{product_key}" 已存在', 'data': None}), 409

    with get_db_session():
        config = permission_dao.create_config(
            key=key,
            type=perm_type,
            product_key=product_key,
            config_detail=config_detail,
        )

    return jsonify({'code': 200, 'message': 'ok', 'data': config.to_dict()})


@admin_bp.route('/permission/<int:config_id>', methods=['PUT'])
@require_admin
def update_permission(config_id: int):
    """更新权限配置"""
    data = request.get_json(silent=True) or {}

    key = (data.get('key') or '').strip()
    perm_type = (data.get('type') or '').strip()
    product_key = (data.get('product_key') or '').strip()
    config_detail = data.get('config_detail')

    if not key or not perm_type or not product_key:
        return jsonify({'code': 400, 'message': 'key、type、product_key 为必填项', 'data': None}), 400

    if len(key) > MAX_PERMISSION_KEY_LEN or not re.match(r'^[a-zA-Z0-9_-]+$', key):
        return jsonify({'code': 400, 'message': 'key 仅允许字母数字下划线与短横线，且长度不超过 64', 'data': None}), 400

    if perm_type not in PermissionConfig.VALID_TYPES:
        return jsonify({
            'code': 400,
            'message': f'type 仅支持: {", ".join(PermissionConfig.VALID_TYPES)}',
            'data': None,
        }), 400

    if len(product_key) > MAX_PERMISSION_PRODUCT_KEY_LEN or not re.match(r'^[a-zA-Z0-9_-]+$', product_key):
        return jsonify({'code': 400, 'message': 'product_key 仅允许字母数字下划线与短横线，且长度不超过 64', 'data': None}), 400

    if perm_type == PermissionConfig.TYPE_COUNT_LIMIT:
        if not config_detail or not isinstance(config_detail, dict) or 'limit' not in config_detail:
            return jsonify({'code': 400, 'message': 'count_limit 类型必须在 config_detail 中包含 limit 字段', 'data': None}), 400
        limit_val = config_detail.get('limit')
        if not isinstance(limit_val, int) or limit_val <= 0:
            return jsonify({'code': 400, 'message': 'limit 必须为正整数', 'data': None}), 400

    # 检查重复（排除自身）
    if permission_dao.check_duplicate(key=key, product_key=product_key, exclude_id=config_id):
        return jsonify({'code': 409, 'message': f'权限配置 key="{key}" + product_key="{product_key}" 已存在', 'data': None}), 409

    with get_db_session():
        config = permission_dao.update_config(
            config_id=config_id,
            key=key,
            type=perm_type,
            product_key=product_key,
            config_detail=config_detail,
        )

    if not config:
        return jsonify({'code': 404, 'message': '权限配置不存在', 'data': None}), 404

    return jsonify({'code': 200, 'message': 'ok', 'data': config.to_dict()})


@admin_bp.route('/permission/<int:config_id>', methods=['DELETE'])
@require_admin
def delete_permission(config_id: int):
    """删除权限配置（软删除）"""
    with get_db_session():
        ok = permission_dao.soft_delete_config(config_id=config_id)
    if not ok:
        return jsonify({'code': 404, 'message': '权限配置不存在', 'data': None}), 404
    return jsonify({'code': 200, 'message': 'ok', 'data': None})


# ========== 资源管理（admin 专用） ==========


@admin_bp.route('/resources', methods=['GET'])
@require_admin
def list_resources_api():
    """获取资源列表（含已下架）"""
    resources = resource_dao.list_resources(include_offline=True)
    return jsonify({'code': 200, 'message': 'ok', 'data': [r.to_dict() for r in resources]})


@admin_bp.route('/resource', methods=['POST'])
@require_admin
def create_resource_api():
    """新增资源"""
    data = request.get_json(silent=True) or {}

    res_name = (data.get('name') or '').strip()
    res_type = (data.get('type') or '').strip()
    source = (data.get('source') or '').strip()
    envs = data.get('envs')
    extra = data.get('extra')

    if not res_name:
        return jsonify({'code': 400, 'message': '资源名称不能为空', 'data': None}), 400
    if len(res_name) > 64:
        return jsonify({'code': 400, 'message': '资源名称不能超过64个字符', 'data': None}), 400
    if resource_dao.check_resource_name_exists(name=res_name):
        return jsonify({'code': 400, 'message': f'资源名称 "{res_name}" 已被占用', 'data': None}), 400

    if not res_type:
        return jsonify({'code': 400, 'message': 'type 不能为空', 'data': None}), 400
    if res_type not in Resource.VALID_TYPES:
        return jsonify({'code': 400, 'message': f'type 仅支持: {", ".join(Resource.VALID_TYPES)}', 'data': None}), 400

    if not source:
        return jsonify({'code': 400, 'message': 'source 不能为空', 'data': None}), 400
    if source not in Resource.VALID_SOURCES:
        return jsonify({'code': 400, 'message': f'source 仅支持: {", ".join(Resource.VALID_SOURCES)}', 'data': None}), 400

    if not envs or not isinstance(envs, list):
        return jsonify({'code': 400, 'message': 'envs 必须是非空数组', 'data': None}), 400
    for env in envs:
        if env not in Resource.VALID_ENVS:
            return jsonify({'code': 400, 'message': f'envs 中包含无效环境: {env}，仅支持: {", ".join(Resource.VALID_ENVS)}', 'data': None}), 400

    # 校验 extra 字段（不同 type+source 组合要求不同字段）
    if not extra or not isinstance(extra, dict):
        return jsonify({'code': 400, 'message': 'extra 不能为空', 'data': None}), 400

    err = _validate_resource_extra(res_type=res_type, source=source, extra=extra)
    if err:
        return jsonify({'code': 400, 'message': err, 'data': None}), 400

    with get_db_session():
        resource = resource_dao.create_resource(
            name=res_name,
            type=res_type,
            source=source,
            envs=envs,
            extra=extra,
        )

    return jsonify({'code': 200, 'message': 'ok', 'data': resource.to_dict()})


@admin_bp.route('/resource/<int:resource_id>', methods=['PUT'])
@require_admin
def update_resource_api(resource_id: int):
    """更新资源"""
    data = request.get_json(silent=True) or {}

    res_name = data.get('name')
    res_type = (data.get('type') or '').strip()
    source = (data.get('source') or '').strip()
    envs = data.get('envs')
    extra = data.get('extra')

    # name 校验（仅在提供了 name 时）
    update_name = None
    if res_name is not None:
        res_name = res_name.strip()
        if not res_name:
            return jsonify({'code': 400, 'message': '资源名称不能为空', 'data': None}), 400
        if len(res_name) > 64:
            return jsonify({'code': 400, 'message': '资源名称不能超过64个字符', 'data': None}), 400
        if resource_dao.check_resource_name_exists(name=res_name, exclude_id=resource_id):
            return jsonify({'code': 400, 'message': f'资源名称 "{res_name}" 已被占用', 'data': None}), 400
        update_name = res_name

    if res_type and res_type not in Resource.VALID_TYPES:
        return jsonify({'code': 400, 'message': f'type 仅支持: {", ".join(Resource.VALID_TYPES)}', 'data': None}), 400
    if source and source not in Resource.VALID_SOURCES:
        return jsonify({'code': 400, 'message': f'source 仅支持: {", ".join(Resource.VALID_SOURCES)}', 'data': None}), 400
    if envs is not None:
        if not isinstance(envs, list) or not envs:
            return jsonify({'code': 400, 'message': 'envs 必须是非空数组', 'data': None}), 400
        for env in envs:
            if env not in Resource.VALID_ENVS:
                return jsonify({'code': 400, 'message': f'envs 中包含无效环境: {env}', 'data': None}), 400

    if extra is not None:
        if not isinstance(extra, dict):
            return jsonify({'code': 400, 'message': 'extra 必须是对象', 'data': None}), 400
        # 获取当前资源确定 type/source
        existing = resource_dao.get_resource_by_id(resource_id=resource_id)
        if not existing:
            return jsonify({'code': 404, 'message': '资源不存在', 'data': None}), 404
        final_type = res_type or existing.type
        final_source = source or existing.source
        # 合并 extra：前端编辑时可能省略敏感字段（如 private_key、access_key_secret、login_password），
        # 需要保留原值而非覆盖为空
        merged_extra = dict(existing.get_raw_extra())
        merged_extra.update(extra)
        extra = merged_extra
        err = _validate_resource_extra(res_type=final_type, source=final_source, extra=extra)
        if err:
            return jsonify({'code': 400, 'message': err, 'data': None}), 400

    with get_db_session():
        resource = resource_dao.update_resource(
            resource_id=resource_id,
            name=update_name,
            type=res_type or None,
            source=source or None,
            envs=envs,
            extra=extra,
        )

    if not resource:
        return jsonify({'code': 404, 'message': '资源不存在', 'data': None}), 404

    return jsonify({'code': 200, 'message': 'ok', 'data': resource.to_dict()})


@admin_bp.route('/resource/<int:resource_id>/offline', methods=['POST'])
@require_admin
def offline_resource_api(resource_id: int):
    """下架资源"""
    with get_db_session():
        ok = resource_dao.offline_resource(resource_id=resource_id)
    if not ok:
        return jsonify({'code': 404, 'message': '资源不存在或已下架', 'data': None}), 404
    logger.info("资源下架: resource_id=%s", resource_id)
    return jsonify({'code': 200, 'message': 'ok', 'data': None})


@admin_bp.route('/resource/<int:resource_id>/online', methods=['POST'])
@require_admin
def online_resource_api(resource_id: int):
    """上架资源"""
    with get_db_session():
        ok = resource_dao.online_resource(resource_id=resource_id)
    if not ok:
        return jsonify({'code': 404, 'message': '资源不存在或已上架', 'data': None}), 404
    logger.info("资源上架: resource_id=%s", resource_id)
    return jsonify({'code': 200, 'message': 'ok', 'data': None})


@admin_bp.route('/resource/<int:resource_id>', methods=['DELETE'])
@require_admin
def delete_resource_api(resource_id: int):
    """删除资源（硬删除）"""
    with get_db_session():
        ok = resource_dao.delete_resource(resource_id=resource_id)
    if not ok:
        return jsonify({'code': 404, 'message': '资源不存在', 'data': None}), 404
    logger.info("资源删除: resource_id=%s", resource_id)
    return jsonify({'code': 200, 'message': 'ok', 'data': None})


@admin_bp.route('/resource/<int:resource_id>/databases', methods=['GET'])
@require_admin
def list_resource_databases_api(resource_id: int):
    """查询资源上的用户数据库（通过 user_id 过滤）"""
    user_id_param = request.args.get('user_id')
    if not user_id_param:
        return jsonify({'code': 400, 'message': 'user_id 参数必填', 'data': None}), 400

    try:
        target_user_id = int(user_id_param)
    except (TypeError, ValueError):
        return jsonify({'code': 400, 'message': 'user_id 须为整数', 'data': None}), 400

    resource = resource_dao.get_resource_by_id(resource_id=resource_id)
    if not resource:
        return jsonify({'code': 404, 'message': '资源不存在', 'data': None}), 404
    if resource.type != Resource.TYPE_MYSQL:
        return jsonify({'code': 400, 'message': '仅支持 mysql 类型资源', 'data': None}), 400

    try:
        dbs = list_databases(resource=resource, user_id=target_user_id)
        return jsonify({'code': 200, 'message': 'ok', 'data': dbs})
    except ResourceMySQLError as e:
        return jsonify({'code': 500, 'message': e.message, 'data': None}), 500


@admin_bp.route('/resource/<int:resource_id>/create-database', methods=['POST'])
@require_admin
def create_resource_database_api(resource_id: int):
    """在指定资源上为用户创建数据库 + 账号"""
    data = request.get_json(silent=True) or {}
    user_id_param = data.get('user_id')
    if not user_id_param:
        return jsonify({'code': 400, 'message': 'user_id 参数必填', 'data': None}), 400

    try:
        target_user_id = int(user_id_param)
    except (TypeError, ValueError):
        return jsonify({'code': 400, 'message': 'user_id 须为整数', 'data': None}), 400

    resource = resource_dao.get_resource_by_id(resource_id=resource_id)
    if not resource:
        return jsonify({'code': 404, 'message': '资源不存在', 'data': None}), 404
    if resource.type != Resource.TYPE_MYSQL:
        return jsonify({'code': 400, 'message': '仅支持 mysql 类型资源', 'data': None}), 400
    if resource.deleted_at is not None:
        return jsonify({'code': 400, 'message': '该资源已下架，无法创建数据库', 'data': None}), 400

    try:
        result = create_database_for_user(resource=resource, user_id=target_user_id)
        return jsonify({'code': 200, 'message': 'ok', 'data': result})
    except ResourceMySQLError as e:
        return jsonify({'code': 500, 'message': e.message, 'data': None}), 500


def _validate_resource_extra(res_type: str, source: str, extra: dict) -> str:
    """
    校验不同 type+source 组合的 extra 字段。

    Returns:
        错误信息，None 表示通过
    """
    if res_type == Resource.TYPE_MYSQL and source == Resource.SOURCE_ALIYUN:
        url = (extra.get('url') or '').strip()
        access_key_id = (extra.get('access_key_id') or '').strip()
        access_key_secret = (extra.get('access_key_secret') or '').strip()
        if not url:
            return '数据库实例地址 (url) 不能为空'
        if not access_key_id:
            return 'AccessKey ID 不能为空'
        if not access_key_secret:
            return 'AccessKey Secret 不能为空'
        return None

    if res_type == Resource.TYPE_CODE_REPO and source == Resource.SOURCE_GITHUB:
        organization = (extra.get('organization') or '').strip()
        app_id = (extra.get('app_id') or '').strip()
        private_key = (extra.get('private_key') or '').strip()
        if not organization:
            return 'GitHub Organization 不能为空'
        if not app_id:
            return 'GitHub App ID 不能为空'
        if not private_key:
            return 'GitHub App Private Key 不能为空'
        return None

    if res_type == Resource.TYPE_CLOUD_SERVER and source == Resource.SOURCE_TENCENT_CLOUD:
        login_user = (extra.get('login_user') or '').strip()
        login_password = (extra.get('login_password') or '').strip()
        server_ip = (extra.get('server_ip') or '').strip()
        if not login_user:
            return '登录账号不能为空'
        if not login_password:
            return '登录密码不能为空'
        if not server_ip:
            return '服务器IP不能为空'
        return None

    return f'暂不支持 type={res_type} + source={source} 的组合'
