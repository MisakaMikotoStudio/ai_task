#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
客户端基础设施配置服务 —— 云服务器、域名、数据库、支付、对象存储的 CRUD
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
    保存指定类型的基础设施配置（支持 servers/domains/databases/payments/oss）。

    Raises:
        InfraConfigError: 参数校验失败
    """
    from dao.client_dao import (
        upsert_client_server, delete_client_server_by_env,
        sync_client_domains, sync_client_databases,
        upsert_client_payment, delete_client_payment_by_env,
        upsert_client_oss, delete_client_oss_by_env,
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

        elif infra_type == 'payments':
            if env_data is None:
                continue
            if not isinstance(env_data, dict):
                raise InfraConfigError(f'{env_key} 支付配置格式无效')
            payment_type = (env_data.get('payment_type') or 'alipay').strip()
            if payment_type not in ('alipay',):
                raise InfraConfigError(f'不支持的支付类型：{payment_type}')
            has_content = any(
                (env_data.get(f) or '').strip()
                for f in ('appid', 'app_private_key', 'alipay_public_key', 'notify_url', 'return_url', 'gateway')
            )
            if has_content:
                upsert_client_payment(
                    client_id=client_id, user_id=user_id, env=env_key,
                    payment_type=payment_type, fields=env_data,
                )
            else:
                delete_client_payment_by_env(client_id=client_id, user_id=user_id, env=env_key)

        elif infra_type == 'oss':
            if env_data is None:
                continue
            if not isinstance(env_data, dict):
                raise InfraConfigError(f'{env_key} 对象存储配置格式无效')
            oss_type = (env_data.get('oss_type') or 'cos').strip()
            if oss_type not in ('cos',):
                raise InfraConfigError(f'不支持的对象存储类型：{oss_type}')
            has_content = any(
                (env_data.get(f) or '').strip() for f in ('secret_id', 'secret_key', 'region', 'bucket')
            )
            if has_content:
                upsert_client_oss(
                    client_id=client_id, user_id=user_id, env=env_key,
                    oss_type=oss_type, fields=env_data,
                )
            else:
                delete_client_oss_by_env(client_id=client_id, user_id=user_id, env=env_key)

        else:
            raise InfraConfigError(f'未知配置类型：{infra_type}')


def get_client_infrastructure(client_id: int, user_id: int) -> dict:
    """
    获取客户端全量基础设施配置。

    Returns:
        {"servers": {...}, "domains": {...}, "databases": {...}, "payments": {...}, "oss": {...}}
    """
    from dao.client_dao import (
        get_client_servers, get_client_domains,
        get_client_databases, get_client_payment, get_client_oss,
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

    payments_result: dict = {}
    for pay in get_client_payment(client_id=client_id, user_id=user_id):
        payments_result[pay.env] = pay.to_dict()

    oss_result: dict = {}
    for oss in get_client_oss(client_id=client_id, user_id=user_id):
        oss_result[oss.env] = oss.to_dict()

    from dao.client_dao import get_client_deploys
    deploys_result = [d.to_dict() for d in get_client_deploys(client_id=client_id, user_id=user_id)]

    return {
        'servers': servers_result,
        'domains': domains_result,
        'databases': databases_result,
        'payments': payments_result,
        'oss': oss_result,
        'deploys': deploys_result,
    }


def save_all_infrastructure(client_id: int, user_id: int, data: dict) -> None:
    """
    一次性保存全量基础设施配置（云服务器、域名、数据库、支付、对象存储）。

    Raises:
        InfraConfigError: 参数校验或 SSH 校验失败
    """
    servers_data = data.get('servers') or {}
    domains_data = data.get('domains') or {}
    databases_data = data.get('databases') or {}
    payments_data = data.get('payments') or {}
    oss_data = data.get('oss') or {}
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
    if payments_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='payments', data=payments_data)
    if oss_data:
        save_client_infrastructure(client_id=client_id, user_id=user_id, infra_type='oss', data=oss_data)

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
