#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI 模型 API 网关启动器

根据配置文件构建本地 Docker 镜像并启动网关容器。
参数：
    --config / -c  TOML 配置文件路径，默认 config.toml
"""

import argparse
import logging
import os
import pathlib
import subprocess
import sys

from config_model import GatewayConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

IMAGE_NAME = "ai-task-gateway:latest"
CONTAINER_NAME = "ai-task-gateway"


def ensure_image():
    """确保本地 Docker 镜像存在，不存在则根据 Dockerfile 构建。"""
    result = subprocess.run(
        ["docker", "images", "-q", IMAGE_NAME],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error(f"检查 Docker 镜像失败: {result.stderr.strip()}")
        sys.exit(1)

    if result.stdout.strip():
        logger.info(f"本地镜像 {IMAGE_NAME} 已存在，跳过构建")
        return

    logger.info(f"本地镜像 {IMAGE_NAME} 不存在，开始构建（首次约需 1-2 分钟）...")
    gateway_dir = pathlib.Path(__file__).parent
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, str(gateway_dir)],
    )
    if build.returncode != 0:
        logger.error("Docker 镜像构建失败")
        sys.exit(1)

    logger.info(f"镜像 {IMAGE_NAME} 构建完成")


def stop_existing_container():
    """如果同名容器已存在，停止并删除它。"""
    check = subprocess.run(
        ["docker", "ps", "-a", "-q", "-f", f"name=^{CONTAINER_NAME}$"],
        capture_output=True, text=True,
    )
    if not check.stdout.strip():
        return  # 不存在，无需处理

    running = subprocess.run(
        ["docker", "ps", "-q", "-f", f"name=^{CONTAINER_NAME}$"],
        capture_output=True, text=True,
    )
    if running.stdout.strip():
        logger.info(f"容器 {CONTAINER_NAME} 正在运行，先停止...")
        subprocess.run(["docker", "stop", CONTAINER_NAME], capture_output=True)

    logger.info(f"删除旧容器 {CONTAINER_NAME}")
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


def start_container(cfg: GatewayConfig):
    """根据配置启动网关容器。"""
    stop_existing_container()

    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "--restart", "unless-stopped",
        "-p", f"{cfg.server.port}:{cfg.server.port}",
        "-e", f"GATEWAY_PORT={cfg.server.port}",
        "-e", f"DB_HOST={cfg.database.host}",
        "-e", f"DB_PORT={cfg.database.port}",
        "-e", f"DB_USER={cfg.database.user}",
        "-e", f"DB_PASSWORD={cfg.database.password}",
        "-e", f"DB_NAME={cfg.database.name}",
        IMAGE_NAME,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"启动容器 {CONTAINER_NAME} 失败: {result.stderr.strip()}")
        sys.exit(1)

    container_id = result.stdout.strip()[:12]
    logger.info(f"容器 {CONTAINER_NAME} 启动成功，ID: {container_id}")
    logger.info(f"网关监听端口: {cfg.server.port}")
    logger.info(f"健康检查: curl http://localhost:{cfg.server.port}/health")


def main():
    parser = argparse.ArgumentParser(description='AI 模型 API 网关启动器')
    parser.add_argument('--config', '-c', type=str, default='config.toml',
                        help='TOML 配置文件路径（默认: config.toml）')
    args = parser.parse_args()

    config_path = args.config
    if not os.path.exists(config_path):
        logger.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    cfg = GatewayConfig.from_toml(config_path)
    logger.info(f"已加载配置: port={cfg.server.port}, db={cfg.database.host}:{cfg.database.port}/{cfg.database.name}")

    ensure_image()
    start_container(cfg)


if __name__ == '__main__':
    main()
