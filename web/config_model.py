#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web 前端服务配置模型定义
"""

from dataclasses import dataclass, field
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 及以下


@dataclass
class ServerConfig:
    """前端服务器配置"""
    host: str = "0.0.0.0"
    port: int = 8080
    url_prefix: str = ""  # URL 前缀，例如 "/ai_task_web"，为空则根路径访问


@dataclass
class ApiServerConfig:
    """后端 API 服务器配置"""
    host: str = ""  # 后端主机地址，例如 "http://127.0.0.1:8105"
    path_prefix: str = "/api"  # 路径前缀，默认 /api


@dataclass
class WebConfig:
    """Web 前端总配置"""
    server: ServerConfig = field(default_factory=ServerConfig)
    apiserver: ApiServerConfig = field(default_factory=ApiServerConfig)

    @classmethod
    def from_toml(cls, path: str) -> "WebConfig":
        """从 TOML 文件加载配置"""
        with open(path, "rb") as f:
            data = tomllib.load(f)

        return cls(
            server=ServerConfig(**data.get("server", {})),
            apiserver=ApiServerConfig(**data.get("apiserver", {})),
        )


# 使用示例
if __name__ == "__main__":
    config = WebConfig.from_toml("config.toml")
    print(f"Web Server: {config.server.host}:{config.server.port}")
    print(f"API Server: {config.apiserver.host}{config.apiserver.path_prefix}")
