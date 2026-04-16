#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI任务需求管理系统 - docker方式启动客户端

根据 Dockerfile 构建本地镜像，获取所有客户端配置，并为每个配置启动独立容器。
"""

import argparse
import logging
import os
import pathlib
import subprocess
import sys
import time
import re

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - L%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def _configure_log_level(log_level: str) -> None:
    level_name = (log_level or "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        raise ValueError(f"无效日志等级: {log_level}")
    logging.getLogger().setLevel(level)
    logger.setLevel(level)


def get_image_tag() -> str:
    """
    生成镜像名：tag 使用当前文件所在目录 git 提交哈希前 8 位。
    若当前文件所在目录不是 git 仓库，则回退到 latest，避免脚本直接失败。
    """
    git_dir = pathlib.Path(__file__).resolve().parent
    result = subprocess.run(
        ["git", "rev-parse", "--short=8", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(git_dir),
    )
    return result.stdout.strip()


IMAGE_REPO = "ai_task.yuban.site.cloud"

def ensure_image(image_name: str, image_tag: str):
    """
    确保本地 Docker 镜像存在，若不存在则根据 Dockerfile 构建。
    通过 --build-arg COMMIT_HASH 使 Docker 在 commit 变更时一定重建 COPY 层。
    """
    result = subprocess.run(["docker", "images", "-q", image_name], capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"检查 Docker 镜像失败: {result.stderr.strip()}")
        sys.exit(1)

    if result.stdout.strip():
        logger.debug(f"本地镜像 {image_name} 已存在，跳过构建")
        return

    logger.info(f"本地镜像 {image_name} 不存在，开始构建（这可能需要几分钟）...")
    clients_dir = pathlib.Path(__file__).resolve().parent
    project_root_dir = clients_dir.parent
    dockerfile = clients_dir / "Dockerfile"
    build = subprocess.run(
        [
            "docker", "build",
            "--build-arg", f"COMMIT_HASH={image_tag}",
            "-f", str(dockerfile),
            "-t", image_name,
            str(project_root_dir),
        ]
    )
    if build.returncode != 0:
        logger.error("Docker 镜像构建失败")
        sys.exit(1)

    logger.info(f"镜像 {image_name} 构建完成")


def start_container(
    container_name: str,
    image_name: str,
    client_id: int,
    secret: str,
    apiserver: str,
    workspace: str,
    env_vars: list | None = None,
    log_level: str = "INFO",
):
    """
    为指定云客户端启动 Docker 容器，若容器已存在（运行中或已退出）则跳过。

    Args:
        container_name: 容器名
        client_id: 客户端 ID
        secret: 客户端秘钥
        apiserver: API Server 地址
        workspace: 宿主机工作目录根路径
        env_vars: 环境变量列表 [{key, value}, ...]，用于 docker run -e 注入
    """
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
        "-e", "AI_TASK_CLIENT_RUNNING_TYPE=docker",
        "-v", f"{os.path.abspath(workspace)}:/workspace:rw",
        image_name,
        "python", "/app/clients/main.py",
        "--apiserver", apiserver,
        "--secret", secret,
        "--client-id", str(client_id),
        "--workspace", "/workspace",
        "--log-level", log_level,
    ]

    if env_vars:
        # 注入到容器“进程环境”中，容器内后续启动的所有进程都可读取
        for ev in env_vars:
            key = (ev or {}).get("key")
            if not key:
                continue
            value = (ev or {}).get("value", "")
            cmd[cmd.index(image_name):cmd.index(image_name)] = ["-e", f"{key}={value}"]

    logger.info(f"启动容器 {container_name} 命令: {cmd}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"启动容器 {container_name} 失败: {result.stderr.strip()}")
    else:
        logger.info(f"容器 {container_name} 启动成功，ID: {result.stdout.strip()[:12]}")


def fetch_client_configs(apiserver: str, secret: str, client_ids: list[int] | None = None) -> dict:
    """
    调用客户端配置接口

    Args:
        apiserver: API Server 地址（如 http://localhost:5000）
        secret: admin 用户的秘钥
        client_ids: 当前环境中已存在的客户端 clientId 列表（用于服务端计算 invalid_ids）

    Returns:
        服务端返回 payload（data + invalid_ids）
    """
    url = f"{apiserver.rstrip('/')}/api/open/startup-config"
    headers = {'X-Client-Secret': secret, 'Content-Type': 'application/json'}
    payload_in = {'clientIds': client_ids or []}

    logger.debug(f"请求: {url}, json={payload_in}")
    response = requests.post(url, headers=headers, json=payload_in, timeout=10)
    logger.debug(f"响应: {response.status_code}, {response.text}")

    try:
        payload = response.json()
    except ValueError:
        content_preview = response.text[:200] if response.text else "(空响应)"
        raise RuntimeError(f"响应解析失败: HTTP {response.status_code}, 内容: {content_preview}")

    if response.status_code != 200:
        raise RuntimeError(f"接口返回错误 [{response.status_code}]: {payload.get('message', '未知错误')}")

    return payload


def get_existing_client_containers(prefix: str) -> dict[int, str]:
    """
    扫描当前主机上已存在的客户端容器，仅解析形如 `{prefix}{client_id}_{client_version}` 的容器。

    Returns:
        { client_id: container_name, ... }
    """
    res = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"查询 Docker 容器失败: {(res.stderr or '').strip()}")
        return {}
    names = [line.strip() for line in (res.stdout or "").splitlines() if line.strip()]
    result: dict[int, str] = {}

    for name in names:
        if not name.startswith(prefix):
            continue
        splits = name.split('_')
        if len(splits) < 3:
            logger.warning(f"容器名格式不符合预期，跳过: {name}")
            continue
        try:
            client_id = int(splits[-2])
        except ValueError:
            logger.warning(f"容器名中的 client_id 非数字，跳过: {name}")
            continue
        # 如果同一个 client_id 下存在多个版本容器：
        # docker ps -a 默认按创建时间倒序，新容器通常先出现。
        # 因此保留首次出现的容器，删除后续出现的旧容器，避免误删刚启动的新容器。
        if client_id in result:
            logger.info(f"删除容器 {name}")
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            continue
        result[client_id] = name
    return result


def main():
    parser = argparse.ArgumentParser(description='AI Task Management - docker方式启动客户端')
    parser.add_argument('--apiserver', '-a', type=str, required=True,
                        help='API Server 地址，例如 http://localhost:5000')
    parser.add_argument('--secret', '-s', type=str, required=True,
                        help='客户端认证专用秘钥（X-Client-Secret）')
    parser.add_argument('--workspace', '-w', type=str, required=True,
                        help='宿主机工作目录根路径')
    parser.add_argument('--client_id', '-i', type=int, required=False, default=None,
                        help='指定启动的客户端ID，不指定则启动所有云客户端（仅admin账号才有这个权限）')
    parser.add_argument('--env', type=str, required=False, default='default',
                        help='环境标识（如 test/prod），用于容器名前缀区分环境')
    parser.add_argument('--log-level', '-l', type=str, required=False, default='INFO',
                        help='日志等级，例如 DEBUG/INFO/WARNING/ERROR')
    args = parser.parse_args()
    _configure_log_level(args.log_level)

    container_prefix = f"{args.env}_ai_task_client_"

    os.makedirs(args.workspace, exist_ok=True)
    os.chmod(args.workspace, 0o777)

    # 循环检测当前环境的容器版本，并按最新配置启动/重建
    while True:
        try:
            # 每轮循环固定一个 commit tag，确保镜像名和容器名使用同一版本标识
            image_tag = get_image_tag()
            image_name = f"{IMAGE_REPO}:v{image_tag}"
            # 初始化镜像：根据 Dockerfile 生成本地镜像，若已存在则跳过
            ensure_image(image_name, image_tag)

            existing_client_containers = get_existing_client_containers(container_prefix)
            logger.debug(f"当前环境已存在的客户端容器: {existing_client_containers}")
            query_client_ids = list(existing_client_containers.keys())
            if args.client_id:
                query_client_ids.append(args.client_id)
            payload = fetch_client_configs(args.apiserver, args.secret, query_client_ids)

            # 清理：重新扫描所有前缀命名的容器，删除需要清理的容器
            for client_id in payload.get('invalid_ids', []):
                if client_id not in existing_client_containers:
                    continue
                logger.info(f"删除已失效的客户端容器 {existing_client_containers[client_id]}")
                subprocess.run(["docker", "rm", "-f", existing_client_containers[client_id]], capture_output=True)

            # 启动：按最新 version 确保容器存在
            for config in payload.get('configs', []):
                version = config.get('version')
                client_id = config.get('client_id')
                container_name = f"{container_prefix}{image_tag}_{client_id}_{version}"
                if client_id in existing_client_containers:
                    if existing_client_containers[client_id].strip() == container_name.strip():
                        continue
                    logger.info(f"删除旧容器 {existing_client_containers[client_id]}")
                    subprocess.run(["docker", "rm", "-f", existing_client_containers[client_id]], capture_output=True)
                secret = config.get('secret')
                env_vars = config.get('env_vars') or []
                if secret is None:
                    logger.error(f"client_id={client_id} 缺少 secret，跳过启动")
                    continue
                start_container(
                    container_name=container_name,
                    image_name=image_name,
                    client_id=client_id,
                    secret=secret,
                    apiserver=args.apiserver,
                    workspace=args.workspace + '/' + str(client_id),
                    env_vars=env_vars,
                    log_level=args.log_level,
                )
        except Exception as e:
            logger.error(f"获取客户端配置失败: {e}")
        time.sleep(5)
        continue


if __name__ == '__main__':
    main()
