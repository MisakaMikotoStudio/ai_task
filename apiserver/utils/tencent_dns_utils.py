#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
腾讯云 DNSPod 解析记录工具

用于 test 环境预览子域名的自动 A 记录管理：
- 查询是否已存在匹配 (domain, subdomain, line) 的记录
- 不存在 → CreateRecord
- 已存在但 value/ttl 不一致 → ModifyRecord
- 完全一致 → 跳过

SDK 采用惰性导入：未启用（enabled=False）时无需安装腾讯云 SDK。
"""

import logging
import time

from config_model import TencentDnsConfig

logger = logging.getLogger(__name__)


class TencentDnsError(Exception):
    """DNSPod 调用失败"""


def _build_dnspod_client(config: TencentDnsConfig):
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.dnspod.v20210323 import dnspod_client
    except ImportError as e:
        raise TencentDnsError(
            '未安装腾讯云 DNSPod SDK：pip install '
            'tencentcloud-sdk-python-common tencentcloud-sdk-python-dnspod'
        ) from e

    if not config.secret_id or not config.secret_key:
        raise TencentDnsError('tencent_dns.secret_id / secret_key 未配置')

    cred = credential.Credential(config.secret_id, config.secret_key)
    http_profile = HttpProfile()
    http_profile.endpoint = 'dnspod.tencentcloudapi.com'
    http_profile.reqTimeout = 15
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    return dnspod_client.DnspodClient(cred, '', client_profile)


def _match_zone(fqdn: str, managed_zones: list) -> tuple:
    """把 fqdn 拆成 DNSPod 需要的 (domain, subdomain)，未命中 zone 返回 (None, None)

    - fqdn == zone 时，subdomain = '@'
    - 按「最长后缀」命中，多 zone 情况下不会误匹配父域
    """
    fqdn = (fqdn or '').strip().lower().rstrip('.')
    if not fqdn:
        return None, None

    best_zone = ''
    for zone in managed_zones or []:
        z = (zone or '').strip().lower().rstrip('.')
        if not z:
            continue
        if fqdn == z or fqdn.endswith('.' + z):
            if len(z) > len(best_zone):
                best_zone = z

    if not best_zone:
        return None, None
    if fqdn == best_zone:
        return best_zone, '@'
    return best_zone, fqdn[:-(len(best_zone) + 1)]


def _describe_records(client, domain: str, subdomain: str, record_type: str = 'A') -> list:
    """按 (domain, subdomain, type) 查询记录；SDK 用「无记录」错误反馈空列表时归一化为 []"""
    from tencentcloud.dnspod.v20210323 import models

    req = models.DescribeRecordListRequest()
    req.Domain = domain
    req.Subdomain = subdomain
    req.RecordType = record_type
    try:
        resp = client.DescribeRecordList(req)
    except Exception as e:
        msg = str(e)
        # DNSPod 对「子域名无任何记录」会抛 ResourceNotFound.NoDataOfRecord；
        # 视作空列表，后续走 CreateRecord 路径。
        if 'NoDataOfRecord' in msg or 'RecordNotFound' in msg or 'ResourceNotFound' in msg:
            return []
        raise TencentDnsError(f'DescribeRecordList failed: {e}') from e
    return list(resp.RecordList or [])


def _create_record(client, domain: str, subdomain: str, value: str, ttl: int, line: str) -> None:
    from tencentcloud.dnspod.v20210323 import models

    req = models.CreateRecordRequest()
    req.Domain = domain
    req.SubDomain = subdomain
    req.RecordType = 'A'
    req.RecordLine = line
    req.Value = value
    req.TTL = ttl
    try:
        client.CreateRecord(req)
    except Exception as e:
        raise TencentDnsError(f'CreateRecord failed: {e}') from e


def _modify_record(
    client, domain: str, record_id: int, subdomain: str, value: str, ttl: int, line: str,
) -> None:
    from tencentcloud.dnspod.v20210323 import models

    req = models.ModifyRecordRequest()
    req.Domain = domain
    req.RecordId = record_id
    req.SubDomain = subdomain
    req.RecordType = 'A'
    req.RecordLine = line
    req.Value = value
    req.TTL = ttl
    try:
        client.ModifyRecord(req)
    except Exception as e:
        raise TencentDnsError(f'ModifyRecord failed: {e}') from e


def is_configured(config: "TencentDnsConfig | None") -> bool:
    """判断 DNSPod 是否已完成可用配置（凭据 + 至少一个托管域）。"""
    if not config:
        return False
    if not config.secret_id or not config.secret_key:
        return False
    return bool(config.managed_zones)


def ensure_a_record(
    config: TencentDnsConfig, fqdn: str, ip: str, trace_id: str = '',
) -> str:
    """
    确保 DNSPod 上存在 fqdn → ip 的 A 记录。

    Returns:
        'unconfigured' — 未配置凭据或 managed_zones，跳过
        'unmanaged'    — fqdn 不在 managed_zones 内，跳过
        'reused'       — 记录已存在且 value/TTL 一致
        'modified'     — 找到已有记录但 value/TTL 不同，做了更新
        'created'      — 新建记录
    Raises:
        TencentDnsError: 任意 DNSPod API 调用失败
    """
    if not is_configured(config=config):
        return 'unconfigured'

    fqdn = (fqdn or '').strip().rstrip('.')
    ip = (ip or '').strip()
    if not fqdn or not ip:
        return 'unmanaged'

    domain, subdomain = _match_zone(fqdn=fqdn, managed_zones=config.managed_zones or [])
    if not domain:
        logger.info(
            '[trace_id=%s] tencent_dns: skip fqdn=%s (not in managed_zones=%s)',
            trace_id, fqdn, config.managed_zones,
        )
        return 'unmanaged'

    client = _build_dnspod_client(config=config)
    # DNSPod 免费套餐 TTL 下限 600；低于 60 一律按 60 处理避免无效请求
    ttl = max(60, int(config.ttl or 600))
    line = (config.record_line or '默认').strip() or '默认'

    records = _describe_records(client=client, domain=domain, subdomain=subdomain)
    match = None
    for rec in records:
        if (getattr(rec, 'Line', '') or '') == line:
            match = rec
            break
    if match is None and records:
        match = records[0]  # 忽略线路差异，至少修正主记录

    if match is not None:
        cur_value = (getattr(match, 'Value', '') or '').strip()
        cur_ttl = int(getattr(match, 'TTL', 0) or 0)
        if cur_value == ip and cur_ttl == ttl:
            logger.info(
                '[trace_id=%s] tencent_dns: record reused, domain=%s subdomain=%s value=%s ttl=%s',
                trace_id, domain, subdomain, ip, ttl,
            )
            return 'reused'
        _modify_record(
            client=client, domain=domain, record_id=match.RecordId,
            subdomain=subdomain, value=ip, ttl=ttl, line=line,
        )
        logger.info(
            '[trace_id=%s] tencent_dns: record modified, domain=%s subdomain=%s '
            'value=%s (was %s) ttl=%s',
            trace_id, domain, subdomain, ip, cur_value or '<empty>', ttl,
        )
        return 'modified'

    _create_record(
        client=client, domain=domain, subdomain=subdomain, value=ip, ttl=ttl, line=line,
    )
    logger.info(
        '[trace_id=%s] tencent_dns: record created, domain=%s subdomain=%s value=%s ttl=%s',
        trace_id, domain, subdomain, ip, ttl,
    )
    return 'created'


def ensure_a_records_for_fqdns(
    config: TencentDnsConfig, fqdns: list, ip: str, trace_id: str = '',
) -> dict:
    """批量 upsert 多个 fqdn（共用同一 IP）。

    记录变更（created/modified）后按 config.propagation_wait_seconds 统一等待一次，
    让从 NS 同步完成后再继续 certbot，避免立刻 HTTP-01 查询 NXDOMAIN。

    Returns:
        {fqdn: status} 映射；状态取值同 ensure_a_record。未配置时对所有 fqdn 返回
        'unconfigured'，保持调用方处理一致。
    """
    results: dict[str, str] = {}
    if not is_configured(config=config):
        return {fqdn: 'unconfigured' for fqdn in (fqdns or [])}

    any_changed = False
    for fqdn in fqdns or []:
        status = ensure_a_record(config=config, fqdn=fqdn, ip=ip, trace_id=trace_id)
        results[fqdn] = status
        if status in ('created', 'modified'):
            any_changed = True

    if any_changed:
        wait_s = max(0, int(getattr(config, 'propagation_wait_seconds', 0) or 0))
        if wait_s:
            logger.info(
                '[trace_id=%s] tencent_dns: waiting %ss for propagation after changes=%s',
                trace_id, wait_s,
                {k: v for k, v in results.items() if v in ('created', 'modified')},
            )
            time.sleep(wait_s)

    return results
