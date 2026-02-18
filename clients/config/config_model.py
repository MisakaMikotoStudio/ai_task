#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Client 客户端配置模型定义
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import uuid

from rpc.apiserver_rpc import ApiServerRpc
from config.base_checker import BaseChecker
from utils import git_utils
from agents.base_agent import BaseAgent
from agents import get_agent_by_name

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
        """从 URL 提取仓库名称（用于创建目录等）"""
        return self.get_repo_name_from_url()

    def get_repo_name_from_url(self) -> str:
        """从 URL 中提取仓库名称"""
        import re
        url = self.url
        # 移除 .git 后缀
        if url.endswith('.git'):
            url = url[:-4]
        # 匹配 git@host:path/repo 或 https://host/path/repo
        match = re.search(r'[:/]([^/:]+)$', url)
        if match:
            return match.group(1)
        raise ValueError(f"无法从 URL {url} 中提取仓库名称")

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
        return {
            'name': self.name,
            'default_main_branch': self.default_branch,
            'desc': self.desc
        }
    
    def get_auth_url(self) -> str:
        """获取带认证信息的 URL"""
        url = self.url
        if url.startswith('https://') and self.token:
            url = url.replace('https://', f'https://{self.token}@')
        return url
    
    def get_web_url(self) -> str:
        """获取 web URL"""
        # 移除 .git 后缀（不能用 rstrip，会误删字符）
        def remove_git_suffix(url: str) -> str:
            return url[:-4] if url.endswith('.git') else url
        
        # 转换 git URL 为 web URL
        # git@github.com:owner/repo.git -> https://github.com/owner/repo
        # git@gitlab.com:group/project.git -> https://gitlab.com/group/project
        # https://github.com/owner/repo.git -> https://github.com/owner/repo            
        if self.url.startswith('git@'):
            return remove_git_suffix(self.url.replace(':', '/', 1).replace('git@', 'https://'))
        elif self.url.startswith('https://'):
            return remove_git_suffix(self.url)        
        raise ValueError(f"Git 仓库 {self.name} 地址格式不正确: {self.url}")
    
    def get_path_prefix(self, branch: str) -> str:
        """获取路径前缀，用于拼接文件浏览 URL"""
        base_url = self.get_web_url()
        if "gitlab" in base_url:
            return f"{base_url}/-/blob/{branch}"
        else:
            return f"{base_url}/blob/{branch}"

    def get_mr_url(self, branch: str) -> str:
        """获取 Merge Request URL"""
        base_url = self.get_web_url()
        if "gitlab" in base_url:
            return f"{base_url}/-/merge_requests?scope=all&state=opened&source_branch={branch}"
        else:
            return f"{base_url}/pulls?q=is%3Apr+is%3Aopen+head%3A{branch}"

    def detect_default_branch(self, apiserver_rpc: "ApiServerRpc" = None):
        """检测默认分支
        
        Args:
            apiserver_rpc: API Server RPC 客户端，用于更新远端配置
        """
        # 如果没有配置默认分支，自动获取并更新
        if self.default_branch:
            return
        detected_branch = git_utils.detect_default_branch_from_url(self.get_auth_url())
        if not detected_branch:
            logger.error(f"检测默认分支失败: {self.name} ({self.url})")
            return
        logger.info(f"  检测到默认分支: {detected_branch}")
        self.default_branch = detected_branch
        if apiserver_rpc is None:
            return
        success = apiserver_rpc.update_repo_default_branch(
            repo_id=self.repo_id, default_branch=detected_branch
        )
        if not success:
            logger.error(f"更新默认分支到服务端失败: {self.name} ({self.url})")
        else:
            logger.info(f"更新默认分支到服务端成功: {self.name} ({self.url})")

