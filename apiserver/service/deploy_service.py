#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
部署配置服务 —— deploy 配置的保存、TOML 生成/合并、SSH 远程写入
"""

import logging
import random
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 官方配置可选项
VALID_OFFICIAL_CONFIGS = ['app_name', 'domain', 'database', 'special_accounts']

# 官方配置选项的中文标签（前端展示用）
OFFICIAL_CONFIG_LABELS = {
    'app_name': '应用名',
    'domain': '域名',
    'database': '数据库',
    'special_accounts': '特殊账号',
}


class DeployConfigError(Exception):
    """部署配置校验或操作失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _generate_unique_uuid() -> str:
    """
    生成 6 位随机数字的唯一 uuid。
    最多重试 10 次，失败则抛出异常。
    """
    from dao.client_dao import is_deploy_uuid_exists

    for _ in range(10):
        uuid = str(random.randint(100000, 999999))
        if not is_deploy_uuid_exists(uuid=uuid):
            return uuid
    raise DeployConfigError('部署 UUID 生成失败，请稍后重试')


def _validate_toml(content: str) -> None:
    """校验 TOML 格式是否合法，不合法则抛出 DeployConfigError"""
    if not content or not content.strip():
        return
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    try:
        tomllib.loads(content)
    except Exception as e:
        raise DeployConfigError(f'自定义配置 TOML 格式错误：{e}')


def _parse_toml(content: str) -> dict:
    """解析 TOML 字符串为字典"""
    if not content or not content.strip():
        return {}
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    return tomllib.loads(content)


def _deep_merge(base: dict, override: dict) -> dict:
    """
    深度合并两个字典，override 中的值优先。
    如果同一个 key 在两个字典中都是 dict，则递归合并。
    """
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _dict_to_toml(data: dict, prefix: str = '') -> str:
    """
    将字典转换为 TOML 格式的字符串。
    支持嵌套的 table（section）。
    """
    lines = []
    # 先输出非 dict 的顶层键值对
    for key, val in data.items():
        if not isinstance(val, dict):
            lines.append(f'{key} = {_toml_value(val=val)}')

    # 再输出 dict 类型的键值对（作为 section）
    for key, val in data.items():
        if isinstance(val, dict):
            section_name = f'{prefix}.{key}' if prefix else key
            lines.append('')
            lines.append(f'[{section_name}]')
            # 递归处理嵌套
            sub_lines = _dict_to_toml_inner(data=val, prefix=section_name)
            lines.append(sub_lines)

    return '\n'.join(lines).strip() + '\n'


def _dict_to_toml_inner(data: dict, prefix: str) -> str:
    """递归处理嵌套的 TOML section"""
    lines = []
    for key, val in data.items():
        if not isinstance(val, dict):
            lines.append(f'{key} = {_toml_value(val=val)}')

    for key, val in data.items():
        if isinstance(val, dict):
            section_name = f'{prefix}.{key}'
            lines.append('')
            lines.append(f'[{section_name}]')
            sub_lines = _dict_to_toml_inner(data=val, prefix=section_name)
            lines.append(sub_lines)

    return '\n'.join(lines)


def _toml_value(val) -> str:
    """将 Python 值转为 TOML 值字面量。

    bool / int / float / str / list / dict 都会按 TOML 字面量格式输出；
    dict 走 "inline table"（{k = v, ...}），嵌到 list 里就是合法的
    "array of inline tables" —— 用这种方式放 special_accounts 而不是
    [[auth.special_accounts]]，避免 _dict_to_toml 的 section 构造逻辑被
    list[dict] 打断。
    """
    if isinstance(val, bool):
        return 'true' if val else 'false'
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return str(val)
    if isinstance(val, str):
        escaped = val.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(val, dict):
        items = ', '.join(
            f'{k} = {_toml_value(val=v)}' for k, v in val.items()
        )
        return '{' + items + '}'
    if isinstance(val, list):
        items = ', '.join(_toml_value(val=v) for v in val)
        return f'[{items}]'
    return f'"{val}"'


