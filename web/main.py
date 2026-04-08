#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI任务需求管理系统 - Web 前端服务
"""

import argparse
import logging
import os
import sys

from flask import Flask, send_from_directory, jsonify, Blueprint, redirect, url_for
from werkzeug.exceptions import NotFound

# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

from config_model import WebConfig


def create_app(config: WebConfig) -> Flask:
    """创建Flask应用"""
    # 获取当前脚本所在目录作为静态文件目录
    static_folder = os.path.dirname(os.path.abspath(__file__))
    app = Flask(__name__, static_folder=static_folder, static_url_path='')
    
    # 构建 URL 前缀（处理空前缀情况）
    url_prefix = config.server.url_prefix.rstrip('/') if config.server.url_prefix else ''
    
    # 保存配置到 app.config
    app.config['URL_PREFIX'] = url_prefix
    
    # 创建蓝图
    web_bp = Blueprint('web', __name__)
    
    # 提供配置接口，供前端获取后端地址
    @web_bp.route('/config.json')
    def get_config():
        return jsonify({
            'apiserver': {
                'host': config.apiserver.host,
                'path_prefix': config.apiserver.path_prefix
            },
        })
    
    # 静态文件路由
    @web_bp.route('/')
    def index():
        return send_from_directory(static_folder, 'index.html')

    @web_bp.route('/admin')
    def admin():
        # 与 / 相同：返回 index.html；前端用 pathname === /admin 识别管理后台，
        # 侧边栏仅展示 应用 / 秘钥 / 商品管理 / 订单管理，并走 /api/admin/... 接口。
        return send_from_directory(static_folder, 'index.html')

    @web_bp.route('/admin/commerce')
    def admin_commerce_redirect():
        # 旧版独立 commerce 页已并入 /admin（hash 路由 #/products、#/orders），避免旧书签落到错误 pathname
        return redirect(url_for('web.admin'), code=302)

    @web_bp.route('/<path:path>')
    def static_files(path):
        try:
            return send_from_directory(static_folder, path)
        except NotFound:
            return send_from_directory(static_folder, 'index.html')
    
    # 注册蓝图（带或不带前缀）
    app.register_blueprint(web_bp, url_prefix=url_prefix if url_prefix else None)

    return app


def _load_config() -> WebConfig:
    config_path = os.environ.get('WEB_CONFIG', 'config.toml')
    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)
    return WebConfig.from_toml(config_path)


# gunicorn 入口：gunicorn "main:app" 时自动加载
# 配置路径通过环境变量 WEB_CONFIG 指定，默认 config.toml
app = create_app(_load_config())


def main():
    parser = argparse.ArgumentParser(description='AI Task Management Web Server')
    parser.add_argument('--config', '-c', type=str, default=None,
                        help='Path to configuration file (TOML format)')
    args = parser.parse_args()

    if args.config:
        os.environ['WEB_CONFIG'] = args.config

    config = _load_config()
    url_prefix = config.server.url_prefix.rstrip('/') if config.server.url_prefix else ''
    print(f"Starting Web Server on http://{config.server.host}:{config.server.port}{url_prefix}")
    print(f"API Server configured at: {config.apiserver.host}{config.apiserver.path_prefix}")

    flask_app = create_app(config)
    flask_app.run(
        host=config.server.host,
        port=config.server.port,
        debug=False
    )


if __name__ == '__main__':
    main()
