#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
部署配置业务逻辑服务层
"""

import copy
import io
import logging
from typing import Dict, List, Optional, Tuple

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 及以下

try:
    import tomli_w
except ModuleNotFoundError:
    tomli_w = None

from dao.deploy_dao import (
    apply_deploy_sync,
    get_deploy_by_id,
    get_deploys_by_client,
)
from dao.models import ClientDeploy

logger = logging.getLogger(__name__)

# 可选的官方配置项
AVAILABLE_OFFICIAL_CONFIGS = ClientDeploy.AVAILABLE_OFFICIAL_CONFIGS
OFFICIAL_CONFIG_LABELS = ClientDeploy.OFFICIAL_CONFIG_LABELS


class DeploySaveError(Exception):
    """部署配置保存失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DeployExecuteError(Exception):
    """部署执行失败"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _strip_str(val) -> str:
    if val is None:
        return ''
    return str(val).strip()


def validate_custom_config(text: str) -> Optional[str]:
    """
    校验自定义配置是否为合法的 TOML 格式。

    Args:
        text: TOML 格式字符串

    Returns:
        None 表示合法，否则返回错误描述
    """
    if not text or not text.strip():
        return None
    try:
        tomllib.loads(text)
        return None
    except Exception as e:
        return f"自定义配置 TOML 格式错误: {e}"


def _normalize_deploy_item(item: dict, index: int) -> Optional[str]:
    """
    规范化单个 deploy 配置项（就地修改）。

    Returns:
        None 通过校验，否则返回错误文案
    """
    if not isinstance(item, dict):
        return f'Deploy配置#{index + 1} 格式无效'

    startup_command = _strip_str(item.get('startup_command'))
    item['startup_command'] = startup_command

    official_configs = item.get('official_configs', [])
    if not isinstance(official_configs, list):
        return f'Deploy配置#{index + 1} 官方配置必须是数组'
    for cfg in official_configs:
        if cfg not in AVAILABLE_OFFICIAL_CONFIGS:
            return f'Deploy配置#{index + 1} 无效的官方配置项: {cfg}，可选: {", ".join(AVAILABLE_OFFICIAL_CONFIGS)}'
    item['official_configs'] = official_configs

    custom_config = _strip_str(item.get('custom_config'))
    if custom_config:
        err = validate_custom_config(custom_config)
        if err:
            return f'Deploy配置#{index + 1} {err}'
    item['custom_config'] = custom_config

    return None


def normalize_deploy_payload(deploys: list) -> Optional[str]:
    """
    规范化 deploy 配置列表。

    Returns:
        None 通过校验，否则返回错误文案
    """
    if not isinstance(deploys, list):
        return 'deploys必须是数组'
    for idx, item in enumerate(deploys):
        err = _normalize_deploy_item(item, idx)
        if err:
            return err
    return None


def _deploy_fields_equal(existing: ClientDeploy, incoming: dict) -> bool:
    """比较现有记录与输入是否一致"""
    return (
        (existing.startup_command or '') == (incoming.get('startup_command') or '')
        and (existing.official_configs or []) == (incoming.get('official_configs') or [])
        and (existing.custom_config or '') == (incoming.get('custom_config') or '')
    )


def save_deploy_configs(client_id: int, deploys: List[dict], *, user_id: int) -> bool:
    """
    按 uuid（若有）或位置全量同步部署配置。
    有 id 的匹配更新，无 id 的新增，不在列表中的软删除。

    Returns:
        是否发生了任何持久化变更
    """
    existing_list = get_deploys_by_client(client_id=client_id, user_id=user_id)
    exist_map: Dict[int, ClientDeploy] = {d.id: d for d in existing_list}

    delete_ids: List[int] = []
    updates: List[dict] = []
    inserts: List[dict] = []

    incoming_ids = set()
    for item in deploys:
        deploy_id = item.get('id')
        if deploy_id and deploy_id in exist_map:
            incoming_ids.add(deploy_id)
            ex = exist_map[deploy_id]
            if not _deploy_fields_equal(ex, item):
                updates.append({
                    'id': deploy_id,
                    'startup_command': item.get('startup_command', ''),
                    'official_configs': item.get('official_configs', []),
                    'custom_config': item.get('custom_config', ''),
                })
        else:
            inserts.append({
                'startup_command': item.get('startup_command', ''),
                'official_configs': item.get('official_configs', []),
                'custom_config': item.get('custom_config', ''),
            })

    for eid in exist_map:
        if eid not in incoming_ids:
            delete_ids.append(eid)

    if not delete_ids and not updates and not inserts:
        return False

    apply_deploy_sync(
        client_id=client_id,
        user_id=user_id,
        delete_ids=delete_ids,
        updates=updates,
        inserts=inserts,
    )
    return True


def _build_official_config_dict(official_configs: list) -> dict:
    """
    根据选中的官方配置项，生成对应的 TOML 配置字典。
    这些是模板值，实际部署时会被客户端的真实配置替换。
    """
    result = {}

    if 'app_name' in official_configs:
        result['app'] = {'name': ''}

    if 'domain' in official_configs:
        result['domain'] = {'host': '', 'port': 0, 'ssl': False}

    if 'database' in official_configs:
        result['database'] = {
            'type': 'mysql',
            'url': '',
            'port': 3306,
            'username': '',
            'password': '',
            'database': '',
        }

    if 'alipay' in official_configs:
        result['alipay'] = {
            'app_id': '',
            'app_private_key': '',
            'alipay_public_key': '',
            'notify_url': '',
            'return_url': '',
            'gateway': 'https://openapi.alipay.com/gateway.do',
            'sandbox': False,
            'app_encrypt_key': '',
        }

    if 'oss' in official_configs:
        result['oss'] = {
            'enabled': False,
            'secret_id': '',
            'secret_key': '',
            'region': 'ap-guangzhou',
            'bucket': '',
            'base_url': '',
        }

    return result


def _deep_merge(base: dict, override: dict) -> dict:
    """
    深度合并两个字典，override 优先。

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        合并后的新字典
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _dict_to_toml(data: dict) -> str:
    """
    将字典序列化为 TOML 字符串。
    优先使用 tomli_w，若不可用则手动序列化。
    """
    if tomli_w:
        return tomli_w.dumps(data)
    return _manual_toml_serialize(data)


