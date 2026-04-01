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
import shutil
import time
import re
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
            if task_id and chat_id and chat_messages:
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

    def before_execute(self):
        """准备执行节点逻辑 - 准备执行节点所需的环境和数据"""
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
            if not git_result.success:
                raise Exception(f"代码仓库 {git_repo.name} 准备失败: {git_result.message}")
        # 工作目录仓库同步(优先同步文档仓库)
        git_repos = [self.docs_git] + self.code_git
        for git_repo in git_repos:
            self._sync_repo(git_repo, type="task") # task分支rebase主分支
            self._sync_repo(git_repo, type="chat") # chat分支rebase task分支
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
                "user_input": chat_message.get("input"),
                "assistant_output": chat_message.get("output"),
            })
        if chat_history:
            with open(self.chat_history_file_path, "w", encoding="utf-8") as f:
                json.dump(chat_history, f, ensure_ascii=False, indent=4)

    def after_execute(self):
        """执行完成后，保存任务执行信息"""
        task_id = self.task.get("task_id")
        chat_id = self.task.get("chat_id")
        message_id = self.task.get("chat_messages")[-1].get("id")

        # -------------------- Step 1：task 分支 vs 默认主分支 --------------------
        docs_task_branch = self._get_task_branch_name(self.docs_git)
        develop_task_doc_url = (
            self.docs_git.get_path_prefix(docs_task_branch)
            + "/develop.md"
        )

        task_branch_merge_request: List[dict] = []
        for git_repo in self.code_git:
            work_repo_dir = os.path.join(self.work_dir, git_repo.name)
            task_branch = self._get_task_branch_name(git_repo)
            diff_result = git_utils.collect_remote_branch_diff_info(
                repo_dir=work_repo_dir,
                dev_branch=task_branch,
                main_branch=git_repo.default_branch,
                trace_id=self.trace_id,
            )
            if not diff_result.success:
                logger.warning(f"[{self.trace_id}] 检查 task 分支差异失败: repo={git_repo.name}, {diff_result.message}")
                continue
            if diff_result.message == "no_diff":
                continue
            task_branch_merge_request.append({
                "repo_name": git_repo.name,
                "branch_name": task_branch,
                "latest_commitId": diff_result.commit_id,
                "merge_url": diff_result.merge_url
            })

        self.client_config.apiserver_rpc.sync_task_execute(
            task_id=task_id,
            develop_doc=develop_task_doc_url,
            merge_request=task_branch_merge_request
        )

        # -------------------- Step 3：chat 分支 vs task 分支 --------------------
        docs_chat_branch = self._get_chat_branch_name(self.docs_git)
        develop_chat_doc_url = (
            self.docs_git.get_path_prefix(docs_chat_branch)
            + "/develop.md"
        )
        chat_branch_merge_request: List[dict] = []
        for git_repo in self.code_git:
            work_repo_dir = os.path.join(self.work_dir, git_repo.name)
            task_branch = self._get_task_branch_name(git_repo)
            chat_branch = self._get_chat_branch_name(git_repo)
            diff_result = git_utils.collect_remote_branch_diff_info(
                repo_dir=work_repo_dir,
                dev_branch=chat_branch,
                main_branch=task_branch,
                trace_id=self.trace_id,
            )
            if not diff_result.success:
                logger.warning(f"[{self.trace_id}] 检查 chat 分支差异失败: repo={git_repo.name}, {diff_result.message}")
                continue
            if diff_result.message == "no_diff":
                continue

            chat_branch_merge_request.append({
                "repo_name": git_repo.name,
                "branch_name": chat_branch,
                "latest_commitId": diff_result.commit_id,
                "merge_url": diff_result.merge_url
            })

        self.client_config.apiserver_rpc.sync_chat_msg_sync_execute(
            task_id=task_id,
            chat_id=chat_id,
            message_id=message_id,
            develop_doc=develop_chat_doc_url,
            merge_request=chat_branch_merge_request
        )

    def execute(self):
        """执行节点逻辑 - 待处理"""
        prompt = self._build_development_prompt()
        session_id = self.task.get("session_id") or ""
        reply, session_id = self.agent.run_prompt(
            trace_id=self.trace_id,
            cwd=self.work_dir,
            prompt=prompt,
            session_id=session_id,
        )

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

    # git 仓库的rebase同步, type=task 代表task执行分支rebase主分支，type=chat 代表chat执行分支rebase task分支
    def _sync_repo(self, git_repo: GitRepoConfig, type="task"):
        if type == "task":
            dev_branch = self._get_task_branch_name(git_repo)
            default_branch = git_repo.default_branch
        elif type == "chat":
            dev_branch = self._get_chat_branch_name(git_repo)
            default_branch = self._get_task_branch_name(git_repo)
        else:
            raise Exception(f"Invalid type: {type}")
        work_repo_dir = os.path.join(self.work_dir, git_repo.name)
        if not os.path.exists(work_repo_dir):
            src_repo_dir = os.path.join(self.git_repo_cache_dir, git_repo.name)
            shutil.copytree(src_repo_dir, work_repo_dir, dirs_exist_ok=True)
        git_utils.update_remote_auth_url(
            work_repo_dir,
            git_repo.auth_url,
            trace_id=self.trace_id,
        )
        git_result = git_utils.sync_and_rebase_branch(
            repo_dir=work_repo_dir,
            dev_branch=dev_branch,
            default_branch=default_branch,
            trace_id=self.trace_id,
        )
        if git_result.success:
            return
        if 'conflict' not in git_result.message.lower():
            raise Exception(f"{work_repo_dir} 同步并 rebase 失败: {git_result.message}")

        """Agent 处理 rebase 冲突（注意：rebase 已被 abort，需要重新发起）"""
        develop_ctx = ""
        if os.path.exists(self.develop_file_path):
            develop_ctx = f"\n当前分支的开发内容文档位于 `{self.develop_file_path}`，请阅读该文档以理解本分支的修改意图，优先保留本分支的开发内容。"

        prompt = f"""# Rebase 冲突解决

## 背景
当前在仓库 `{work_repo_dir}` 的开发分支 `{dev_branch}` 上。
该分支需要 rebase 到主分支 `origin/{default_branch}`，但存在冲突（rebase 已被中止，仓库当前处于干净状态）。{develop_ctx}

## 冲突解决策略
- 优先保留本开发分支 `{dev_branch}` 的修改内容
- 同时整合主分支 `{default_branch}` 的新改动，确保不丢失主分支的新增功能
- 如果两边修改了同一处逻辑，以本分支的开发意图为准，但要确保代码能正常运行

## 操作步骤
1. 重新发起 rebase：`git rebase origin/{default_branch}`
2. 查看冲突文件：`git status`（查找 "both modified" 的文件）
3. 逐个打开冲突文件，分析冲突标记（`<<<<<<<`、`=======`、`>>>>>>>`），按上述策略解决
4. 解决后暂存：`git add <已解决的文件>`
5. 继续 rebase：`GIT_EDITOR=true git rebase --continue`
6. 如果还有冲突，重复步骤 2-5
7. rebase 完成后，强制推送：`git push -f origin {dev_branch}`

## 注意事项
- 如果冲突无法解决，执行 `git rebase --abort` 回退
- rebase --continue 时必须使用 `GIT_EDITOR=true` 前缀，避免打开交互式编辑器"""

        _reply, _session_id = self.client_config.agent.run_prompt(
            trace_id=self.trace_id,
            cwd=work_repo_dir,
            prompt=prompt,
        )
        # 通过检查 rebase 状态判断成功与否，而非依赖 agent 的文本输出
        rebase_in_progress = os.path.exists(os.path.join(work_repo_dir, '.git', 'rebase-merge')) or \
                             os.path.exists(os.path.join(work_repo_dir, '.git', 'rebase-apply'))
        if rebase_in_progress:
            import subprocess
            subprocess.run(['git', 'rebase', '--abort'], cwd=work_repo_dir, timeout=30, capture_output=True)
            raise Exception(f"Agent 未能完成 rebase 冲突解决，已自动 abort")

    def _build_development_prompt(self) -> str:
        """构建跨多项目开发 prompt"""
        develop_file_exists = os.path.exists(self.develop_file_path)
        knowledge_file_exists = os.path.exists(self.knowledge_file_path)
        has_chat_history = len(self.task.get("chat_messages", [])) > 1

        sections = []

        # ===== 1. 用户需求（放最前面，让 agent 第一时间理解目标）=====
        sections.append(
            f"# 开发任务\n\n"
            f"## 用户需求\n\n"
            f"{self.user_input}"
        )

        # ===== 2. 工作环境 =====
        sections.append(
            f"## 工作环境\n\n"
            f"- **工作目录**: `{self.work_dir}`\n"
            f"- **文档目录**: `{self.docs_dir}`\n"
            f"- 工作目录下每个子文件夹是一个独立 git 仓库，所有仓库已切换到正确的开发分支\n\n"
            f"### 项目仓库\n\n"
            f"{self._build_repo_info_table_for_prompt()}"
        )

        # ===== 3. 对话历史（存在时强制最先阅读，避免模型直接扫仓库/文档目录）=====
        if has_chat_history:
            sections.append(
                "## 对话历史（必须最先完成）\n\n"
                f"除当前这条「用户需求」外，先前轮次的对话已写入 `{self.chat_history_file_path}`（JSON 数组，"
                f"每项含 `user_input`、`assistant_output`，按时间顺序）。\n\n"
                "**在你用工具读取任何其它文件、列举目录、搜索或浏览任一仓库代码之前**，必须先读取该文件全文，"
                "理清历史语境（例如用户说「重试」「改一下」时具体所指），再结合本轮用户需求理解任务；"
                "完成后再读下方「前置阅读」中的其余材料并执行后续步骤。"
            )

        # ===== 4. 前置阅读（对话历史条目与上一节呼应，便于扫清单时不遗漏）=====
        read_items = []
        if knowledge_file_exists:
            read_items.append(
                f"- **知识库** `{self.knowledge_file_path}` — 项目背景、架构设计、已有约定"
            )
        if develop_file_exists:
            read_items.append(
                f"- **开发文档** `{self.develop_file_path}` — 已有的需求描述和技术方案"
            )
        if read_items:
            preface = (
                "## 前置阅读（编码前须完成）\n\n"
            )
            sections.append(preface + "\n".join(read_items))

        # ===== 5. 强制约束 =====
        sections.append(
            "## 强制约束\n\n"
            "1. **禁止切换或新建 git 分支** — 所有仓库已在正确的开发分支，直接在当前分支开发\n"
            "2. **禁止在主分支（main/master）上提交任何变更**\n"
            "3. **需求累积记录** — 更新开发文档「需求内容」章节时追加新内容，不得覆盖或删除已有需求\n"
            f"4. **强制产出开发文档** — 无论用户需求是否涉及实际代码改动（例如咨询、介绍、分析类问题），都必须创建或更新 `{self.develop_file_path}`，并将本次需求、分析过程、执行结论完整记录到文档中"
        )

        # ===== 6. 执行步骤（先拼步骤列表再统一编号，避免 develop_file × chat_history 四分支重复）=====
        commit_step = (
            "**提交并推送变更** — 开发完成后，对每个有改动的仓库执行 `git add -A && git commit && git push`，"
            "commit message 用英文简要概括本次改动内容（不要使用固定模板，根据实际修改编写），"
            "格式示例：`feat: add email verification for user registration`"
        )
        sync_doc_step = (
            f"**同步文档** — 开发或分析过程中如有调整，同步更新 `{self.develop_file_path}`；"
            f"对照模板 `{self.develop_plan_example_file_path}` 检查章节与结构，缺失则补齐、明显偏离则酌情调整，"
            f"确保最终文档完整反映本次执行过程与结果"
        )
        exec_step = (
            f"**执行开发或分析** — 按 `{self.develop_file_path}` 中的技术方案进行编码；"
            f"若无需编码，则完成必要的项目分析并形成结论"
        )

        step_bodies: List[str] = []
        if has_chat_history:
            step_bodies.append(
                f"**读取对话历史** — 打开并阅读 `{self.chat_history_file_path}` 全文，结合上文「用户需求」理解任务；"
                f"未完成前不得浏览各仓库或读取知识库/开发文档。"
            )
        if develop_file_exists:
            step_bodies.append(
                f"**更新需求文档** — 在 `{self.develop_file_path}` 的「需求内容」章节追加本次用户需求（即使是咨询/介绍类需求也必须记录）"
            )
        else:
            step_bodies.append(
                "**了解项目** — 浏览各仓库代码结构，理解现有架构和代码组织"
            )
            step_bodies.append(
                f"**创建开发文档** — 参照模板 `{self.develop_plan_example_file_path}`，"
                f"创建 `{self.develop_file_path}`，完整记录用户需求和技术方案"
            )
        step_bodies.append(exec_step)
        step_bodies.append(sync_doc_step)
        step_bodies.append(commit_step)

        numbered = "\n".join(f"{i + 1}. {body}" for i, body in enumerate(step_bodies))
        sections.append(f"## 执行步骤\n\n{numbered}")

        return "\n\n---\n\n".join(sections) + "\n"

    def _build_repo_info_table_for_prompt(self) -> str:
        """构建项目仓库信息表，包含目录名、说明和当前分支"""
        rows = [
            "| 仓库目录 | 说明 | 当前分支 |",
            "|----------|------|----------|",
        ]
        for repo in self.code_git:
            branch = self._get_chat_branch_name(repo)
            desc = repo.desc or "—"
            rows.append(f"| `{repo.name}` | {desc} | `{branch}` |")
        return "\n".join(rows)

    def _get_task_branch_name(self, git_repo: GitRepoConfig) -> str:
        return git_repo.branch_prefix + str(self.task['task_id'])
    
    def _get_chat_branch_name(self, git_repo: GitRepoConfig) -> str:
        return self._get_task_branch_name(git_repo) + "_" + str(self.task['chat_id'])