def generate_official_toml(client_id: int, user_id: int, official_configs: list, env: str = 'prod') -> dict:
    """
    根据 official_configs 中选中的配置项，从 client 的基础设施配置中提取数据，构建 TOML 字典。

    Args:
        client_id: 客户端 ID
        user_id: 用户 ID
        official_configs: 选中的官方配置项列表
        env: 环境标识（test/prod），用于提取对应环境的配置

    Returns:
        包含官方配置的字典
    """
    from dao.client_dao import (
        get_client_by_id, get_client_domains, get_client_databases,
        get_client_special_accounts,
    )

    result = {}

    if 'app_name' in official_configs:
        client = get_client_by_id(client_id=client_id, user_id=user_id)
        if client:
            result['app'] = {'name': client.name}

    if 'domain' in official_configs:
        domains = get_client_domains(client_id=client_id, user_id=user_id)
        env_domains = [d.domain for d in domains if d.env == env]
        if env_domains:
            result['domain'] = {'domains': env_domains}

    if 'database' in official_configs:
        databases = get_client_databases(client_id=client_id, user_id=user_id)
        env_dbs = [db for db in databases if db.env == env]
        if env_dbs:
            db = env_dbs[0]
            result['database'] = {
                'type': db.db_type or 'mysql',
                'url': db.host or '',
                'port': db.port or 3306,
                'username': db.username or '',
                'password': db.password or '',
                'database': db.db_name or '',
            }

    if 'special_accounts' in official_configs:
        # 目标 apiserver 约定读取 [auth].special_accounts（数组，元素为 {name, password}）
        # 这里不区分环境：特殊账号在 test/prod 下完全一致
        accounts = get_client_special_accounts(client_id=client_id, user_id=user_id)
        if accounts:
            result.setdefault('auth', {})
            result['auth']['special_accounts'] = [
                {'name': acc.name or '', 'password': acc.password or ''}
                for acc in accounts
            ]

    return result


def generate_deploy_toml(client_id: int, user_id: int, official_configs: list, custom_config: str, env: str = 'prod') -> str:
    """
    生成最终的 deploy TOML 配置内容。
    先生成官方配置 TOML，再合并自定义配置（冲突时保留自定义配置）。

    Returns:
        最终的 TOML 字符串
    """
    official_dict = generate_official_toml(client_id=client_id, user_id=user_id, official_configs=official_configs, env=env)
    custom_dict = _parse_toml(content=custom_config)
    merged = _deep_merge(base=official_dict, override=custom_dict)
    if not merged:
        return ''
    return _dict_to_toml(data=merged)


