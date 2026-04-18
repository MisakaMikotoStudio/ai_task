#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
代码开发节点 - 根据 claude.md 需求开发指引进行实际代码开发
"""

import logging
import os
import traceback
import threading
from typing import Optional, List
from utils import git_utils
from utils import git_workflow_utils
import shutil
import json
from .base_worker import BaseWorker
from config.config_model import GitRepoConfig

logger = logging.getLogger(__name__)


class CodeDevelopWorker(BaseWorker):
    """代码开发工作线程"""
    
    worker_name = "代码开发"
    worker_key = "code_develop"

    # ========== 目录路径属性 ==========  
    @property
    def docs_git(self) -> GitRepoConfig:
        return self.client_config.docs_git
    
    @property
    def code_git(self) -> List[GitRepoConfig]:
        return self.client_config.code_git
 
    @property
    def work_dir(self) -> str:
        """本次客户端的工作空间目录，也是使用claude cli执行的目录"""
        dir_path = os.path.join(self.client_config.workspace, self.worker_key)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        dir_path = os.path.join(dir_path, self.task['key'])
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        return dir_path

    @property
    def git_repo_cache_dir(self) -> str:
        """代码仓库缓存目录（全局共享）"""
        dir_path = os.path.join(self.client_config.workspace, "git_repo_cache")
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        return dir_path

    @property
    def docs_dir(self) -> str:
        """文档仓库中当前任务的目录"""
        dir_path = os.path.join(self.work_dir, self.docs_git.name)
        if not os.path.exists(dir_path):
            raise Exception(f"文档仓库目录 {dir_path} 不存在")
        return dir_path

    # ========== 文件路径属性 ==========
    @property
    def knowledge_file_path(self) -> str:
        """知识库文件路径"""
        return os.path.join(self.work_dir, "knowledge.md")

    @property
    def claude_file_path(self) -> str:
        """claude.md 文件路径"""
        return os.path.join(self.work_dir, "claude.md")

    @property
    def develop_guidance_file_path(self) -> str:
        """开发规范文件路径"""
        return os.path.join(self.work_dir, "develop_guidance.md")

    @property
    def develop_plan_example_file_path(self) -> str:
        """开发计划示例文件路径"""
        return os.path.join(self.work_dir, "develop_plan_example.md")

    @property
    def develop_file_path(self) -> str:
        """开发文档路径（本次任务的执行结果）"""
        return os.path.join(self.docs_dir, 'develop.md')

    @property
    def chat_history_file_path(self) -> str:
        """对话历史文件路径"""
        return os.path.join(self.work_dir, 'chat_history.json')

    # ========== 其他属性 ==========

    @property
    def user_input(self) -> str:
        """用户输入"""
        return self.task.get("chat_messages")[-1].get("input")

    # ========== 执行方法 ==========
    def exception_handler(self, e: Exception):
        """异常处理 - 将错误信息与完整异常栈回传给用户"""
        tb_text = "".join(
            traceback.format_exception(type(e), e, e.__traceback__)
        )
        logger.error(
            f"[{self.trace_id}] 代码开发节点异常: {e}",
            exc_info=(type(e), e, e.__traceback__),
        )
        try:
            task_id = self.task.get("task_id")
            chat_id = self.task.get("chat_id")
            chat_messages = self.task.get("chat_messages")
            if task_id is not None and chat_id and chat_messages:
                message_id = chat_messages[-1].get("id")
                self.client_config.apiserver_rpc.agent_reply_chat_msg(
                    task_id=int(task_id),
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    reply=f"任务执行异常:\n{tb_text}",
                    session_id=self.task.get("session_id"),
                )
        except Exception as notify_err:
            logger.error(f"[{self.trace_id}] 异常回传失败: {notify_err}")

    @property
    def is_standalone(self) -> bool:
        """是否为独立 Chat（task_id=0）"""
        return int(self.task.get('task_id', 0)) == 0

    def before_execute(self):
        """准备执行节点逻辑 - 准备执行节点所需的环境和数据"""
        self._pending_rebases: List[dict] = []

        # 更新消息状态为 running
        message_id = self.task.get("chat_messages")[-1].get("id")
        self.client_config.apiserver_rpc.update_message_status(
            task_id=int(self.task['task_id']),
            chat_id=int(self.task['chat_id']),
            message_id=int(message_id),
            status='running',
        )
        # 代码仓库缓存更新
        for git_repo in self.code_git:
            git_result = git_utils.clone_or_sync_repo(
                work_dir=self.git_repo_cache_dir,
                repo_config=git_repo,
                trace_id=self.trace_id,
            )
            if not git_result.success and git_utils.git_error_is_auth_failure(git_result.message):
                logger.warning(f"[{self.trace_id}] 仓库 {git_repo.name} 认证失败，尝试刷新 token")
                if self.client_config.refresh_repo_token(repo_config=git_repo):
                    git_result = git_utils.clone_or_sync_repo(
                        work_dir=self.git_repo_cache_dir,
                        repo_config=git_repo,
                        trace_id=self.trace_id,
                    )
            if not git_result.success:
                raise Exception(f"代码仓库 {git_repo.name} 准备失败: {git_result.message}")
        # 工作目录仓库同步(优先同步文档仓库)
        git_repos = [self.docs_git] + self.code_git
        if self.is_standalone:
            for git_repo in git_repos:
                self._sync_repo_or_defer(git_repo, sync_type="chat_standalone")
        else:
            for git_repo in git_repos:
                task_ok = self._sync_repo_or_defer(git_repo, sync_type="task")
                if task_ok:
                    self._sync_repo_or_defer(git_repo, sync_type="chat")
                else:
                    # task 分支有冲突，chat 分支也跳过程序化 rebase（依赖 task）
                    self._prepare_repo_dir(git_repo)
                    self._pending_rebases.append({
                        "repo_name": git_repo.name,
                        "repo_dir": os.path.join(self.work_dir, git_repo.name),
                        "dev_branch": self._get_chat_branch_name(git_repo),
                        "default_branch": self._get_task_branch_name(git_repo),
                    })
        # 文档仓库init_docs拷贝到当前目录，如果没有的话，默认使用当前clients目录下的init_docs
        current_file_dir = os.path.dirname(os.path.abspath(__file__))
        default_init_docs_dir = os.path.join(os.path.dirname(current_file_dir), "init_docs")
        shutil.copytree(default_init_docs_dir, self.work_dir, dirs_exist_ok=True)
        if self.docs_git:
            repo_init_docs_dir = os.path.join(self.work_dir, self.docs_git.name, "init_docs")
            if os.path.exists(repo_init_docs_dir):
                shutil.copytree(repo_init_docs_dir, self.work_dir, dirs_exist_ok=True)
        # 对话历史保存到文件中 （除了最后一个消息）
        chat_history = []
        for chat_message in self.task.get("chat_messages")[:-1]:
            chat_history.append({
                "extra": chat_message.get("extra"),
                "user_input": chat_message.get("input"),
                "assistant_output": chat_message.get("output"),
            })
        if chat_history:
            with open(self.chat_history_file_path, "w", encoding="utf-8") as f:
                json.dump(chat_history, f, ensure_ascii=False, indent=4)

    def _collect_branch_merge_requests(self, dev_branch_fn, base_branch_fn, diff_label: str = "分支") -> List[dict]:
        """收集各代码仓库的分支差异信息并创建 PR（公共方法）

        Args:
            dev_branch_fn: 接收 git_repo 返回开发分支名的函数
            base_branch_fn: 接收 git_repo 返回基准分支名的函数
            diff_label: 日志中的分支类型标签（如 "task" / "chat"）
        """
        merge_requests: List[dict] = []
        for git_repo in self.code_git:
            work_repo_dir = os.path.join(self.work_dir, git_repo.name)
            dev_branch = dev_branch_fn(git_repo)
            base_branch = base_branch_fn(git_repo)
            actual_branch = git_workflow_utils.get_current_branch(repo_dir=work_repo_dir, trace_id=self.trace_id) or dev_branch
            diff_result = git_workflow_utils.collect_remote_branch_diff_info(
                repo_dir=work_repo_dir,
                dev_branch=dev_branch,
                main_branch=base_branch,
                trace_id=self.trace_id,
            )
            # 认证失败时调用 apiserver 刷新 token 后重试一次
            if not diff_result.success and git_utils.git_error_is_auth_failure(diff_result.message):
                logger.warning(f"[{self.trace_id}] 仓库 {git_repo.name} collect_diff 认证失败，尝试刷新 token")
                if self.client_config.refresh_repo_token(repo_config=git_repo):
                    git_utils.update_remote_auth_url(work_repo_dir, git_repo.auth_url, trace_id=self.trace_id)
                    diff_result = git_workflow_utils.collect_remote_branch_diff_info(
                        repo_dir=work_repo_dir,
                        dev_branch=dev_branch,
                        main_branch=base_branch,
                        trace_id=self.trace_id,
                    )
            if not diff_result.success:
                logger.warning(f"[{self.trace_id}] 检查 {diff_label} 分支差异失败: repo={git_repo.name}, {diff_result.message}")
                continue
            if diff_result.message == "no_diff":
                continue
            actual_pr_url = git_workflow_utils.create_github_pr_if_not_exists(
                repo_url=git_repo.url,
                token=git_repo.token,
                head_branch=dev_branch,
                base_branch=base_branch,
                pr_title=dev_branch,
                trace_id=self.trace_id,
            )
            merge_requests.append({
                "repo_id": git_repo.repo_id,
                "repo_name": git_repo.name,
                "branch_name": actual_branch,
                "latest_commitId": diff_result.commit_id,
                "merge_url": actual_pr_url or diff_result.merge_url
            })
        return merge_requests

    def after_execute(self):
        """执行完成后，保存任务执行信息"""
        task_id = self.task.get("task_id")
        chat_id = self.task.get("chat_id")
        message_id = self.task.get("chat_messages")[-1].get("id")
        self._commit_and_push_all_git_repos()

        if self.is_standalone:
            # ---- 独立 Chat：chat 分支 vs 默认分支（跳过 task 分支层）----
            docs_chat_branch = self._get_chat_branch_name(self.docs_git)
            develop_chat_doc_url = (
                self.docs_git.get_path_prefix(docs_chat_branch)
                + "/develop.md"
            )
            chat_branch_merge_request = self._collect_branch_merge_requests(
                dev_branch_fn=self._get_chat_branch_name,
                base_branch_fn=lambda repo: repo.default_branch,
                diff_label="chat",
            )
            self.client_config.apiserver_rpc.sync_chat_msg_sync_execute(
                task_id=task_id,
                chat_id=chat_id,
                message_id=message_id,
                develop_doc=develop_chat_doc_url,
                merge_request=chat_branch_merge_request
            )
            return

        # -------------------- Step 1：task 分支 vs 默认主分支 --------------------
        docs_task_branch = self._get_task_branch_name(self.docs_git)
        develop_task_doc_url = (
            self.docs_git.get_path_prefix(docs_task_branch)
            + "/develop.md"
        )
        task_branch_merge_request = self._collect_branch_merge_requests(
            dev_branch_fn=self._get_task_branch_name,
            base_branch_fn=lambda repo: repo.default_branch,
            diff_label="task",
        )
        self.client_config.apiserver_rpc.sync_task_execute(
            task_id=task_id,
            develop_doc=develop_task_doc_url,
            merge_request=task_branch_merge_request
        )

        # -------------------- Step 2：chat 分支 vs task 分支 --------------------
        docs_chat_branch = self._get_chat_branch_name(self.docs_git)
        develop_chat_doc_url = (
            self.docs_git.get_path_prefix(docs_chat_branch)
            + "/develop.md"
        )
        chat_branch_merge_request = self._collect_branch_merge_requests(
            dev_branch_fn=self._get_chat_branch_name,
            base_branch_fn=self._get_task_branch_name,
            diff_label="chat",
        )
        self.client_config.apiserver_rpc.sync_chat_msg_sync_execute(
            task_id=task_id,
            chat_id=chat_id,
            message_id=message_id,
            develop_doc=develop_chat_doc_url,
            merge_request=chat_branch_merge_request
        )

    def execute(self):
        """执行节点逻辑 - 待处理"""
        # 检查最新消息是否携带图片，下载到工作目录
        downloaded_images = self._download_chat_images()

        prompt = self._build_development_prompt()

        # 如果有下载的图片，在 prompt 末尾追加图片信息
        if downloaded_images:
            image_lines = ["\n\n---\n\n## 用户附带的图片\n\n以下图片已下载到当前工作目录：\n"]
            for img in downloaded_images:
                image_lines.append(f"- `{img['local_path']}` (原始文件名: {img['filename']})")
            image_lines.append("\n请在处理用户需求时参考这些图片。")
            prompt += "\n".join(image_lines)

        session_id = self.task.get("session_id") or ""
        reply, session_id = self.run_agent_prompt(cwd=self.work_dir, prompt=prompt, session_id=session_id)

        # 将 agent 输出同步回数据库（output + chat.session_id）
        task_id = self.task.get("task_id")
        chat_id = self.task.get("chat_id")
        message_id = self.task.get("chat_messages")[-1].get("id")
        self.client_config.apiserver_rpc.agent_reply_chat_msg(
            task_id=int(task_id),
            chat_id=int(chat_id),
            message_id=int(message_id),
            reply=reply,
            session_id=session_id,
        )

    def _prepare_repo_dir(self, git_repo: GitRepoConfig):
        """确保工作目录中存在该仓库副本并更新认证 URL"""
        work_repo_dir = os.path.join(self.work_dir, git_repo.name)
        if not os.path.exists(work_repo_dir):
            src_repo_dir = os.path.join(self.git_repo_cache_dir, git_repo.name)
            shutil.copytree(src_repo_dir, work_repo_dir, dirs_exist_ok=True)
        git_utils.update_remote_auth_url(work_repo_dir, git_repo.auth_url, trace_id=self.trace_id)

    def _resolve_sync_branches(self, git_repo: GitRepoConfig, sync_type: str) -> tuple:
        """根据同步类型返回 (dev_branch, default_branch)"""
        if sync_type == "task":
            return self._get_task_branch_name(git_repo), git_repo.default_branch
        elif sync_type == "chat":
            return self._get_chat_branch_name(git_repo), self._get_task_branch_name(git_repo)
        elif sync_type == "chat_standalone":
            return self._get_chat_branch_name(git_repo), git_repo.default_branch
        raise Exception(f"Invalid sync_type: {sync_type}")

    def _sync_repo_or_defer(self, git_repo: GitRepoConfig, sync_type: str) -> bool:
        """程序化 rebase 同步，成功返回 True；冲突时记录到 _pending_rebases 并返回 False；其他错误 raise。"""
        dev_branch, default_branch = self._resolve_sync_branches(git_repo, sync_type)
        self._prepare_repo_dir(git_repo)
        work_repo_dir = os.path.join(self.work_dir, git_repo.name)

        git_result = git_workflow_utils.sync_and_rebase_branch(
            repo_dir=work_repo_dir, dev_branch=dev_branch,
            default_branch=default_branch, trace_id=self.trace_id,
        )
        if not git_result.success and git_utils.git_error_is_auth_failure(git_result.message):
            logger.warning(f"[{self.trace_id}] 仓库 {git_repo.name} sync_and_rebase 认证失败，尝试刷新 token")
            if self.client_config.refresh_repo_token(repo_config=git_repo):
                git_utils.update_remote_auth_url(work_repo_dir, git_repo.auth_url, trace_id=self.trace_id)
                git_result = git_workflow_utils.sync_and_rebase_branch(
                    repo_dir=work_repo_dir, dev_branch=dev_branch,
                    default_branch=default_branch, trace_id=self.trace_id,
                )
        if git_result.success:
            return True
        if 'conflict' not in git_result.message.lower():
            raise Exception(f"{work_repo_dir} 同步并 rebase 失败: {git_result.message}")

        logger.warning(f"[{self.trace_id}] 仓库 {git_repo.name} rebase 冲突，将由 agent 在主流程中解决")
        self._pending_rebases.append({
            "repo_name": git_repo.name,
            "repo_dir": work_repo_dir,
            "dev_branch": dev_branch,
            "default_branch": default_branch,
        })
        return False

    def _download_chat_images(self) -> list:
        """检查最新消息的 extra.images，通过 COS STS 凭证下载到工作目录。
        返回 [{'filename': str, 'local_path': str}]
        """
        chat_messages = self.task.get("chat_messages", [])
        if not chat_messages:
            logger.info(f"[{self.trace_id}] 聊天图片下载: chat_messages 为空，跳过")
            return []

        latest_msg = chat_messages[-1]
        extra = latest_msg.get("extra") or {}
        images = extra.get("images", [])
        logger.info(
            f"[{self.trace_id}] 聊天图片下载: 最新消息 id={latest_msg.get('id')}, "
            f"extra 类型={type(extra).__name__}, extra keys={list(extra.keys()) if isinstance(extra, dict) else 'N/A'}, "
            f"images 数量={len(images)}"
        )
        if not images:
            return []

        # 刷新 OSS STS 凭证（仅在过期时请求）
        self.client_config.refresh_oss_sts()

        # 创建图片保存目录
        images_dir = os.path.join(self.work_dir, "chat_images")
        os.makedirs(images_dir, exist_ok=True)

        downloaded = []
        oss_config = self.client_config.oss

        for img in images:
            oss_path = img.get("oss_path", "")
            filename = img.get("filename", "image")
            if not oss_path:
                logger.warning(f"[{self.trace_id}] 聊天图片下载: 图片缺少 oss_path，跳过 (filename={filename})")
                continue

            # 使用 oss_path 的文件名部分作为本地文件名（保留原始扩展名）
            _, ext = os.path.splitext(oss_path)
            local_filename = filename
            if ext and not filename.lower().endswith(ext.lower()):
                local_filename = filename + ext
            local_path = os.path.join(images_dir, local_filename)

            try:
                if oss_config and oss_config.secret_id and oss_config.bucket:
                    logger.info(f"[{self.trace_id}] 聊天图片下载中: oss_path={oss_path}, local_path={local_path}")
                    self._download_image_from_oss(oss_config=oss_config, oss_path=oss_path, local_path=local_path)
                else:
                    logger.error(f"[{self.trace_id}] OSS STS 凭证不可用，无法下载图片: {oss_path}")
                    continue
                downloaded.append({
                    'filename': filename,
                    'local_path': local_path,
                })
                logger.info(f"[{self.trace_id}] 聊天图片下载成功: {oss_path} -> {local_path}")
            except Exception as e:
                logger.error(f"[{self.trace_id}] 聊天图片下载失败: {oss_path}, error={e}")

        logger.info(f"[{self.trace_id}] 聊天图片下载完成: 共 {len(images)} 张, 成功 {len(downloaded)} 张")
        return downloaded

    def _download_image_from_oss(self, oss_config, oss_path: str, local_path: str):
        """通过 COS SDK + STS 临时凭证直接下载图片"""
        from utils.oss_utils import download_image_to_file
        download_image_to_file(config=oss_config, oss_path=oss_path, local_path=local_path)

    def _build_development_prompt(self) -> str:
        """构建跨多项目开发 prompt"""
        develop_file_exists = os.path.exists(self.develop_file_path)
        knowledge_file_exists = os.path.exists(self.knowledge_file_path)
        guidance_file_exists = os.path.exists(self.develop_guidance_file_path)
        has_chat_history = len(self.task.get("chat_messages", [])) > 1

        sections = []

        # ===== 1. 用户需求（放最前面，让 agent 第一时间理解目标）=====
        sections.append(
            f"# 开发任务\n\n"
            f"## 用户需求\n\n"
            f"{self.user_input}"
        )

        # ===== 2. 工作环境 =====
        if self.is_standalone:
            sections.append(
                f"## 工作环境\n\n"
                f"- **工作目录**: `{self.work_dir}`（非 git 仓库，下面的子文件夹才是独立 git 仓库）\n"
                f"- **文档目录**: `{self.docs_dir}`\n"
                f"- 所有仓库已切换到正确的开发分支\n"
                f"- **模式**: 独立 Chat（不归属特定 Task），chat 分支直接基于默认分支\n"
                f"- **分支命名规则**: chat 分支为 `{{branch_prefix}}0_{{chat_id}}`\n"
                f"- **当前 chat_id**: `{self.task['chat_id']}`\n\n"
                f"### 项目仓库\n\n"
                f"{self._build_repo_info_table_for_prompt()}"
            )
        else:
            sections.append(
                f"## 工作环境\n\n"
                f"- **工作目录**: `{self.work_dir}`（非 git 仓库，下面的子文件夹才是独立 git 仓库）\n"
                f"- **文档目录**: `{self.docs_dir}`\n"
                f"- 所有仓库已切换到正确的开发分支\n"
                f"- **分支命名规则**: task 分支为 `{{branch_prefix}}{{task_id}}`，chat 分支为 `{{branch_prefix}}{{task_id}}_{{chat_id}}`\n"
                f"- **当前 task_id**: `{self.task['task_id']}`，**chat_id**: `{self.task['chat_id']}`\n\n"
                f"### 项目仓库\n\n"
                f"{self._build_repo_info_table_for_prompt()}"
            )

        # ===== 2.5 代码变更提示（commitId 不一致时插入）=====
        code_change_notice = self._build_code_change_notice_for_prompt()
        if code_change_notice:
            sections.append(code_change_notice)

        # ===== 3. 对话历史（存在时强制最先阅读）=====
        if has_chat_history:
            sections.append(
                "## 对话历史\n\n"
                f"历史对话文件：`{self.chat_history_file_path}`（JSON 数组，含 user_input / assistant_output /extra）。\n"
                "**必须在操作任何文件前先读取**，理解上下文引用（如「重试」「改一下」的具体指向）。"
            )

        # ===== 4. 前置阅读 =====
        read_items = []
        if guidance_file_exists:
            read_items.append(
                f"- **开发规范** `{self.develop_guidance_file_path}` — 统一规范（数据库、日志、接口、安全等）"
            )
        if knowledge_file_exists:
            read_items.append(
                f"- **知识库** `{self.knowledge_file_path}` — 项目背景、架构、已有约定"
            )
        if develop_file_exists:
            read_items.append(
                f"- **开发文档** `{self.develop_file_path}` — 已有需求和技术方案"
            )
        if read_items:
            sections.append(
                "## 前置阅读（编码前须完成）\n\n" + "\n".join(read_items)
            )

        # ===== 4.5 Rebase 冲突（有 pending 时插入）=====
        rebase_instructions = self._build_rebase_instructions()
        if rebase_instructions:
            sections.append(rebase_instructions)

        # ===== 5. 强制约束 =====
        constraints = [
            "**禁止切换分支** — 仅当用户需求明确要求合并操作时例外",
            "**禁止在主分支提交** — 用户明确要求合并到默认分支时例外",
            "**需求文档维护** — 更新「需求内容」时可结合历史与现状整理归纳，但不得丢失已确认的需求要点",
            f"**强制产出开发文档** — 任何需求（含咨询/分析）都须记录到 `{self.develop_file_path}`",
        ]
        if guidance_file_exists:
            constraints.append(
                f"**遵守开发规范** — 代码须符合 `{self.develop_guidance_file_path}`，冲突时在文档说明"
            )
        constraints.append(
            "**待确认事项必须返回** — 在开发文档「待确认事项」和最终回复中同时列出"
        )
        numbered_constraints = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(constraints))
        sections.append(f"## 强制约束\n\n{numbered_constraints}")

        # ===== 6. 执行步骤 =====
        step_bodies: List[str] = []

        if self._pending_rebases:
            repo_names = ", ".join(f"`{r['repo_name']}`" for r in self._pending_rebases)
            step_bodies.append(
                f"**解决 Rebase 冲突** — 按照上方「Rebase 冲突解决」章节的指引，"
                f"逐一解决 {repo_names} 的分支冲突并推送，完成前不得进行任何开发"
            )

        if has_chat_history:
            step_bodies.append(
                f"**读取对话历史** — 阅读 `{self.chat_history_file_path}` 全文，完成前不得操作其他文件"
            )

        if guidance_file_exists:
            step_bodies.append(
                f"**读取开发规范** — 阅读 `{self.develop_guidance_file_path}`，后续编码严格遵守"
            )

        if develop_file_exists:
            step_bodies.append(
                f"**更新需求文档** — 将本次需求整合到 `{self.develop_file_path}` 的「需求内容」章节，可结合历史进行整理归纳"
            )
        else:
            step_bodies.append(
                "**了解项目** — 浏览各仓库代码结构，理解现有架构"
            )
            step_bodies.append(
                f"**创建开发文档** — 参照模板 `{self.develop_plan_example_file_path}`，创建 `{self.develop_file_path}`"
            )

        step_bodies.append(
            f"**执行开发或分析** — 按 `{self.develop_file_path}` 中的方案编码；无需编码则完成分析形成结论"
        )
        step_bodies.append(
            f"**同步文档与进度** — 持续更新 `{self.develop_file_path}`：参照模板 `{self.develop_plan_example_file_path}` 补齐缺失章节，更新「开发进度」状态"
        )
        step_bodies.append(
            "**提交并推送** — 对有改动的仓库执行 `git add -A && git commit && git push`，"
            "commit message 用英文概括改动（如 `feat: add email verification for user registration`）"
        )

        numbered_steps = "\n".join(f"{i + 1}. {body}" for i, body in enumerate(step_bodies))
        sections.append(f"## 执行步骤\n\n{numbered_steps}")

        return "\n\n---\n\n".join(sections) + "\n"

    def _build_code_change_notice_for_prompt(self) -> str:
        """检查当前分支 commitId 与最后一条消息的 commitId 是否一致，不一致则生成变更提示"""
        # 从历史消息中找到最近一条包含 merge_request 的已完成消息
        last_commit_map = {}  # repo_name -> latest_commitId
        for msg in reversed(self.task.get("chat_messages", [])[:-1]):
            extra = msg.get("extra") or {}
            merge_request_list = extra.get("merge_request", [])
            if merge_request_list:
                for item in merge_request_list:
                    repo_name = item.get("repo_name")
                    commit_id = item.get("latest_commitId")
                    if repo_name and commit_id:
                        last_commit_map[repo_name] = commit_id
                break  # 只取最近一条有 merge_request 的消息

        if not last_commit_map:
            return ""  # 无历史 commitId 记录，无需比较

        # 获取每个代码仓库当前本地 HEAD commitId
        changed_repos = []
        current_commit_map = {}
        for git_repo in self.code_git:
            work_repo_dir = os.path.join(self.work_dir, git_repo.name)
            result = git_workflow_utils.get_local_head_commit_id(
                repo_dir=work_repo_dir,
                trace_id=self.trace_id,
            )
            if not result.success:
                logger.warning(f"[{self.trace_id}] 获取 {git_repo.name} HEAD commit 失败: {result.message}")
                continue
            current_commit_id = result.commit_id
            current_commit_map[git_repo.name] = current_commit_id

            last_commit_id = last_commit_map.get(git_repo.name)
            if last_commit_id and last_commit_id != current_commit_id:
                changed_repos.append({
                    "repo_name": git_repo.name,
                    "previous_commit_id": last_commit_id,
                    "current_commit_id": current_commit_id,
                })

        if not changed_repos:
            return ""

        # 构建变更提示
        lines = [
            "## 代码变更提示\n",
            "**以下仓库自上次对话以来有代码变更：**\n",
            "| 仓库 | 上次commitId | 当前commitId |",
            "|------|-------------|-------------|",
        ]
        for item in changed_repos:
            lines.append(
                f"| `{item['repo_name']}` | `{item['previous_commit_id'][:12]}` | `{item['current_commit_id'][:12]}` |"
            )
        lines.append("")
        lines.append("各项目当前最新commitId：\n")
        for repo_name, commit_id in current_commit_map.items():
            lines.append(f"- `{repo_name}`: `{commit_id[:12]}`")
        lines.append("")
        lines.append("代码可能已被其他任务修改，请基于当前最新状态开发。")

        return "\n".join(lines)

    def _build_rebase_instructions(self) -> str:
        """根据 _pending_rebases 生成 agent 需要执行的 rebase 冲突解决指令"""
        if not self._pending_rebases:
            return ""

        lines = [
            "## Rebase 冲突解决（必须最先完成）\n",
            "以下仓库的分支 rebase 存在冲突（rebase 已被中止，仓库处于干净状态），",
            "请在开始正式开发前逐一解决。\n",
        ]

        for info in self._pending_rebases:
            lines.append(
                f"### 仓库 `{info['repo_name']}`\n"
                f"- **仓库目录**: `{info['repo_dir']}`\n"
                f"- **开发分支**: `{info['dev_branch']}`\n"
                f"- **目标分支**: `origin/{info['default_branch']}`\n"
            )

        lines.append("### 操作步骤（对每个冲突仓库）\n")
        lines.append(
            "1. 进入仓库目录\n"
            "2. 重新发起 rebase：`git rebase origin/<目标分支>`\n"
            "3. 查看冲突文件：`git status`（查找 \"both modified\" 的文件）\n"
            "4. 逐个解决冲突文件中的冲突标记（`<<<<<<<` / `=======` / `>>>>>>>`）\n"
            "5. 解决后暂存：`git add <已解决的文件>`\n"
            "6. 继续 rebase：`GIT_EDITOR=true git rebase --continue`\n"
            "7. 如果还有冲突，重复步骤 3-6\n"
            "8. rebase 完成后，强制推送：`git push -f origin <开发分支>`\n"
        )
        lines.append(
            "### 冲突解决策略\n"
            "- 优先保留开发分支的修改，同时整合目标分支的新改动\n"
            "- 同一处冲突以开发分支意图为准，但确保代码可正常运行\n"
            "- rebase --continue 时必须使用 `GIT_EDITOR=true` 前缀，避免打开交互式编辑器\n"
            "- 如果冲突无法解决，执行 `git rebase --abort` 回退并在回复中说明"
        )

        return "\n".join(lines)

    def _build_repo_info_table_for_prompt(self) -> str:
        """构建项目仓库信息表，包含目录名、说明、默认分支和当前分支"""
        rows = [
            "| 仓库目录 | 说明 | 默认分支 | 分支前缀 | 当前分支 |",
            "|----------|------|---------|---------|----------|",
        ]
        for repo in self.code_git:
            branch = self._get_chat_branch_name(repo)
            desc = repo.desc or "—"
            default_branch = repo.default_branch or "main"
            rows.append(f"| `{repo.name}` | {desc} | `{default_branch}` | `{repo.branch_prefix}` | `{branch}` |")
        return "\n".join(rows)

    def _get_task_branch_name(self, git_repo: GitRepoConfig) -> str:
        return git_repo.branch_prefix + str(self.task['task_id'])

    def _get_chat_branch_name(self, git_repo: GitRepoConfig) -> str:
        return self._get_task_branch_name(git_repo) + "_" + str(self.task['chat_id'])

    def _commit_and_push_all_git_repos(self):
        """兜底处理：遍历工作目录下所有 git 仓库，自动提交并推送。"""
        from utils.git_workflow_utils import commit_and_push_all_repos
        commit_and_push_all_repos(work_dir=self.work_dir, commit_message="default-commit-msg", trace_id=self.trace_id)