@dataclass
class ClientConfig:
    """客户端基础配置"""
    apiserver_url: str
    client_id: int
    secret: str
    cache_dir: str = "" # 缓存目录
    instance_uuid: str = str(uuid.uuid4()) # 客户端实例唯一标识
    apiserver_rpc: ApiServerRpc = None
    """Client 客户端后生成的配置"""
    docs_git: Optional[GitRepoConfig] = None # 文档仓库配置
    code_git: List[GitRepoConfig] = field(default_factory=list) # 代码仓库配置
    agent : BaseAgent = None # 客户端 Agent
    agent_name: str = '' # agent 类型名称（原始字符串）
    env_vars: Dict[str, str] = field(default_factory=dict) # 环境变量（key -> value）

    def __init__(self, apiserver_url: str, client_id: int, secret: str) -> None:
        self.apiserver_url = apiserver_url
        self.client_id = client_id
        self.secret = secret
        self.instance_uuid = str(uuid.uuid4())
        self.docs_git = None
        self.code_git = []
        self.agent = None
        self.agent_name = ''
        self.env_vars = {}
        self.apiserver_rpc = ApiServerRpc(base_url=apiserver_url, secret=secret, client_id=client_id, instance_uuid=self.instance_uuid)

    def set_cache_dir(self, cache_dir: str):
        """设置缓存目录"""
        self.cache_dir = cache_dir

    def sync_config(self):
        """同步客户端配置"""
        self.apiserver_rpc = ApiServerRpc(base_url=self.apiserver_url, secret=self.secret, client_id=self.client_id, instance_uuid=self.instance_uuid)
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

        # 解析环境变量（仅 cloud agent 时有效）
        env_vars_list = remote_config.get('env_vars', [])
        self.env_vars = {ev['key']: ev.get('value', '') for ev in env_vars_list if ev.get('key')}

        # 根据配置的 agent 类型获取对应的 Agent 实例
        agent_name = remote_config.get('agent', 'Claude Code')
        self.agent_name = agent_name
        self.agent = get_agent_by_name(agent_name)
        logger.debug(f"使用 Agent: {agent_name}")

        logger.debug(f"客户端配置同步完成")
        logger.debug(f"缓存目录: {self.cache_dir}")
        logger.debug(f"代码仓库数量: {len(self.code_git)}")

    def check_config(self):
        """检查客户端配置"""
        startup_checker = StartupChecker(self)
        if not startup_checker.run_all_checks():
            raise Exception("启动检查失败")


class StartupChecker:
    """启动检查器 - 在客户端启动时执行各项检查"""
    
    def __init__(
        self, 
        config: ClientConfig
    ):
        """
        初始化启动检查器
        
        Args:
            config: 客户端配置
            cache_dir: 缓存目录路径
            client_id: 客户端 ID（用于更新仓库默认分支）
            secret: 用户秘钥（用于 API 认证）
        """
        self.config = config
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.checkers: List[BaseChecker] = []
        
        # 初始化各个检查器
        self._init_checkers()
    
    def _init_checkers(self):
        """初始化所有检查器"""
        from config.api_server_checker import ApiServerChecker
        from config.git_repo_checker import GitRepoChecker
        self.api_server_checker = ApiServerChecker(self.config)
        self.git_repo_checker = GitRepoChecker(self.config)
        self.checkers.extend([
            self.api_server_checker,
            self.git_repo_checker,
        ])
    
    def _collect_messages(self, checker: BaseChecker):
        """收集检查器的错误和警告信息"""
        self.errors.extend(checker.errors)
        self.warnings.extend(checker.warnings)
        checker.clear_messages()

    def run_all_checks(self) -> bool:
        """
        运行所有启动检查

        检查顺序：
        1. 检查 API 服务器是否联通
        2. 检查所有 git 仓库是否可访问（docs_git + code_git）
        3. 检查 Agent 是否可用并具有工具权限（git、bash）

        Returns:
            (是否全部通过, 错误列表, 警告列表)
        """
        logger.info("=" * 50)
        logger.info("开始启动检查...")
        logger.info("=" * 50)

        # 清空之前的错误和警告
        self.errors = []
        self.warnings = []

        for checker in self.checkers:
            logger.info(f"检查 {checker.__class__.__name__}...")
            if not checker.check():
                self._collect_messages(checker)
                return self._finish_checks(False)
            self._collect_messages(checker)
        return self._finish_checks(True)
    
    def _finish_checks(self, passed: bool) -> bool:
        """结束检查并输出结果"""
        logger.info("=" * 50)
        if passed:
            logger.info("启动检查完成：全部通过 ✓")
        else:
            logger.error("启动检查完成：存在错误 ✗")
        
        if self.warnings:
            for warning in self.warnings:
                logger.warning(f"⚠ {warning}")
        
        if self.errors:
            for error in self.errors:
                logger.error(f"✗ {error}")
        
        logger.info("=" * 50)
        
        return passed


# 使用示例
if __name__ == "__main__":
    config = ClientConfig.from_toml("config.toml")
    print(f"API Server: {config.apiserver.url}")
    print(f"Client ID: {config.client.id}")
    print(f"Docs Git: {config.docs_git.url if config.docs_git else None}")
