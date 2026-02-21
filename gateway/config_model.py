#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网关配置模型 - 使用 dataclass 映射 TOML 配置文件
"""

from dataclasses import dataclass, field

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 及以下


@dataclass
class GatewayServerConfig:
    """网关服务配置"""
    port: int = 10000


@dataclass
class DatabaseConfig:
    """数据库配置（与 apiserver 共用同一个 MySQL）"""
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    name: str = "ai_task"


@dataclass
class GatewayConfig:
    """网关总配置"""
    server: GatewayServerConfig = field(default_factory=GatewayServerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)

    @classmethod
    def from_toml(cls, path: str) -> "GatewayConfig":
        """从 TOML 文件加载配置"""
        with open(path, "rb") as f:
            data = tomllib.load(f)

        return cls(
            server=GatewayServerConfig(**data.get("server", {})),
            database=DatabaseConfig(**data.get("database", {})),
        )
