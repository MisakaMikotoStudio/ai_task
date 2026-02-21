# 模型 API 网关技术设计文档

## 1. 功能概述

本功能在现有 AI 任务管理系统中新增**模型 API 网关**能力，实现：

- **虚拟秘钥代理**：客户端使用虚拟 API Key，网关替换为真实 Key 转发给模型供应商
- **按日消费限额**：支持对虚拟秘钥设置单日 RMB 消费上限
- **费用统计**：按虚拟秘钥 × 日期记录 Token 使用量与费用
- **管理后台**：Admin 用户在前端管理虚拟秘钥、模型价格、查看监控数据

---

## 2. 架构概览

```
Client（Claude Agent SDK）
  │  ANTHROPIC_BASE_URL = http://gateway:8080
  │  ANTHROPIC_API_KEY  = vk-xxxx（虚拟秘钥）
  ▼
Gateway（Go, port 8080）
  │  1. 验证虚拟秘钥（查 MySQL）
  │  2. 检查单日消费限额
  │  3. 替换为真实 API Key
  │  4. 转发到真实模型供应商
  │  5. 解析响应中的 Token 用量
  │  6. 异步写入使用日志
  ▼
真实模型 API（Anthropic / OpenAI / ...）

Web 前端（Admin 用户）
  │
  ▼
API Server（Python Flask）
  │  /api/model/* 接口（仅 Admin）
  ▼
MySQL 数据库（共享同一个 DB）
```

---

## 3. 数据库变更（需手动执行的 SQL）

### 3.1 为用户表增加管理员标记

```sql
ALTER TABLE ai_task_users
    ADD COLUMN is_admin TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否是管理员（1=是）';
```

### 3.2 将指定用户设置为管理员

```sql
-- 将用户名替换为实际的 admin 用户名
UPDATE ai_task_users SET is_admin = 1 WHERE name = 'admin';
```

### 3.3 虚拟秘钥表

```sql
CREATE TABLE ai_task_gateway_virtual_keys (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    provider     VARCHAR(64)      NOT NULL                COMMENT '供应商（如 Anthropic / OpenAI）',
    real_key     VARCHAR(512)     NOT NULL                COMMENT '真实 API Key',
    virtual_key  VARCHAR(128)     NOT NULL UNIQUE         COMMENT '虚拟 API Key（客户端使用）',
    target_url   VARCHAR(512)     NOT NULL                COMMENT '目标 API 基础地址',
    daily_limit  DECIMAL(10, 4)   NOT NULL DEFAULT -1     COMMENT '单日消费限额（RMB），-1 表示无限制',
    created_at   DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at   DATETIME                  DEFAULT NULL   COMMENT '软删除',
    INDEX idx_virtual_key (virtual_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='网关虚拟秘钥表';
```

### 3.4 模型价格表

```sql
CREATE TABLE ai_task_gateway_model_prices (
    id                       INT AUTO_INCREMENT PRIMARY KEY,
    provider                 VARCHAR(64)    NOT NULL        COMMENT '供应商',
    model_name               VARCHAR(128)   NOT NULL        COMMENT '模型名称（支持前缀匹配）',
    input_price_per_million  DECIMAL(10, 4) NOT NULL DEFAULT 0
                                                            COMMENT '输入 Token 单价（RMB / 百万 Token）',
    output_price_per_million DECIMAL(10, 4) NOT NULL DEFAULT 0
                                                            COMMENT '输出 Token 单价（RMB / 百万 Token）',
    created_at               DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_model_name (model_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型价格配置表';
```

### 3.5 使用量日志表

```sql
CREATE TABLE ai_task_gateway_usage_logs (
    id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    virtual_key_id INT            NOT NULL               COMMENT '关联虚拟秘钥 ID',
    model          VARCHAR(128)   NOT NULL DEFAULT ''    COMMENT '使用的模型名称',
    input_tokens   INT            NOT NULL DEFAULT 0     COMMENT '输入 Token 数',
    output_tokens  INT            NOT NULL DEFAULT 0     COMMENT '输出 Token 数',
    input_cost     DECIMAL(14, 6) NOT NULL DEFAULT 0     COMMENT '输入费用（RMB）',
    output_cost    DECIMAL(14, 6) NOT NULL DEFAULT 0     COMMENT '输出费用（RMB）',
    stat_date      DATE           NOT NULL               COMMENT '统计日期',
    created_at     DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_usage_key_date (virtual_key_id, stat_date),
    INDEX idx_usage_stat_date (stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='网关使用量日志表';
```

---

## 4. Gateway（Go 1.24）

### 4.1 目录结构

```
gateway/
├── main.go       # 入口：DB 连接 + HTTP 服务器
├── config.go     # 配置：从环境变量读取
├── db.go         # 数据库查询：虚拟秘钥、价格、日志写入
├── proxy.go      # 核心代理逻辑：鉴权替换、流式/非流式转发
├── go.mod
└── Dockerfile    # 多阶段构建，默认在本地构建镜像
```

### 4.2 环境变量

