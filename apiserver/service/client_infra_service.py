#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端基础设施配置服务 —— 云服务器、域名、数据库的 CRUD
"""

import logging
from typing import Tuple

from dao.client_dao import VALID_ENVS
from service.client_service import ClientSaveError

logger = logging.getLogger(__name__)

SSH_CHECK_TIMEOUT = 5  # SSH 连通性检查超时秒数


class InfraConfigError(ClientSaveError):
    """基础设施配置校验或操作失败"""
    pass


def check_ssh_connectivity(ip: str, username: str, password: str) -> Tuple[bool, str]:
    """
    使用 paramiko 检查 SSH 连通性。

    Returns:
        (success: bool, error_message: str)
    """
    try:
        import paramiko
    except ImportError:
        logger.error("paramiko not installed, cannot check SSH connectivity")
        return False, "SSH 校验依赖未安装，请联系管理员"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ip,
            username=username,
            password=password,
            timeout=SSH_CHECK_TIMEOUT,
            allow_agent=False,
            look_for_keys=False,
        )
        client.close()
        logger.info("SSH connectivity check passed for ip=%s user=%s", ip, username)
        return True, ""
    except Exception as e:
        # 不在日志中打印 password
        logger.warning("SSH connectivity check failed for ip=%s user=%s: %s", ip, username, type(e).__name__)
        return False, f"SSH 连接失败（{ip}）：{type(e).__name__}: {str(e)}"
    finally:
        client.close()


def _is_valid_ip_address(value: str) -> bool:
    """判断字符串是否为合法的 IP 地址（IPv4 或 IPv6）"""
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def check_servers_ssh(servers_data: dict) -> Tuple[bool, str]:
    """
    对 servers_data 中所有非空 ip 的服务器配置进行校验。

    - 如果 ip 是合法的 IP 地址，执行 SSH 连通性检查
    - 如果 ip 不是合法 IP，检查是否为有效的、上架中的云服务器资源名称
    """
    from dao.resource_dao import get_online_resource_by_name
    from dao.models import Resource

    env_labels = {'test': '测试环境', 'prod': '生产环境'}
    for env_key, cfg in servers_data.items():
        if not isinstance(cfg, dict):
            continue
        ip = (cfg.get('ip') or '').strip()
        if not ip:
            continue
        label = env_labels.get(env_key, env_key)

        if _is_valid_ip_address(ip):
            name = (cfg.get('name') or '').strip()
            password = (cfg.get('password') or '').strip()
            ok, err = check_ssh_connectivity(ip=ip, username=name, password=password)
            if not ok:
                return False, f"{label} SSH 校验失败：{err}"
        else:
            resource = get_online_resource_by_name(name=ip)
            if not resource or resource.type != Resource.TYPE_CLOUD_SERVER:
                return False, f"{label} 云服务器 \"{ip}\" 不是有效的IP地址，也不是有效的、上架中的云服务器资源名称"
    return True, ""


def save_client_infrastructure(client_id: int, user_id: int, infra_type: str, data: dict) -> None:
    """
    保存指定类型的基础设施配置（支持 servers/domains/databases）。

    Raises:
        InfraConfigError: 参数校验失败
    """
    from dao.client_dao import (
        upsert_client_server, delete_client_server_by_env,
        sync_client_domains, sync_client_databases,
    )

    for env_key in VALID_ENVS:
        env_data = data.get(env_key)

        if infra_type == 'servers':
            if env_data is None:
                continue
            if not isinstance(env_data, dict):
                raise InfraConfigError(f'{env_key} 云服务器配置格式无效')
            ip = (env_data.get('ip') or '').strip()
            name = (env_data.get('name') or '').strip()
            password = (env_data.get('password') or '').strip()
            if ip:
                upsert_client_server(
                    client_id=client_id, user_id=user_id, env=env_key,
                    name=name, password=password, ip=ip,
                )
            else:
                delete_client_server_by_env(client_id=client_id, user_id=user_id, env=env_key)

        elif infra_type == 'domains':
            if env_data is None:
                env_data = []
            if not isinstance(env_data, list):
                raise InfraConfigError(f'{env_key} 域名配置格式无效')
            sync_client_domains(
                client_id=client_id, user_id=user_id, env=env_key,
                domains=[str(d).strip() for d in env_data if str(d).strip()],
            )

        elif infra_type == 'databases':
            if env_data is None:
                env_data = []
            if not isinstance(env_data, list):
                raise InfraConfigError(f'{env_key} 数据库配置格式无效')
            for idx, db in enumerate(env_data):
                if not isinstance(db, dict):
                    raise InfraConfigError(f'{env_key} 数据库配置 #{idx + 1} 格式无效')
                db_type = (db.get('db_type') or 'mysql').strip()
                if db_type not in ('mysql',):
                    raise InfraConfigError(f'不支持的数据库类型：{db_type}')
                if not (db.get('host') or '').strip():
                    raise InfraConfigError(f'{env_key} 数据库 #{idx + 1} 地址不能为空')
            sync_client_databases(client_id=client_id, user_id=user_id, env=env_key, databases=env_data)

        else:
            raise InfraConfigError(f'未知配置类型：{infra_type}')


def get_client_infrastructure(client_id: int, user_id: int) -> dict:
    """
    获取客户端全量基础设施配置。

    Returns:
        {"servers": {...}, "domains": {...}, "databases": {...},
         "special_accounts": [...], "deploys": [...]}
    """
    from dao.client_dao import (
        get_client_servers, get_client_domains,
        get_client_databases, get_client_special_accounts,
    )

    servers_result: dict = {}
    for srv in get_client_servers(client_id=client_id, user_id=user_id):
        servers_result[srv.env] = srv.to_dict()

    domains_result: dict = {e: [] for e in VALID_ENVS}
    for dom in get_client_domains(client_id=client_id, user_id=user_id):
        domains_result.setdefault(dom.env, []).append(dom.domain)

    databases_result: dict = {e: [] for e in VALID_ENVS}
    for db in get_client_databases(client_id=client_id, user_id=user_id):
        databases_result.setdefault(db.env, []).append(db.to_dict())

    special_accounts_result = [
        acc.to_dict() for acc in get_client_special_accounts(client_id=client_id, user_id=user_id)
    ]

    from dao.client_dao import get_client_deploys
    deploys_result = [d.to_dict() for d in get_client_deploys(client_id=client_id, user_id=user_id)]

    return {
        'servers': servers_result,
        'domains': domains_result,
        'databases': databases_result,
        'special_accounts': special_accounts_result,
        'deploys': deploys_result,
    }


def save_client_special_accounts(client_id: int, user_id: int, data: list) -> None:
    """保存特殊账号列表（全量同步）。

    入参为 [{'name': str, 'password': str}, ...]，name 必填且 client 内唯一，
    password 允许为空字符串但一般应非空（前端新建默认会给 16 位随机密码）。
    """
    from dao.client_dao import sync_client_special_accounts

    if not isinstance(data, list):
        raise InfraConfigError('特殊账号配置必须是数组')
    seen = set()
    normalized = []
    for idx, acc in enumerate(data):
        if not isinstance(acc, dict):
            raise InfraConfigError(f'特殊账号 #{idx + 1} 格式无效')
        name = (acc.get('name') or '').strip()
        password = acc.get('password')
        if password is None:
            password = ''
        password = str(password)
        if not name:
            raise InfraConfigError(f'特殊账号 #{idx + 1} 账号名不能为空')
        if len(name) > 64:
            raise InfraConfigError(f'特殊账号 #{idx + 1} 账号名长度不能超过 64 个字符')
        if name in seen:
            raise InfraConfigError(f'特殊账号名称重复：{name}')
        seen.add(name)
        normalized.append({'name': name, 'password': password})
    sync_client_special_accounts(
        client_id=client_id, user_id=user_id, accounts=normalized,
    )


def save_all_infrastructure(client_id: int, user_id: int, data: dict) -> None:
    """
    一次性保存全量基础设施配置（云服务器、域名、数据库、特殊账号）。

    Raises:
        InfraConfigError: 参数校验或 SSH 校验失败
    """
    servers_data = data.get('servers') or {}
    domains_data = data.get('domains') or {}
    databases_data = data.get('databases') or {}
    special_accounts_data = data.get('special_accounts')
    deploys_data = data.get('deploys')

    # SSH 连通性校验（仅在有服务器 ip 时）
    if servers_data:
        ssh_ok, ssh_err = check_servers_ssh(servers_data)
        if not ssh_ok:
            raise InfraConfigError(ssh_err)

    # 逐类型保存
    if servers_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='servers', data=servers_data)
    if domains_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='domains', data=domains_data)
    if databases_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='databases', data=databases_data)
    if special_accounts_data is not None:
        save_client_special_accounts(
            client_id=client_id, user_id=user_id, data=special_accounts_data,
        )

    # 保存部署配置（deploys 是列表，不区分环境）
    if deploys_data is not None and isinstance(deploys_data, list):
        from service.deploy_service import save_deploy_configs, execute_deploy
        save_deploy_configs(client_id=client_id, user_id=user_id, deploys_data=deploys_data)

        # 保存后自动执行部署：SSH 写入配置文件到远程服务器
        from dao.client_dao import get_client_deploys
        saved_deploys = get_client_deploys(client_id=client_id, user_id=user_id)
        for dep in saved_deploys:
            try:
                execute_deploy(client_id=client_id, user_id=user_id, deploy_id=dep.id)
                logger.info("auto deploy after save: client_id=%s, deploy_id=%s, success", client_id, dep.id)
            except Exception as e:
                logger.warning("auto deploy after save: client_id=%s, deploy_id=%s, error=%s", client_id, dep.id, str(e))
