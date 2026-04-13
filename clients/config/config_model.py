#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Client 客户端配置模型定义
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import uuid

from rpc.apiserver_rpc import ApiServerRpc
from config.base_checker import BaseChecker
from utils import git_utils
from utils import git_utils_advanced
from agents.base_agent import BaseAgent
from agents import get_agent_by_name
from config.api_server_checker import ApiServerChecker
from config.git_repo_checker import GitRepoChecker


logger = logging.getLogger(__name__)


@dataclass
class GitRepoConfig:
    """Git 仓库配置"""
    url: str  # 仓库地址（git@ 或 https://）
    desc: str = ""  # 仓库简介
    token: Optional[str] = None  # 认证 token（https 地址必填）
    default_branch: str = ""  # 主分支名称，空字符串表示未配置
    branch_prefix: str = "ai_"  # 代码分支前缀
    repo_id: Optional[int] = None  # 仓库配置 ID（用于回调更新）

    @property
    def name(self) -> str:
        """从仓库地址中提取仓库名称（用于创建目录等）"""
        return git_utils.get_repo_name_from_url(self.url)
    
    @property
    def auth_url(self) -> str:
        """获取带认证信息的 URL（仅 https 且配置 token 时注入）。"""
        return git_utils.get_auth_url(self.url, self.token)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {k: v for k, v in {
            'url': self.url,
            'desc': self.desc,
            'token': self.token,
            'default_branch': self.default_branch,
            'branch_prefix': self.branch_prefix,
            'repo_id': self.repo_id
        }.items() if v is not None and v != ''}

    def to_simple_intro_dict(self) -> Dict[str, Any]:
        """转换为简单介绍字典"""
        return {'name': self.name, 'default_main_branch': self.default_branch, 'desc': self.desc}
    
    def get_path_prefix(self, branch: str) -> str:
        """获取路径前缀，用于拼接文件浏览 URL。"""
        return git_utils.get_path_prefix(self.url, branch)

    def detect_default_branch(self, apiserver_rpc: "ApiServerRpc" = None):
        """如果没有配置默认分支，自动获取并更新
        
        Args:
            apiserver_rpc: API Server RPC 客户端，用于更新远端配置
        """
        if self.default_branch:
            return
        detected_branch = git_utils_advanced.detect_default_branch_from_url(auth_url=self.auth_url, repo_name=self.name)
        if not detected_branch:
            logger.error(f"检测默认分支失败: {self.name} ({self.url})")
            return
        logger.info(f"  检测到默认分支: {detected_branch}")
        self.default_branch = detected_branch
        success = apiserver_rpc.update_repo_default_branch(repo_id=self.repo_id, default_branch=detected_branch)
        if not success:
            logger.error(f"更新默认分支到服务端失败: {self.name} ({self.url})")
        else:
            logger.info(f"更新默认分支到服务端成功: {self.name} ({self.url})")

@dataclass
class OssConfig:
    """OSS 对象存储配置（STS 临时凭证，限定用户目录访问）"""
    secret_id: str = ""       # tmp_secret_id
    secret_key: str = ""      # tmp_secret_key
    session_token: str = ""   # STS session token
    expired_time: int = 0     # 凭证过期时间（Unix 时间戳）
    region: str = "ap-guangzhou"
    bucket: str = ""
    allow_prefix: str = ""    # 允许访问的路径前缀


