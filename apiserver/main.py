#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI任务需求管理系统 - API Server
"""

import argparse
import logging
import os
import sys

from flask import Flask, send_from_directory, jsonify, request
from werkzeug.exceptions import HTTPException

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class _QuietPollFilter(logging.Filter):
    """过滤高频轮询接口的 werkzeug 请求日志，避免刷屏"""
    _quiet_prefixes = (
        '"GET /api/task ',
        '"GET /api/health ',
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._quiet_prefixes)


logging.getLogger('werkzeug').addFilter(_QuietPollFilter())
from flask_cors import CORS

from config_model import AppConfig
from dao import init_database, remove_session
from routes.user import user_bp
from routes.client import client_bp
from routes.task import task_bp
from routes.okr import okr_bp
from routes.todo import todo_bp
from routes.chat import chat_bp
from routes.commercial import commercial_bp
from routes.admin import admin_bp
from routes.auth_plugin import register_global_auth, skip_auth


def create_app(config: AppConfig) -> Flask:
    """创建Flask应用"""
    app = Flask(__name__, static_folder='../web', static_url_path='')

    # 存储完整配置，供路由层通过 current_app.config['APP_CONFIG'] 访问
    app.config['APP_CONFIG'] = config
    # 配置 - 直接访问对象属性
    app.config['HEARTBEAT_TIMEOUT_SECONDS'] = config.heartbeat.timeout_seconds
    # 含 multipart 上传（如商品封面），限制整体请求体约 10MB
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
    app.json.ensure_ascii = False  # JSON响应中文不转义

    # 启用CORS
    CORS(app, supports_credentials=True)

    # 构建 URL 前缀（处理空前缀情况）
    prefix = config.server.url_prefix.rstrip('/') if config.server.url_prefix else ''

    # 注册蓝图
    app.register_blueprint(user_bp, url_prefix=f'{prefix}/api/user')
    app.register_blueprint(client_bp, url_prefix=f'{prefix}/api/client')
    app.register_blueprint(task_bp, url_prefix=f'{prefix}/api/task')
    app.register_blueprint(okr_bp, url_prefix=f'{prefix}/api/okr')
    app.register_blueprint(todo_bp, url_prefix=f'{prefix}/api/todo')
    app.register_blueprint(chat_bp, url_prefix=f'{prefix}/api/chat')
    app.register_blueprint(commercial_bp, url_prefix=f'{prefix}/api/commercial')
    app.register_blueprint(admin_bp, url_prefix=f'{prefix}/api/admin')
    register_global_auth(app, api_prefix=f'{prefix}/api')

    api_prefix = f'{prefix}/api'

    @app.errorhandler(Exception)
    def handle_api_exception(e):
        """
        API 全局异常兜底：
        - 仅对 /api 路由统一 JSON 封装
        - 未捕获异常统一返回 code=500
        """
        if not request.path.startswith(api_prefix):
            if isinstance(e, HTTPException):
                return e
            app.logger.exception('Unhandled non-API exception')
            return 'Internal Server Error', 500

        if isinstance(e, HTTPException):
            return jsonify({
                'code': e.code or 500,
                'message': e.description or '请求处理失败',
                'data': None
            }), e.code or 500

        app.logger.exception('Unhandled API exception')
        return jsonify({
            'code': 500,
            'message': '服务器内部错误',
            'data': None
        }), 500
    
    # 请求结束时清理session
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        remove_session()

    # 健康检查端点
    @app.route(f'{prefix}/api/health')
    @skip_auth
    def health():
        return {'code': 200, 'message': 'ok', 'data': {'status': 'healthy'}}

    # 静态文件路由
    @app.route('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/<path:path>')
    def static_files(path):
        return send_from_directory(app.static_folder, path)

    return app


def main():
    parser = argparse.ArgumentParser(description='AI Task Management API Server')
    parser.add_argument('--config', '-c', type=str, default='config.toml',
                        help='Path to configuration file (TOML format)')
    args = parser.parse_args()
    
    # 加载配置
    if not os.path.exists(args.config):
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)
    
    config = AppConfig.from_toml(args.config)
    
    # 初始化数据库（检查并创建表）
    init_database(config.database)
    
    # 创建应用
    app = create_app(config)
    
    # 启动服务器
    print(f"Starting API Server on http://{config.server.host}:{config.server.port}")
    app.run(
        host=config.server.host,
        port=config.server.port,
        debug=config.server.debug
    )


if __name__ == '__main__':
    main()
