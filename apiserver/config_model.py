#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
配置模型定义 - 使用 dataclass 映射配置文件
"""

from dataclasses import dataclass, field
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 及以下


@dataclass
class ServerConfig:
    """服务器配置"""
    host: str = "0.0.0.0"
    port: int = 8105
    debug: bool = False
    url_prefix: str = ""  # URL 前缀，例如 "/v1"，为空则不添加前缀
    workers: int = 1  # Gunicorn worker 进程数（单 worker + gevent 协程即可应对高并发 I/O）
    timeout: int = 120  # Gunicorn worker 超时时间（秒）
    worker_class: str = "gevent"  # Gunicorn worker 类型：gevent（协程）/ sync（同步）/ gthread（多线程）


@dataclass
class DatabaseConfig:
    """数据库配置（MySQL）"""
    type: str = "mysql"
    url: str = "127.0.0.1"
    port: int = 3306
    username: str = "root"
    password: str = ""
    database: str = "ai_task"
    
    def get_connection_url(self) -> str:
        """获取数据库连接URL"""
        return f"mysql+pymysql://{self.username}:{self.password}@{self.url}:{self.port}/{self.database}"


@dataclass
class HeartbeatConfig:
    """心跳保活配置"""
    timeout_seconds: int = 10  # 心跳超时阈值（秒），超过该时间视为客户端离线


@dataclass
class AlipayConfig:
    """支付宝配置"""
    app_id: str = ""
    app_private_key: str = ""      # RSA2 私钥（PKCS8 格式，不含头尾）
    alipay_public_key: str = ""    # 支付宝公钥（不含头尾）
    notify_url: str = ""           # 异步通知回调 URL（公网可访问）
    return_url: str = ""           # 同步返回 URL（支付完成后跳转）
    gateway: str = "https://openapi.alipay.com/gateway.do"
    sandbox: bool = False          # 沙箱模式
    app_encrypt_key: str = ""      # AES 内容加密密钥（Base64，使用获取会员手机号等能力时必填）


@dataclass
class OssConfig:
    """对象存储配置（腾讯云 COS）"""
    enabled: bool = False
    secret_id: str = ""
    secret_key: str = ""
    region: str = "ap-guangzhou"
    bucket: str = ""
    base_url: str = ""             # 公开访问域名前缀，如 https://xxx.cos.ap-guangzhou.myqcloud.com


@dataclass
class DefaultDatabaseConfig:
    """默认数据库实例配置（用于为用户应用自动创建数据库）"""
    url: str = "127.0.0.1"
    port: int = 3306
    admin_username: str = "root"       # 拥有 CREATE DATABASE / CREATE USER 权限的管理员账号
    admin_password: str = ""
    app_username: str = ""             # 为应用创建的数据库分配的访问账号
    app_password: str = ""             # 为应用创建的数据库分配的访问密码


@dataclass
class AppConfig:
    """应用总配置"""
    server: ServerConfig = field(default_factory=ServerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    alipay: AlipayConfig = field(default_factory=AlipayConfig)
    oss: OssConfig = field(default_factory=OssConfig)
    default_database: DefaultDatabaseConfig = field(default_factory=DefaultDatabaseConfig)

    @classmethod
    def from_toml(cls, path: str) -> "AppConfig":
        """从 TOML 文件加载配置"""
        with open(path, "rb") as f:
            data = tomllib.load(f)

        return cls(
            server=ServerConfig(**data.get("server", {})),
            database=DatabaseConfig(**data.get("database", {})),
            heartbeat=HeartbeatConfig(**data.get("heartbeat", {})),
            alipay=AlipayConfig(**data.get("alipay", {})),
            oss=OssConfig(**data.get("oss", {})),
            default_database=DefaultDatabaseConfig(**data.get("default_database", {})),
        )


# 使用示例
if __name__ == "__main__":
    config = AppConfig.from_toml("config.toml")
    print(f"Server: {config.server.host}:{config.server.port}")
    print(f"Database: {config.database.type} @ {config.database.url}:{config.database.port}/{config.database.database}")
