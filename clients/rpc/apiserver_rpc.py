#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ApiServer RPC 调用封装
统一处理认证、重试、错误处理

认证方式：通过 X-Client-Secret 请求头传递用户秘钥
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """任务数据模型"""
    id: int
    key: str
    title: str
    status: str
    status_text: str
    client_id: Optional[int]  # 可为 None，表示未分配客户端
    client_name: Optional[str]
    flow: Dict[str, Any]
    flow_status: str
    created_at: Optional[str]
    updated_at: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Task':
        """从字典创建 Task 对象"""
        return cls(
            id=data.get('id', 0),
            key=data.get('key', ''),
            title=data.get('title', ''),
            status=data.get('status', ''),
            status_text=data.get('status_text', ''),
            client_id=data.get('client_id'),  # 可为 None
            client_name=data.get('client_name'),
            flow=data.get('flow', {}),
            flow_status=data.get('flow_status', ''),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at'),
        )


class ApiServerRpc:
    """ApiServer RPC 客户端（使用 Secret 秘钥认证）"""
    
    def __init__(self, base_url: str, secret: str, client_id: int, instance_uuid: str = None):
        """
        初始化 RPC 客户端

        Args:
            base_url: API 服务器地址
            secret: 用户秘钥（用于 X-Client-Secret 认证）
            client_id: 客户端 ID
            instance_uuid: 客户端实例UUID
        """
        self.base_url = base_url.rstrip('/')
        self.secret = secret
        self.client_id = client_id
        self.instance_uuid = instance_uuid
        self._timeout = 3

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            'Content-Type': 'application/json',
            'traceId': str(uuid.uuid4()),  # 每次请求生成唯一的 traceId
            'X-Client-Secret': self.secret,  # 秘钥认证
            'X-Client-ID': str(self.client_id)  # 客户端ID
        }
        if self.instance_uuid:
            headers['X-Instance-UUID'] = self.instance_uuid
        return headers
    
    def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        network_retry_count: int = 0
    ) -> Dict[str, Any]:
        """
        发送请求（内部方法）
        
        Args:
            method: HTTP 方法
            endpoint: API 端点（如 /api/task）
            json_data: JSON 请求体
            params: URL 查询参数
            network_retry_count: 网络异常重试次数（内部使用）
            
        Returns:
            响应数据
            
        Raises:
            ApiException: API 调用失败
        """
        url = f"{self.base_url}{endpoint}"
        max_network_retries = 10
        
        try:
            headers = self._get_headers()
            logger.debug(f"请求: {method} {endpoint}, json_data={json_data}, params={params}")
            response = requests.request(
                method=method,
                url=url,
                json=json_data,
                params=params,
                headers=headers,
                timeout=self._timeout
            )
            logger.debug(f"响应: {response.status_code}, {response.text}")
            
            # 尝试解析 JSON 响应
            try:
                data = response.json()
            except (ValueError, requests.exceptions.JSONDecodeError) as json_err:
                # JSON 解析失败，记录响应内容用于调试
                content_preview = response.text[:500] if response.text else "(空响应)"
                logger.error(
                    f"JSON 解析失败 [{method}] {endpoint}: {json_err}, "
                    f"请求traceId={headers['traceId']}, "
                    f"HTTP状态码: {response.status_code}, "
                    f"响应内容预览: {content_preview}"
                )
                raise ApiException(
                    response.status_code,
                    f"服务器返回非 JSON 响应: {json_err}"
                )
            
            # 检查业务状态码
            if response.status_code >= 400:
                logger.error(
                    f"API调用失败 [{method}] {url}, "
                    f"params={params}, body={json_data}, "
                    f"HTTP状态码: {response.status_code}, "
                    f"请求traceId={headers['traceId']}, "
                    f"响应: {data.get('message', '请求失败')}"
                )
                raise ApiException(
                    response.status_code,
                    data.get('message', '请求失败')
                )
            
            return data
            
        except requests.RequestException as e:
            # 网络异常重试逻辑：最多重试3次，每次间隔10秒
            if network_retry_count < max_network_retries:
                next_retry = network_retry_count + 1
                sleep_seconds = 10
                logger.warning(
                    f"网络异常 [{method}] {endpoint}: {e}，"
                    f"第 {next_retry}/{max_network_retries} 次重试..."
                )
                time.sleep(sleep_seconds)
                return self._request(
                    method, endpoint, json_data, params,
                    network_retry_count=next_retry
                )
            
            logger.error(f"请求异常 [{method}] {endpoint}: {e}，已达到最大重试次数")
            raise ApiException(0, f"请求异常: {e}")

    def check_health(self):
        """
        检查 API 服务器健康状态

        Returns:
            None
        """
        self._request("GET", "/api/health", network_retry_count=3)
    
    # ==================== 用户相关 API ====================
    
    def get_current_user(self) -> Dict[str, Any]:
        """
        获取当前登录用户信息
        
        Returns:
            用户信息
        """
        return self._request('GET', '/api/user/me')
    
    # ==================== 任务相关 API ====================
        
    
    def get_task(self, task_id: int) -> Optional[Task]:
        """
        获取任务详情
        
        Args:
            task_id: 任务 ID
            
        Returns:
            任务对象，如果不存在返回 None
        """
        result = self._request('GET', f'/api/task/{task_id}')
        data = result.get('data')
        if not data:
            raise ApiException(404, "任务不存在")
        return Task.from_dict(data)

    def update_task_flow(
        self, 
        task_id: int, 
        flow_status: Optional[str] = None, 
        flow: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        更新任务的 flow 状态和 flow 数据（只更新非 None 的字段）
        
        Args:
            task_id: 任务 ID
            flow_status: flow 状态（可选）
            flow: flow 数据（可选）
            
        Returns:
            是否更新成功
        """
        try:
            self._request(
                'PUT',
                f'/api/task/{task_id}/flow',
                json_data={'flow_status': flow_status, 'flow': flow}
            )
            logger.info(f"更新任务 flow 成功: task_id={task_id}, flow_status={flow_status}")
            return True
        except ApiException as e:
            logger.warning(f"更新任务 flow 失败: {e.message}")
            return False

    # ==================== 客户端相关 API ====================

    def get_running_chat_message(self, client_id: int) -> List[Dict[str, Any]]:
        """
        获取需要客户端处理的运行中对话任务列表
        
        Args:
            client_id: 可选，指定客户端 ID 进行筛选（0 表示未分配客户端的任务）
        
        Returns:
            对话任务列表（每项包含 task_id/chat_id/chat_messages）
        """
        result = self._request('GET', f'/api/client/{client_id}/running_chat_message')
        return result.get('data', [])

    def sync_client(self, client_id: int, instance_uuid: str) -> Dict[str, Any]:
        """
        客户端心跳同步
        
        Args:
            client_id: 客户端 ID
            instance_uuid: 客户端实例的唯一标识UUID
            
        Returns:
            同步结果
            
        Raises:
            ApiException: 心跳同步失败（如实例冲突返回409）
        """
        result = self._request(
            'POST', 
            f'/api/client/{client_id}/heartbeat',
            json_data={'instance_uuid': instance_uuid},
            network_retry_count=10
        )
        return result.get('data', {})


    def get_client_config(self, client_id: int) -> Dict[str, Any]:
        """
        获取客户端配置

        Args:
            client_id: 客户端 ID

        Returns:
            客户端配置信息
        """
        result = self._request('GET', f'/api/client/{client_id}/config')
        return result.get('data', {})

    def update_repo_default_branch(
        self, repo_id: int, default_branch: str
    ) -> bool:
        """
        更新仓库的默认主分支

        Args:
            repo_id: 仓库配置 ID
            default_branch: 默认分支名称

        Returns:
            是否更新成功
        """
        try:
            self._request(
                'PATCH',
                f'/api/client/{self.client_id}/repos/{repo_id}/default-branch',
                json_data={'default_branch': default_branch}
            )
            logger.info(f"更新仓库默认分支成功: repo_id={repo_id}, branch={default_branch}")
            return True
        except ApiException as e:
            logger.warning(f"更新仓库默认分支失败: {e.message}")
            return False

    # ==================== 同步执行结果（给客户端写入数据库） ====================
    def sync_task_execute(
        self,
        task_id: int,
        develop_doc: str,
        merge_request: List[Dict[str, Any]],
    ):
        """
        /api/task/sync_execute
        将 task 分支差异与开发文档链接写入 ai_task_tasks.extra
        """
        payload = {
            "task_id": task_id,
            "develop_doc": develop_doc,
            "merge_request": merge_request
        }
        self._request("POST", "/api/task/sync_execute", json_data=payload)

    def sync_chat_msg_sync_execute(
        self,
        task_id: int,
        chat_id: int,
        message_id: int,
        develop_doc: str,
        merge_request: List[Dict[str, Any]],
    ):
        """
        /api/chat/msg/sync_execute
        将 develop_doc/merge_request 写入 ai_task_chat_message.extra
        """
        payload = {
            "task_id": task_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "develop_doc": develop_doc,
            "merge_request": merge_request or []
        }
        self._request("POST", "/api/chat/msg/sync_execute", json_data=payload)

    def update_chat_status(
        self,
        task_id: int,
        chat_id: int,
        status: str,
    ) -> bool:
        """
        /api/chat/update_chat_status
        更新 Chat 状态（running / completed / terminated）
        """
        payload = {
            "task_id": task_id,
            "chat_id": chat_id,
            "status": status,
        }
        try:
            self._request("POST", "/api/chat/update_chat_status", json_data=payload)
            return True
        except ApiException as e:
            logger.warning(f"更新 Chat 状态失败: {e.message}")
            return False

    def update_message_status(
        self,
        task_id: int,
        chat_id: int,
        message_id: int,
        status: str,
    ) -> bool:
        """
        /api/chat/msg/update_message_status
        更新 Message 状态（running / completed / terminated）
        """
        payload = {
            "task_id": task_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "status": status,
        }
        return self._request("POST",f"/api/chat/msg/update_message_status", json_data=payload)

    def agent_reply_chat_msg(
        self,
        task_id: int,
        chat_id: int,
        message_id: int,
        reply: str,
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        """
        /api/chat/msg/agent_reply
        将 agent 执行结果同步回数据库：
        - ai_task_chat_message.output = agent reply
        - ai_task_chat.session_id = agent session_id
        """
        payload = {
            "task_id": task_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "reply": reply,
            "session_id": session_id,
        }
        return self._request("POST", "/api/chat/msg/agent_reply", json_data=payload)


class ApiException(Exception):
    """API 调用异常"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