| 变量名        | 默认值      | 说明               |
|--------------|------------|-------------------|
| GATEWAY_PORT  | 8080       | 监听端口           |
| DB_HOST       | localhost  | MySQL 主机         |
| DB_PORT       | 3306       | MySQL 端口         |
| DB_USER       | root       | MySQL 用户         |
| DB_PASSWORD   | (空)       | MySQL 密码         |
| DB_NAME       | ai_task    | 数据库名           |

### 4.3 代理逻辑

1. 从 `x-api-key` 或 `Authorization: Bearer` 提取虚拟秘钥
2. 查询 `ai_task_gateway_virtual_keys`，无效则返回 401
3. 检查今日累计消费 vs `daily_limit`（-1 跳过），超限返回 429
4. 读取请求 body，提取 `model` 字段
5. 构造上游请求，将 Auth Header 替换为真实 Key
   - `provider = Anthropic/DeepSeek/...` → 使用 `x-api-key`
   - `provider = OpenAI/...` → 使用 `Authorization: Bearer`
6. 转发请求，根据 `Content-Type: text/event-stream` 区分流式/非流式
7. 流式：逐行解析 SSE，从 `message_start` 提取 `input_tokens`，从 `message_delta` 提取 `output_tokens`
8. 非流式：从响应 JSON 的 `usage` 字段提取 Token 用量
9. 异步查询模型单价，写入 `ai_task_gateway_usage_logs`

### 4.4 客户端使用方式

```bash
# 设置环境变量，让 Claude Agent SDK 通过网关请求
export ANTHROPIC_API_KEY="vk-your-virtual-key"
export ANTHROPIC_BASE_URL="http://localhost:8080"
```

### 4.5 Docker 运行

```bash
# 在 gateway/ 目录构建镜像（首次约需 1-2 分钟）
cd gateway
docker build -t ai-task-gateway:latest .

# 运行容器
docker run -d \
  --name ai-task-gateway \
  -p 8080:8080 \
  -e DB_HOST=host.docker.internal \
  -e DB_PORT=3306 \
  -e DB_USER=your_user \
  -e DB_PASSWORD=your_password \
  -e DB_NAME=ai_task \
  ai-task-gateway:latest
```

---

## 5. API Server 变更

### 5.1 新增接口（仅 Admin 可访问）

| 方法   | 路径                          | 说明               |
|--------|-------------------------------|-------------------|
| GET    | /api/model/virtual-keys       | 获取虚拟秘钥列表   |
| POST   | /api/model/virtual-keys       | 创建虚拟秘钥       |
| DELETE | /api/model/virtual-keys/{id}  | 删除虚拟秘钥       |
| GET    | /api/model/prices             | 获取模型价格列表   |
| POST   | /api/model/prices             | 新增模型价格       |
| DELETE | /api/model/prices/{id}        | 删除模型价格       |
| GET    | /api/model/monitor            | 获取使用量统计     |

### 5.2 创建虚拟秘钥请求体

```json
{
  "provider": "Anthropic",
  "real_key": "sk-ant-xxxx",
  "daily_limit": 50.0
}
```

内置 Provider → target_url 映射：
- `Anthropic` → `https://api.anthropic.com`
- `OpenAI` → `https://api.openai.com`
- `DeepSeek` → `https://api.deepseek.com`

### 5.3 新增模型价格请求体

```json
{
  "provider": "Anthropic",
  "model_name": "claude-sonnet-4-5",
  "input_price_per_million": 21.8,
  "output_price_per_million": 87.6
}
```

### 5.4 监控接口参数

```
GET /api/model/monitor?days=30
```

返回最近 N 天按（日期 × 虚拟秘钥）分组的 Token 用量和费用。

---

## 6. 前端变更

### 6.1 导航权限

- **Admin 用户**：显示「模型」菜单，隐藏「OKR管理」「待办事项」「任务列表」「客户端管理」
- **普通用户**：显示以上四项，隐藏「模型」

### 6.2 模型页面结构

```
模型
 ├── 监控（默认）
 │    └── 表格：日期 | 供应商 | 虚拟秘钥 | 真实秘钥(脱敏) | 输入Token | 输出Token | 费用(RMB)
 ├── 虚拟秘钥
 │    └── 表格：供应商 | 真实秘钥(脱敏) | 虚拟秘钥 | 单日限费 | 操作（删除）
 │    └── 新增按钮：弹窗填写供应商/真实秘钥/单日限费
 └── 价格
      └── 表格：供应商 | 模型名称 | 输入价格(/百万Token) | 输出价格(/百万Token) | 操作（删除）
      └── 新增按钮：弹窗填写供应商/模型名称/价格
```

---

## 7. 部署注意事项

1. 先执行第 3 节的所有 SQL 语句
2. 通过 SQL 手动将目标用户设置为 Admin（`UPDATE ai_task_users SET is_admin=1 WHERE name='xxx'`）
3. 重启 API Server 使新路由生效
4. 在网关目录 `docker build` 构建镜像
5. 运行网关容器，配置正确的 DB 连接信息
6. 在前端「模型 → 虚拟秘钥」创建虚拟秘钥，将虚拟秘钥配置到 Claude Agent SDK 环境变量