@dataclass
class ClientConfig:
    """客户端基础配置"""
    apiserver_url: str
    client_id: int
    secret: str
    workspace: str = "" # 宿主机工作目录路径
    instance_uuid: str = str(uuid.uuid4()) # 客户端实例唯一标识
    apiserver_rpc: ApiServerRpc = None
    """Client 客户端后生成的配置"""
    docs_git: Optional[GitRepoConfig] = None # 文档仓库配置
    code_git: List[GitRepoConfig] = field(default_factory=list) # 代码仓库配置
    agent : BaseAgent = None # 客户端 Agent
    login_user_name: str = '' # 当前登录用户名称（由 apiserver 返回）
    oss: Optional[OssConfig] = None # OSS 配置（从 apiserver 下发）
    """检查器"""
    errors: List[str] = field(default_factory=list) # 错误信息列表
    warnings: List[str] = field(default_factory=list) # 警告信息列表
    checkers: List[BaseChecker] = field(default_factory=list) # 客户端配置检查器列表

    def __init__(self, apiserver_url: str, client_id: int, secret: str, workspace: str = "") -> None:
        self.apiserver_url = apiserver_url
        self.client_id = client_id
        self.secret = secret
        self.workspace = workspace
        self.apiserver_rpc = ApiServerRpc(base_url=apiserver_url, secret=secret, client_id=client_id, instance_uuid=self.instance_uuid)

    def sync_config(self):
        """同步客户端配置"""
        identity_result = git_utils.ensure_git_identity_configured()
        if not identity_result.success:
            logger.warning(f"Git 全局身份配置检查失败，不影响继续运行: {identity_result.message}")

        remote_config = self.apiserver_rpc.get_client_config(self.client_id)
        logger.debug(f"从远程加载客户端配置: client_id={self.client_id}")

        # 解析仓库配置
        repos = remote_config.get('repos', [])
        code_git_list = []

        for repo in repos:
            git_config = GitRepoConfig(
                url=repo.get('url', ''),
                desc=repo.get('desc', ''),
                token=repo.get('token'),
                default_branch=repo.get('default_branch', ''),
                branch_prefix=repo.get('branch_prefix', 'ai_'),
                repo_id=repo.get('id')  # 保存仓库ID，用于更新默认分支
            )
            git_config.detect_default_branch(self.apiserver_rpc)
            code_git_list.append(git_config)
            # 如果是文档仓库（通过 docs_repo 标志判断）
            if repo.get('docs_repo'):
                self.docs_git = git_config
        self.code_git = code_git_list

        # 根据配置的 agent 类型获取对应的 Agent 实例
        self.agent = get_agent_by_name(remote_config.get('agent'))
        logger.debug(f"使用 Agent: {self.agent.name}")

        self.login_user_name = remote_config.get('login_user_name', '')

        logger.debug(f"客户端配置同步完成")
        logger.debug(f"宿主机工作目录: {self.workspace}")
        logger.debug(f"代码仓库数量: {len(self.code_git)}")

    def refresh_oss_sts(self):
        """
        刷新 OSS STS 临时凭证。
        仅在凭证不存在或即将过期（提前 5 分钟）时向 apiserver 请求新凭证。
        """
        if self.oss and self.oss.expired_time > 0:
            remaining = self.oss.expired_time - int(time.time())
            if remaining > 300:
                logger.debug("OSS STS 凭证仍有效, 剩余 %d 秒", remaining)
                return

        try:
            oss_data = self.apiserver_rpc.get_oss_sts(client_id=self.client_id)
            if oss_data and isinstance(oss_data, dict):
                self.oss = OssConfig(
                    secret_id=oss_data.get('tmp_secret_id', ''),
                    secret_key=oss_data.get('tmp_secret_key', ''),
                    session_token=oss_data.get('session_token', ''),
                    expired_time=int(oss_data.get('expired_time', 0)),
                    region=oss_data.get('region', 'ap-guangzhou'),
                    bucket=oss_data.get('bucket', ''),
                    allow_prefix=oss_data.get('allow_prefix', ''),
                )
                logger.info("OSS STS 临时凭证已刷新, allow_prefix=%s, expired_time=%d",
                            self.oss.allow_prefix, self.oss.expired_time)
            else:
                logger.warning("获取 OSS STS 凭证返回为空")
        except Exception as e:
            logger.warning("刷新 OSS STS 临时凭证失败: %s", e)

    def refresh_repo_token(self, repo_config: "GitRepoConfig") -> bool:
        """
        调用 apiserver 刷新仓库的 Installation Access Token，并更新本地配置。

        apiserver 负责生成新 token 并持久化到数据库，
        客户端仅触发刷新并将返回的新 token 更新到内存中的 repo_config。

        Args:
            repo_config: 需要刷新 token 的仓库配置

        Returns:
            是否刷新成功
        """
        if not repo_config.repo_id:
            logger.warning("refresh_repo_token: repo_id 为空，无法刷新")
            return False

        new_token = self.apiserver_rpc.refresh_repo_token(repo_id=repo_config.repo_id)
        if new_token:
            repo_config.token = new_token
            logger.info(
                "refresh_repo_token: 刷新成功, repo_id=%s, url=%s",
                repo_config.repo_id, repo_config.url,
            )
            return True

        logger.warning(
            "refresh_repo_token: 刷新失败, repo_id=%s, url=%s",
            repo_config.repo_id, repo_config.url,
        )
        return False

    def check_config(self):
        """检查客户端配置"""
        self.checkers = [ApiServerChecker(self), GitRepoChecker(self)]
        logger.info("=" * 50)
        logger.info("开始客户端配置检查...")
        logger.info("=" * 50)

        # 清空之前的错误和警告
        for checker in self.checkers:
            if not checker.check():
                return False
        return True

# 使用示例
if __name__ == "__main__":
    config = ClientConfig.from_toml("config.toml")
    print(f"API Server: {config.apiserver.url}")
    print(f"Client ID: {config.client.id}")
    print(f"Docs Git: {config.docs_git.url if config.docs_git else None}")
