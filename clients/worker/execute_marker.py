#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务工作目录内的「最后完成一轮执行」时间戳。

使用隐藏文件名避免与仓库内普通文档混淆；内容为单行 RFC 3339 UTC 时间
（与 ISO 8601 常见写法一致），便于人工排查与脚本解析。
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 与客户端删除目录逻辑共用此文件名
LAST_EXECUTE_MARKER_NAME = ".ai_task_last_execute"


def write_last_execute_marker(work_dir: str) -> None:
    """在 work_dir 写入或覆盖最后执行完成时间（UTC）。失败只打日志，不抛出。"""
    try:
        os.makedirs(work_dir, exist_ok=True)
    except OSError as e:
        logger.warning(f"无法确保任务工作目录存在，跳过写入执行时间戳 {work_dir}: {e}")
        return
    path = os.path.join(work_dir, LAST_EXECUTE_MARKER_NAME)
    line = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning(f"写入执行时间戳失败 {path}: {e}")
