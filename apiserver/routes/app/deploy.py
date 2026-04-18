#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发布记录路由（Web 前端调用）
"""

import logging

from flask import Blueprint, request, jsonify

from dao.deploy_dao import (
    create_deploy_record,
    get_deploy_records_by_client,
    get_deploy_records_by_user,
    cancel_deploy_record,
    retry_deploy_record,
    get_latest_deploy_records_by_msg_ids,
    get_latest_deploy_record_by_msg_env,
    reset_deploy_record_to_pending,
    get_deploy_record_by_id,
)
from dao.client_dao import (
    get_client_by_id, get_client_domains,
    get_client_repos, get_client_deploys,
)
from dao.chat_dao import create_standalone_chat_message, batch_get_msg_by_msgids
from dao.models import DeployRecord, ChatMessage

logger = logging.getLogger(__name__)

deploy_bp = Blueprint('app_deploy', __name__)


@deploy_bp.route('/client/<int:client_id>/records', methods=['GET'])
def list_deploy_records(client_id):
    """获取指定客户端的发布记录列表（支持分页、状态/环境/msg_id 筛选）"""
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 20, type=int)
    status = request.args.get('status', '', type=str).strip() or None
    env = request.args.get('env', '', type=str).strip() or None
    msg_id_raw = request.args.get('msg_id', '', type=str).strip()
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20
    if status and status not in DeployRecord.VALID_STATUSES:
        return jsonify({'code': 400, 'message': f'无效的状态筛选，可选值: {", ".join(DeployRecord.VALID_STATUSES)}'}), 400
    if env and env not in DeployRecord.VALID_ENVS:
        return jsonify({'code': 400, 'message': f'无效的环境筛选，可选值: {", ".join(DeployRecord.VALID_ENVS)}'}), 400

    msg_id = None
    if msg_id_raw:
        try:
            msg_id = int(msg_id_raw)
        except ValueError:
            return jsonify({'code': 400, 'message': 'msg_id 必须为整数'}), 400
        if msg_id < 0:
            return jsonify({'code': 400, 'message': 'msg_id 不能为负数'}), 400

    result = get_deploy_records_by_client(
        user_id=user_id, client_id=client_id, status=status, env=env, msg_id=msg_id,
        page=page, page_size=page_size,
    )
    return jsonify({'code': 200, 'data': result})


@deploy_bp.route('/client/<int:client_id>/records/by-msgs', methods=['POST'])
def list_deploy_records_by_msgs(client_id):
    """批量查询指定 msg_id 列表下的最新发布记录，按 (msg_id, env) 聚合"""
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    data = request.get_json(silent=True) or {}
    msg_ids = data.get('msg_ids') or []
    if not isinstance(msg_ids, list):
        return jsonify({'code': 400, 'message': 'msg_ids 必须为数组'}), 400
    # 避免请求过大
    if len(msg_ids) > 500:
        return jsonify({'code': 400, 'message': 'msg_ids 数量不能超过 500'}), 400

    result = get_latest_deploy_records_by_msg_ids(user_id=user_id, client_id=client_id, msg_ids=msg_ids)
    return jsonify({'code': 200, 'data': result})


@deploy_bp.route('/records', methods=['GET'])
def list_all_deploy_records():
    """获取当前用户的发布记录列表（支持按应用、状态筛选 + 分页，附带应用名称）"""
    user_id = request.user_info.user_id

    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 20, type=int)
    status = request.args.get('status', '', type=str).strip() or None
    client_id = request.args.get('client_id', type=int)
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20
    if status and status not in DeployRecord.VALID_STATUSES:
        return jsonify({'code': 400, 'message': f'无效的状态筛选，可选值: {", ".join(DeployRecord.VALID_STATUSES)}'}), 400
    if client_id is not None:
        client = get_client_by_id(client_id, user_id)
        if not client:
            return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    result = get_deploy_records_by_user(user_id=user_id, client_id=client_id, status=status, page=page, page_size=page_size)
    return jsonify({'code': 200, 'data': result})


@deploy_bp.route('/client/<int:client_id>/records', methods=['POST'])
def create_deploy_record_api(client_id):
    """创建发布记录"""
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'code': 400, 'message': '请求数据为空'}), 400

    env = (data.get('env') or '').strip()
    if env not in DeployRecord.VALID_ENVS:
        return jsonify({'code': 400, 'message': f'无效的环境标识，可选值: {", ".join(DeployRecord.VALID_ENVS)}'}), 400

    description = (data.get('description') or '').strip()
    if not description:
        return jsonify({'code': 400, 'message': '发布描述不能为空'}), 400

    status = (data.get('status') or DeployRecord.STATUS_PENDING).strip()
    if status not in DeployRecord.VALID_STATUSES:
        return jsonify({'code': 400, 'message': f'无效的状态，可选值: {", ".join(DeployRecord.VALID_STATUSES)}'}), 400

    detail = data.get('detail') or {}
    if not isinstance(detail, dict):
        return jsonify({'code': 400, 'message': 'detail 必须是字典'}), 400

    def _parse_nonneg_int(val):
        if val is None or val == '':
            return 0
        try:
            n = int(val)
        except (TypeError, ValueError):
            return None
        if n < 0:
            return -1
        return n

    # msg_id / task_id / chat_id：可选，默认 0；兼容 body 顶层或 detail 内
    msg_id_raw = data.get('msg_id')
    if msg_id_raw is None:
        msg_id_raw = detail.get('msg_id')
    task_id_raw = data.get('task_id')
    if task_id_raw is None:
        task_id_raw = detail.get('task_id')
    chat_id_raw = data.get('chat_id')
    if chat_id_raw is None:
        chat_id_raw = detail.get('chat_id')

    msg_id = _parse_nonneg_int(msg_id_raw)
    if msg_id is None:
        return jsonify({'code': 400, 'message': 'msg_id 必须为整数'}), 400
    if msg_id < 0:
        return jsonify({'code': 400, 'message': 'msg_id 不能为负数'}), 400

    task_id = _parse_nonneg_int(task_id_raw)
    if task_id is None:
        return jsonify({'code': 400, 'message': 'task_id 必须为整数'}), 400
    if task_id < 0:
        return jsonify({'code': 400, 'message': 'task_id 不能为负数'}), 400

    chat_id = _parse_nonneg_int(chat_id_raw)
    if chat_id is None:
        return jsonify({'code': 400, 'message': 'chat_id 必须为整数'}), 400
    if chat_id < 0:
        return jsonify({'code': 400, 'message': 'chat_id 不能为负数'}), 400

    # 同步写入 detail（便于老前端只读 detail）
    if msg_id:
        detail['msg_id'] = msg_id
    if task_id:
        detail['task_id'] = task_id
    if chat_id:
        detail['chat_id'] = chat_id

    record_id = create_deploy_record(
        user_id=user_id, client_id=client_id, env=env, description=description,
        status=status, detail=detail, msg_id=msg_id, task_id=task_id, chat_id=chat_id,
    )
    return jsonify({'code': 201, 'message': '发布记录创建成功', 'data': {'id': record_id}}), 201


@deploy_bp.route('/records/<int:record_id>/cancel', methods=['PATCH'])
def cancel_deploy_record_api(record_id):
    """取消发布记录"""
    user_id = request.user_info.user_id
    success = cancel_deploy_record(user_id=user_id, record_id=record_id)
    if not success:
        return jsonify({'code': 400, 'message': '记录不存在、无权限或状态不允许取消'}), 400

    return jsonify({'code': 200, 'message': '发布记录已取消'})


@deploy_bp.route('/client/<int:client_id>/preview', methods=['POST'])
def preview_chat_message_api(client_id):
    """
    chat 消息预览：
    1. SSH 登录应用测试环境服务器，检查 docker 网络是否存在
       - 存在：返回 ready + 预览 URL `https://{host_key}.{test_domain}`
       - 不存在：查找/创建 test 环境发布记录，触发后台部署，返回 deploying

    host_key 格式：task{task_id}chat{chat_id}msg{msg_id}
    """
    from service.remote_deploy_service import check_test_docker_network_exists

    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    data = request.get_json(silent=True) or {}

    def _parse_nonneg(val):
        try:
            n = int(val)
        except (TypeError, ValueError):
            return None
        return n if n >= 0 else None

    task_id = _parse_nonneg(data.get('task_id'))
    chat_id = _parse_nonneg(data.get('chat_id'))
    msg_id = _parse_nonneg(data.get('msg_id'))
    if task_id is None or chat_id is None or msg_id is None:
        return jsonify({'code': 400, 'message': 'task_id/chat_id/msg_id 必须为非负整数'}), 400
    if msg_id <= 0 or chat_id <= 0:
        return jsonify({'code': 400, 'message': '缺少有效的 chat_id/msg_id'}), 400

    description = (data.get('description') or '').strip() or f'预览 chat{chat_id}msg{msg_id}'
    if len(description) > 255:
        description = description[:255]

    domains = [d.domain for d in get_client_domains(client_id=client_id, user_id=user_id, env='test')]
    domain_values = [d.strip() for d in domains if d and d.strip()]
    if not domain_values:
        return jsonify({'code': 400, 'message': '未配置应用测试环境域名'}), 400

    host_key = f'task{task_id}chat{chat_id}msg{msg_id}'
    preview_url = f'https://{host_key}.{domain_values[0]}'

    try:
        network_ready = check_test_docker_network_exists(
            client_id=client_id, user_id=user_id, host_key=host_key,
        )
    except Exception:
        logger.exception('check test docker network failed: client_id=%s host_key=%s', client_id, host_key)
        network_ready = False

    if network_ready:
        return jsonify({
            'code': 200,
            'data': {'status': 'ready', 'url': preview_url, 'host_key': host_key},
        })

    # docker 网络不存在：触发测试环境部署流程
    # - 无记录：新建一条 pending 记录
    # - 有记录且状态 ∈ {publishing, pending}：保持不动，提示"正在发布中"
    # - 有记录且状态 ∉ {publishing, pending}：复用原记录，重置为 pending
    existing = get_latest_deploy_record_by_msg_env(
        user_id=user_id, client_id=client_id, msg_id=msg_id, env='test',
    )
    if existing is None:
        record_id = create_deploy_record(
            user_id=user_id, client_id=client_id, env='test',
            description=description, status=DeployRecord.STATUS_PENDING,
            detail={'task_id': task_id, 'chat_id': chat_id, 'msg_id': msg_id, 'source': 'preview'},
            msg_id=msg_id, task_id=task_id, chat_id=chat_id,
        )
        action = 'created'
    elif existing.status in (DeployRecord.STATUS_PUBLISHING, DeployRecord.STATUS_PENDING):
        record_id = existing.id
        action = 'publishing'
    else:
        record_id = existing.id
        ok = reset_deploy_record_to_pending(user_id=user_id, record_id=record_id)
        action = 'reset' if ok else 'publishing'

    return jsonify({
        'code': 200,
        'data': {
            'status': 'deploying',
            'url': preview_url,
            'host_key': host_key,
            'record_id': record_id,
            'action': action,
            'message': '服务正在部署，请稍后5分钟查看',
        },
    })


@deploy_bp.route('/records/<int:record_id>/retry', methods=['PATCH'])
def retry_deploy_record_api(record_id):
    """重试发布记录（失败或取消状态），将状态重置为 pending"""
    user_id = request.user_info.user_id
    success = retry_deploy_record(user_id=user_id, record_id=record_id)
    if not success:
        return jsonify({'code': 400, 'message': '记录不存在、无权限或状态不允许重试'}), 400

    return jsonify({'code': 200, 'message': '发布记录已重置为等待发布'})


def _build_merge_request_for_client(client_id: int, user_id: int) -> list:
    """
    汇总当前应用下「配置了 deploy 命令」的仓库的最新 commit 信息，生成 merge_request 结构。

    仅选取被 ClientDeploy 引用的 repo（repo_id 非空），每条返回：
        {
          "repo_id": <仓库ID>,
          "merge_url": "",
          "repo_name": <仓库名>,
          "branch_name": <默认主分支，缺省 main>,
          "latest_commitId": <默认主分支最新 commit>
        }

    Raises:
        ValueError: 无法获取某个仓库的最新 commit（用于 400 提示）
    """
    from service.git_service import refresh_repo_token_by_url
    from utils.git_utils import parse_github_url, get_branch_latest_commit

    deploys = get_client_deploys(client_id=client_id, user_id=user_id)
    if not deploys:
        raise ValueError('当前应用未配置部署命令')
    referenced_repo_ids = {d.repo_id for d in deploys if d.repo_id}
    if not referenced_repo_ids:
        raise ValueError('当前应用的部署命令均未关联代码仓库')

    repos = get_client_repos(client_id=client_id, user_id=user_id)
    referenced_repos = [r for r in repos if r.id in referenced_repo_ids]
    if not referenced_repos:
        raise ValueError('未找到与部署命令关联的代码仓库')

    merge_request = []
    for repo in referenced_repos:
        url = repo.url or ''
        org, repo_name = parse_github_url(url=url)
        if not org or not repo_name:
            raise ValueError(f'仓库 URL 解析失败：{url}')
        branch = (repo.default_branch or 'main').strip() or 'main'
        try:
            token = refresh_repo_token_by_url(repo_url=url)
        except Exception as e:
            raise ValueError(f'刷新仓库 {repo_name} token 失败：{e}')
        try:
            commit_id = get_branch_latest_commit(
                token=token, organization=org, repo_name=repo_name, branch=branch,
            )
        except Exception as e:
            raise ValueError(f'获取仓库 {repo_name} 分支 {branch} 最新 commit 失败：{e}')
        if not commit_id:
            raise ValueError(f'仓库 {repo_name} 分支 {branch} 未返回有效 commit')
        merge_request.append({
            'repo_id': repo.id,
            'merge_url': '',
            'repo_name': repo_name,
            'branch_name': branch,
            'latest_commitId': commit_id,
        })
    return merge_request


@deploy_bp.route('/client/<int:client_id>/publish-prod', methods=['POST'])
def publish_prod_api(client_id):
    """
    「发布生产」入口：
    1. 根据描述新建一条独立 ChatMessage（task_id=0, chat_id=0, client_id=当前应用,
       input=描述, status=completed, extra.merge_request=仓库最新 commit 列表）
    2. 新建一条 prod 环境发布记录（task_id=0, chat_id=0, msg_id=上述 msg_id, status=pending）
    """
    user_id = request.user_info.user_id
    client = get_client_by_id(client_id, user_id)
    if not client:
        return jsonify({'code': 404, 'message': '客户端不存在或无权限'}), 404

    data = request.get_json(silent=True) or {}
    description = (data.get('description') or '').strip()
    if not description:
        return jsonify({'code': 400, 'message': '发布描述不能为空'}), 400
    if len(description) > 512:
        return jsonify({'code': 400, 'message': '发布描述长度不能超过 512 个字符'}), 400

    try:
        merge_request = _build_merge_request_for_client(client_id=client_id, user_id=user_id)
    except ValueError as e:
        return jsonify({'code': 400, 'message': str(e)}), 400
    except Exception:
        logger.exception('publish_prod: build merge_request failed, client_id=%s', client_id)
        return jsonify({'code': 500, 'message': '构建发布信息失败，请稍后重试'}), 500

    msg_id = create_standalone_chat_message(
        user_id=user_id,
        client_id=client_id,
        input_text=description,
        output_text='',
        extra={'merge_request': merge_request},
        status=ChatMessage.STATUS_COMPLETED,
    )

    detail = {
        'task_id': 0,
        'chat_id': 0,
        'msg_id': msg_id,
        'source': 'publish_prod',
        'merge_request': merge_request,
    }
    record_id = create_deploy_record(
        user_id=user_id, client_id=client_id, env='prod',
        description=description[:255], status=DeployRecord.STATUS_PENDING,
        detail=detail, msg_id=msg_id, task_id=0, chat_id=0,
    )

    return jsonify({
        'code': 201,
        'message': '发布生产记录创建成功',
        'data': {'record_id': record_id, 'msg_id': msg_id},
    }), 201


@deploy_bp.route('/records/<int:record_id>/preview', methods=['POST'])
def preview_record_api(record_id):
    """
    基于已有发布记录的预览：
    - 仅允许 env=test 的记录
    - 存在对应 docker 网络：返回 ready + 预览 URL
    - 不存在：将记录重置为 pending（若可重置），提示正在重新发布
    """
    from service.remote_deploy_service import check_test_docker_network_exists

    user_id = request.user_info.user_id
    record = get_deploy_record_by_id(user_id=user_id, record_id=record_id)
    if not record:
        return jsonify({'code': 404, 'message': '发布记录不存在或无权限'}), 404
    if record.env != 'test':
        return jsonify({'code': 400, 'message': '仅测试环境记录支持预览'}), 400

    client_id = record.client_id
    task_id = record.task_id or 0
    chat_id = record.chat_id or 0
    msg_id = record.msg_id or 0
    if msg_id <= 0:
        return jsonify({'code': 400, 'message': '记录缺少 msg_id，无法预览'}), 400

    domains = [d.domain for d in get_client_domains(client_id=client_id, user_id=user_id, env='test')]
    domain_values = [d.strip() for d in domains if d and d.strip()]
    if not domain_values:
        return jsonify({'code': 400, 'message': '未配置应用测试环境域名'}), 400

    host_key = f'task{task_id}chat{chat_id}msg{msg_id}'
    preview_url = f'https://{host_key}.{domain_values[0]}'

    try:
        network_ready = check_test_docker_network_exists(
            client_id=client_id, user_id=user_id, host_key=host_key,
        )
    except Exception:
        logger.exception(
            'preview_record: check test docker network failed, record_id=%s host_key=%s',
            record_id, host_key,
        )
        network_ready = False

    if network_ready:
        return jsonify({
            'code': 200,
            'data': {
                'status': 'ready', 'url': preview_url, 'host_key': host_key,
                'record_id': record.id,
            },
        })

    # 网络不存在：publishing/pending 时保持，其它状态重置为 pending
    if record.status in (DeployRecord.STATUS_PUBLISHING, DeployRecord.STATUS_PENDING):
        action = 'publishing'
    else:
        ok = reset_deploy_record_to_pending(user_id=user_id, record_id=record.id)
        action = 'reset' if ok else 'publishing'

    return jsonify({
        'code': 200,
        'data': {
            'status': 'deploying',
            'url': preview_url,
            'host_key': host_key,
            'record_id': record.id,
            'action': action,
            'message': '服务正在重新发布，请稍后查看',
        },
    })


@deploy_bp.route('/records/<int:record_id>/publish-prod', methods=['POST'])
def publish_prod_from_record_api(record_id):
    """
    以当前测试环境发布记录的信息，新建一条生产环境发布记录。

    仅允许：env=test && status=success && task_id=0 && chat_id=0 的记录。
    生产记录复用原 msg_id / task_id / chat_id，status=pending，
    merge_request 从原 msg.extra 取回（保证 remote_deploy_service 可继续使用）。
    """
    user_id = request.user_info.user_id
    record = get_deploy_record_by_id(user_id=user_id, record_id=record_id)
    if not record:
        return jsonify({'code': 404, 'message': '发布记录不存在或无权限'}), 404
    if record.env != 'test':
        return jsonify({'code': 400, 'message': '仅测试环境记录可发布生产'}), 400
    if record.status != DeployRecord.STATUS_SUCCESS:
        return jsonify({'code': 400, 'message': '仅发布成功的记录可发布生产'}), 400
    if (record.task_id or 0) != 0 or (record.chat_id or 0) != 0:
        return jsonify({'code': 400, 'message': '仅 task_id=0 且 chat_id=0 的记录可发布生产'}), 400

    src_msg_id = record.msg_id or 0
    if src_msg_id <= 0:
        return jsonify({'code': 400, 'message': '记录缺少 msg_id，无法发布生产'}), 400

    msgs = batch_get_msg_by_msgids(user_id=user_id, msg_ids=[src_msg_id])
    merge_request = []
    src_input = ''
    if msgs:
        src_extra = msgs[0].extra or {}
        merge_request = src_extra.get('merge_request') or []
        src_input = (msgs[0].input or '').strip()
    if not merge_request:
        # 兜底：从原记录 detail 里取
        merge_request = (record.detail or {}).get('merge_request') or []
    if not merge_request:
        return jsonify({'code': 400, 'message': '未找到原记录的 merge_request，无法发布生产'}), 400

    description = (src_input or record.description or '发布生产')[:255]

    # 新建一条独立 ChatMessage，避免 (task_id, chat_id, msg_id, env) 唯一约束与原 test 记录的
    # 生产孪生体冲突，并让后续 remote_deploy 按 msg 分组时仍可定位到 merge_request。
    new_msg_id = create_standalone_chat_message(
        user_id=user_id,
        client_id=record.client_id,
        input_text=description,
        output_text='',
        extra={
            'merge_request': merge_request,
            'from_record_id': record.id,
            'from_msg_id': src_msg_id,
        },
        status=ChatMessage.STATUS_COMPLETED,
    )

    detail = {
        'task_id': 0,
        'chat_id': 0,
        'msg_id': new_msg_id,
        'source': 'publish_prod_from_test',
        'merge_request': merge_request,
        'from_record_id': record.id,
        'from_msg_id': src_msg_id,
    }
    new_record_id = create_deploy_record(
        user_id=user_id, client_id=record.client_id, env='prod',
        description=description, status=DeployRecord.STATUS_PENDING,
        detail=detail, msg_id=new_msg_id, task_id=0, chat_id=0,
    )

    return jsonify({
        'code': 201,
        'message': '生产发布记录创建成功',
        'data': {'record_id': new_record_id, 'msg_id': new_msg_id},
    }), 201