def save_deploy_configs(client_id: int, user_id: int, deploys_data: list) -> None:
    """
    全量同步客户端的部署配置。

    流程：
    1. 校验每条 deploy 的数据
    2. 区分已有记录（有 id）和新记录
    3. 更新已有记录、创建新记录、软删除被移除的记录

    Args:
        client_id: 客户端 ID
        user_id: 用户 ID
        deploys_data: 前端提交的部署配置列表

    Raises:
        DeployConfigError: 校验失败
    """
    from dao.client_dao import (
        get_client_deploys, add_client_deploy, update_client_deploy,
        soft_delete_client_deploys,
    )
    from service.deploy_route_prefix import normalize_deploy_route_prefix, validate_unique_route_prefixes

    if not isinstance(deploys_data, list):
        raise DeployConfigError('部署配置必须是数组')

    keep_ids = []
    normalized_prefixes = []

    for idx, deploy in enumerate(deploys_data):
        num = idx + 1
        if not isinstance(deploy, dict):
            raise DeployConfigError(f'部署配置 #{num} 格式无效')

        startup_command = (deploy.get('startup_command') or '').strip()
        official_configs = deploy.get('official_configs', [])
        custom_config = deploy.get('custom_config') or ''
        repo_id = deploy.get('repo_id') or None
        work_dir = (deploy.get('work_dir') or '').strip()
        route_prefix = (deploy.get('route_prefix') or '').strip()
        try:
            normalized_prefixes.append(normalize_deploy_route_prefix(route_prefix))
        except ValueError as e:
            raise DeployConfigError(f'部署配置 #{num}：{e}') from e

        # repo_id 转为 int 或 None
        if repo_id is not None:
            try:
                repo_id = int(repo_id)
            except (ValueError, TypeError):
                raise DeployConfigError(f'部署配置 #{num} 代码仓库 ID 无效')

        if not isinstance(official_configs, list):
            raise DeployConfigError(f'部署配置 #{num} 官方配置必须是数组')
        for cfg in official_configs:
            if cfg not in VALID_OFFICIAL_CONFIGS:
                raise DeployConfigError(f'部署配置 #{num} 包含无效的官方配置项：{cfg}')

        # 校验自定义配置 TOML 格式
        _validate_toml(content=custom_config)

        deploy_id = deploy.get('id')
        if deploy_id:
            # 更新已有记录
            update_client_deploy(
                deploy_id=deploy_id, client_id=client_id, user_id=user_id,
                startup_command=startup_command, official_configs=official_configs, custom_config=custom_config,
                repo_id=repo_id, work_dir=work_dir, route_prefix=route_prefix,
            )
            keep_ids.append(deploy_id)
        else:
            # 新建记录，生成 uuid
            uuid = _generate_unique_uuid()
            new_id = add_client_deploy(
                client_id=client_id, user_id=user_id, uuid=uuid,
                startup_command=startup_command, official_configs=official_configs, custom_config=custom_config,
                repo_id=repo_id, work_dir=work_dir, route_prefix=route_prefix,
            )
            keep_ids.append(new_id)

    try:
        validate_unique_route_prefixes(normalized_prefixes)
    except ValueError as e:
        raise DeployConfigError(str(e)) from e

    # 软删除被移除的记录
    soft_delete_client_deploys(client_id=client_id, user_id=user_id, exclude_ids=keep_ids)


def execute_deploy(client_id: int, user_id: int, deploy_id: int) -> str:
    f"""
    执行部署：SSH 连接到已配置的云服务器，将生成的 TOML 配置写入 /home/{username}/app{client_id}/config{uuid}/config.toml。

    对所有已配置的服务器（test/prod）逐一部署，使用对应环境的配置生成 TOML。

    Returns:
        部署结果描述

    Raises:
        DeployConfigError: 部署失败
    """
    from dao.client_dao import get_client_deploy_by_id, get_client_servers

    deploy = get_client_deploy_by_id(deploy_id=deploy_id, client_id=client_id, user_id=user_id)
    if not deploy:
        raise DeployConfigError('部署配置不存在')

    servers = get_client_servers(client_id=client_id, user_id=user_id)
    if not servers:
        raise DeployConfigError('没有已配置的云服务器，无法执行部署')

    results = []
    for server in servers:
        env = server.env
        ip = (server.ip or '').strip()
        username = (server.name or '').strip()
        password = (server.password or '').strip()

        if not ip:
            continue

        # 生成该环境的 TOML 配置
        toml_content = generate_deploy_toml(
            client_id=client_id, user_id=user_id,
            official_configs=deploy.official_configs or [],
            custom_config=deploy.custom_config or '',
            env=env,
        )

        # SSH 写入配置文件（放在用户 home 目录下，避免根目录权限问题）
        config_dir = f'/home/{username}/app{client_id}/config{deploy.uuid}'
        config_path = f'{config_dir}/config.toml'

        try:
            _ssh_write_file(ip=ip, username=username, password=password, remote_dir=config_dir, remote_path=config_path, content=toml_content)
            results.append(f'{env}({ip}): 部署成功')
            logger.info("execute_deploy: deploy_id=%s, env=%s, ip=%s, path=%s, success", deploy_id, env, ip, config_path)
        except Exception as e:
            error_msg = f'{env}({ip}): 部署失败 - {e}'
            results.append(error_msg)
            logger.error("execute_deploy: deploy_id=%s, env=%s, ip=%s, error=%s", deploy_id, env, ip, str(e))

    if not results:
        raise DeployConfigError('没有可用的服务器进行部署')

    return '；'.join(results)


