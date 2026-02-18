#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI任务需求管理系统 - 云客户端启动器

根据 Dockerfile 构建本地镜像，获取所有 cloud 类型客户端配置，并为每个配置启动独立容器。
"""

import argparse
import logging
import os
import pathlib
import subprocess
import sys
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

IMAGE_NAME = "ai_task.yuban.site.cloud:v20260220_1821"


def ensure_image():
    """
    确保本地 Docker 镜像存在，若不存在则根据 Dockerfile 构建。
    """
    result = subprocess.run(
        ["docker", "images", "-q", IMAGE_NAME],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        logger.error(f"检查 Docker 镜像失败: {result.stderr.strip()}")
        sys.exit(1)

    if result.stdout.strip():
        logger.info(f"本地镜像 {IMAGE_NAME} 已存在，跳过构建")
        return

    logger.info(f"本地镜像 {IMAGE_NAME} 不存在，开始构建（这可能需要几分钟）...")
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, "."]
    )
    if build.returncode != 0:
        logger.error("Docker 镜像构建失败")
        sys.exit(1)

    logger.info(f"镜像 {IMAGE_NAME} 构建完成")


def start_container(client_id: int, secret: str, apiserver: str, workspace: str):
    """
    为指定云客户端启动 Docker 容器，若容器已存在（运行中或已退出）则跳过。

    Args:
        client_id: 客户端 ID
        secret: 客户端秘钥
        apiserver: API Server 地址
        workspace: 宿主机工作目录根路径
    """
    container_name = f"ai_task_cloud_client_{client_id}"

    # 检查容器是否已存在（包含运行中和已退出的容器）
    check = subprocess.run(
        ["docker", "ps", "-a", "-q", "-f", f"name=^{container_name}$"],
        capture_output=True, text=True
    )
    if check.stdout.strip():
        # 容器已存在，检查是否在运行
        running = subprocess.run(
            ["docker", "ps", "-q", "-f", f"name=^{container_name}$"],
            capture_output=True, text=True
        )
        if running.stdout.strip():
            # 容器正在运行，跳过
            return

        # 容器已退出，获取退出码判断原因
        inspect = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.ExitCode}}", container_name],
            capture_output=True, text=True
        )
        try:
            exit_code = int(inspect.stdout.strip())
        except ValueError:
            exit_code = -1

        # 137=SIGKILL(docker stop/kill), 143=SIGTERM，视为主动停止 → 删除并重启
        if exit_code in (0, 137, 143):
            logger.info(f"容器 {container_name} 已被停止（exit code={exit_code}），删除后重新启动")
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        else:
            # 非零且非信号退出，视为启动失败 → 保留容器供排查
            logger.warning(
                f"容器 {container_name} 异常退出（exit code={exit_code}），"
                f"跳过重启，可通过 'docker logs {container_name}' 查看原因"
            )
            return

    # 为该客户端创建独立的工作目录，并确保容器内 node 用户（UID=1000）可写
    os.makedirs(workspace, exist_ok=True)
    os.chmod(workspace, 0o777)

    app_dir = pathlib.Path(__file__).parent.parent

    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-e", "CLOUD_AGENT=1",
        "-v", f"{os.path.abspath(workspace)}:/workspace:rw",
        "-v", f"{os.path.abspath(app_dir)}:/app:ro",
        IMAGE_NAME,
        "python", "/app/clients/main.py",
        "--apiserver", apiserver,
        "--secret", secret,
        "--client-id", str(client_id),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"启动容器 {container_name} 失败: {result.stderr.strip()}")
    else:
        logger.info(f"容器 {container_name} 启动成功，ID: {result.stdout.strip()[:12]}")


def fetch_cloud_startup_config(apiserver: str, secret: str) -> list:
    """
    调用云客户端启动配置接口

    Args:
        apiserver: API Server 地址（如 http://localhost:5000）
        secret: admin 用户的秘钥

    Returns:
        云客户端配置列表 [{client_id, secret}, ...]
    """
    url = f"{apiserver.rstrip('/')}/api/cloud/startup-config"
    headers = {
        'X-Client-Secret': secret,
        'Content-Type': 'application/json'
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
    except requests.RequestException as e:
        logger.error(f"请求失败: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"响应解析失败: {e}")
        sys.exit(1)

    if response.status_code != 200:
        logger.error(f"接口返回错误 [{response.status_code}]: {data.get('message', '未知错误')}")
        sys.exit(1)

    return data.get('data', [])


def main():
    parser = argparse.ArgumentParser(description='AI Task Management - 云客户端启动器')
    parser.add_argument('--apiserver', '-a', type=str, required=True,
                        help='API Server 地址，例如 http://localhost:5000')
    parser.add_argument('--secret', '-s', type=str, required=True,
                        help='admin 用户的云客户端专用秘钥（X-Client-Secret）')
    parser.add_argument('--workspace', '-w', type=str, required=True,
                        help='Workspace directory path')
    args = parser.parse_args()

    os.makedirs(args.workspace, exist_ok=True)
    os.chmod(args.workspace, 0o777)

    # 初始化镜像：根据 Dockerfile 生成本地镜像，若已存在则跳过
    ensure_image()

    # 获取所有云客户端配置
    logger.info(f"正在连接 API Server: {args.apiserver}")
    logger.info("获取云客户端启动配置...")

    while True:
        try:
            configs = fetch_cloud_startup_config(args.apiserver, args.secret)
            if not configs:
                logger.info("未找到任何 cloud 类型客户端（或对应用户没有云客户端专用秘钥）")
                continue
            for config in configs:
                start_container(
                    client_id=config['client_id'],
                    secret=config['secret'],
                    apiserver=args.apiserver,
                    workspace=args.workspace + '/' + str(config['client_id']),
                )
        except Exception as e:
            logger.error(f"获取云客户端配置失败: {e}")
        time.sleep(60)
        continue


if __name__ == '__main__':
    main()