def _manual_toml_serialize(data: dict, prefix: str = '') -> str:
    """手动序列化字典为 TOML 格式字符串"""
    lines = []
    simple_items = {}
    table_items = {}

    for key, value in data.items():
        if isinstance(value, dict):
            table_items[key] = value
        else:
            simple_items[key] = value

    for key, value in simple_items.items():
        lines.append(f'{key} = {_toml_value(value)}')

    for key, value in table_items.items():
        section = f'{prefix}.{key}' if prefix else key
        lines.append('')
        lines.append(f'[{section}]')
        lines.append(_manual_toml_serialize(value, prefix=section))

    return '\n'.join(lines)


def _toml_value(value) -> str:
    """将 Python 值转为 TOML 值字符串"""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        items = ', '.join(_toml_value(v) for v in value)
        return f'[{items}]'
    return f'"{value}"'


def generate_config_toml(deploy_id: int, user_id: int) -> Tuple[bool, str]:
    """
    生成最终的 TOML 配置内容（官方配置 + 自定义配置合并）。

    Args:
        deploy_id: 部署配置ID
        user_id: 用户ID

    Returns:
        (success, content_or_error)
    """
    deploy = get_deploy_by_id(deploy_id=deploy_id, user_id=user_id)
    if not deploy:
        return False, '部署配置不存在'

    official_dict = _build_official_config_dict(deploy.official_configs or [])

    custom_dict = {}
    if deploy.custom_config and deploy.custom_config.strip():
        try:
            custom_dict = tomllib.loads(deploy.custom_config)
        except Exception as e:
            return False, f'自定义配置解析失败: {e}'

    merged = _deep_merge(official_dict, custom_dict)
    toml_content = _dict_to_toml(merged)
    return True, toml_content


def execute_deploy(deploy_id: int, user_id: int, deploy_config) -> Tuple[bool, str]:
    """
    执行部署：SSH 连接远程服务器，写入配置文件。

    Args:
        deploy_id: 部署配置ID
        user_id: 用户ID
        deploy_config: DeployConfig 实例（SSH配置）

    Returns:
        (success, message)
    """
    deploy = get_deploy_by_id(deploy_id=deploy_id, user_id=user_id)
    if not deploy:
        return False, '部署配置不存在'

    if not deploy_config.ssh_host:
        return False, '未配置SSH部署服务器地址'

    success, toml_content = generate_config_toml(deploy_id=deploy_id, user_id=user_id)
    if not success:
        return False, toml_content

    remote_dir = f'{deploy_config.config_base_path}{deploy.uuid}'
    remote_path = f'{remote_dir}/config.toml'

    try:
        import paramiko

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            'hostname': deploy_config.ssh_host,
            'port': deploy_config.ssh_port,
            'username': deploy_config.ssh_username,
        }
        if deploy_config.ssh_key_path:
            connect_kwargs['key_filename'] = deploy_config.ssh_key_path
        elif deploy_config.ssh_password:
            connect_kwargs['password'] = deploy_config.ssh_password
        else:
            return False, 'SSH认证信息不完整：需要密码或私钥路径'

        logger.info(f"SSH connecting to {deploy_config.ssh_host}:{deploy_config.ssh_port} for deploy uuid={deploy.uuid}")
        ssh.connect(**connect_kwargs)

        _, stdout, stderr = ssh.exec_command(f'mkdir -p {remote_dir}')
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err_msg = stderr.read().decode('utf-8', errors='replace')
            ssh.close()
            return False, f'创建目录失败: {err_msg}'

        sftp = ssh.open_sftp()
        with sftp.open(remote_path, 'w') as f:
            f.write(toml_content)
        sftp.close()

        ssh.close()
        logger.info(f"Deploy success: uuid={deploy.uuid}, path={remote_path}")
        return True, f'部署成功，配置已写入 {remote_path}'

    except ImportError:
        return False, '服务端未安装 paramiko 库，无法执行SSH部署'
    except Exception as e:
        logger.error(f"Deploy failed: uuid={deploy.uuid}, error={e}", exc_info=True)
        return False, f'部署失败: {e}'


def get_website_template_deploys() -> List[dict]:
    """
    获取「网站」类型应用的默认 deploy 配置列表。

    Returns:
        包含 apiserver 和 web 两个默认 deploy 配置的列表
    """
    return [
        {
            'startup_command': 'gunicorn --worker-class gevent --workers 4 --worker-connections 1000 --bind 0.0.0.0:8080 --timeout 60 --keep-alive 5 "main:app"',
            'official_configs': ['app_name', 'domain', 'database', 'alipay', 'oss'],
            'custom_config': '',
        },
        {
            'startup_command': 'python main.py --config config.toml',
            'official_configs': ['app_name'],
            'custom_config': '',
        },
    ]