def _ssh_write_file(ip: str, username: str, password: str, remote_dir: str, remote_path: str, content: str) -> None:
    """通过 SSH 在远程服务器上创建目录并写入文件"""
    try:
        import paramiko
    except ImportError:
        raise DeployConfigError('SSH 依赖未安装，请联系管理员')

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=ip, username=username, password=password, timeout=10, allow_agent=False, look_for_keys=False)
        # 创建目录
        stdin, stdout, stderr = client.exec_command(f'mkdir -p {remote_dir}')
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err_output = stderr.read().decode('utf-8', errors='replace').strip()
            raise DeployConfigError(f'创建目录失败：{err_output}')

        # 写入文件
        sftp = client.open_sftp()
        try:
            with sftp.file(remote_path, 'w') as f:
                f.write(content)
        finally:
            sftp.close()
    finally:
        client.close()


def auto_create_test_deploy_on_message_sync(
    user_id: int,
    task_id: int,
    chat_id: int,
    message_id: int,
    merge_request: List[Dict[str, Any]],
) -> Optional[int]:
    """
    客户端 after_execute 通过 /api/open/chat/msg/sync_execute 上报 chat 分支 merge_request
    时，自动创建/更新一条测试环境发布记录。

    触发条件：merge_request 中存在至少一条带 repo_name 与 latest_commitId 的 diff 记录。
    幂等语义：按 (user_id, client_id, task_id, chat_id, msg_id, env='test') 唯一：
      - 不存在 → 创建 pending 记录
      - 已存在且 status == publishing → 不打断，仅返回现有记录 ID
      - 已存在且其它状态 → 重置为 pending 并更新 description/detail

    client_id 解析：
      - task_id > 0：从 Task.client_id 取
      - task_id == 0（独立 Chat）：从 Chat.client_id 取

    Returns:
        记录 ID；merge_request 无 diff、或无法解析 client_id 时返回 None。
    """
    if not merge_request:
        return None

    has_diff = any(
        isinstance(mr, dict) and mr.get('repo_name') and mr.get('latest_commitId')
        for mr in merge_request
    )
    if not has_diff:
        return None

    task_id = int(task_id or 0)
    chat_id = int(chat_id or 0)
    message_id = int(message_id or 0)
    if chat_id <= 0 or message_id <= 0:
        logger.warning(
            "auto test deploy skipped: missing chat_id/msg_id, user_id=%s task_id=%s chat_id=%s msg_id=%s",
            user_id, task_id, chat_id, message_id,
        )
        return None

    client_id: Optional[int] = None
    if task_id > 0:
        from dao.task_dao import get_task_by_id
        task = get_task_by_id(task_id, user_id)
        client_id = getattr(task, 'client_id', None) if task else None
    else:
        from dao.chat_dao import get_chat_by_id
        chat = get_chat_by_id(user_id=user_id, chat_id=chat_id, task_id=task_id)
        client_id = getattr(chat, 'client_id', None) if chat else None

    if not client_id:
        logger.warning(
            "auto test deploy skipped: client_id unresolved, user_id=%s task_id=%s chat_id=%s msg_id=%s",
            user_id, task_id, chat_id, message_id,
        )
        return None

    from dao.deploy_dao import upsert_auto_test_deploy_record

    description = f'after_execute 自动发布 task{task_id}chat{chat_id}msg{message_id}'[:255]
    detail = {
        'task_id': task_id,
        'chat_id': chat_id,
        'msg_id': message_id,
        'source': 'after_execute',
    }

    record_id, action = upsert_auto_test_deploy_record(
        user_id=user_id,
        client_id=int(client_id),
        task_id=task_id,
        chat_id=chat_id,
        msg_id=message_id,
        description=description,
        detail=detail,
    )
    logger.info(
        "auto test deploy upsert: action=%s record_id=%s user_id=%s client_id=%s task_id=%s chat_id=%s msg_id=%s",
        action, record_id, user_id, client_id, task_id, chat_id, message_id,
    )
    return record_id
