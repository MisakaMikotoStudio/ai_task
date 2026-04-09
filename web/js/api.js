/**
 * API调用封装
 */

// API基础地址，从配置加载
let API_BASE = '/api';

// 初始化API配置（从服务器获取后端地址）
async function initAPIConfig() {
    try {
        // 使用相对路径，兼容 url_prefix 场景
        const response = await fetch('config.json');
        if (response.ok) {
            const config = await response.json();
            if (config.apiserver) {
                const { host, path_prefix } = config.apiserver;
                if (host) {
                    // 使用 host + path_prefix
                    const hostPart = host.replace(/\/$/, '');
                    const pathPart = path_prefix || '/api';
                    API_BASE = hostPart + pathPart;
                }
                console.log('API Server configured:', API_BASE);
            }
        }
    } catch (error) {
        console.warn('Failed to load config, using default API_BASE:', error);
    }
}

// 通用请求方法
async function request(url, options = {}) {
    const token = getToken();
    
    const headers = {
        'Content-Type': 'application/json',
        'Appid': 'ai_task',
        'traceId': generateUUID(),
        ...options.headers
    };
    
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    
    try {
        const response = await fetch(`${API_BASE}${url}`, {
            ...options,
            headers
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            // Token过期或无效
            if (response.status === 401) {
                clearAuth();
                window.location.reload();
            }
            throw new Error(data.message || data.error || '请求失败');
        }
        
        return data;
    } catch (error) {
        if (error.message === 'Failed to fetch') {
            throw new Error('网络连接失败，请检查服务器是否运行');
        }
        throw error;
    }
}

// 用户API
const userAPI = {
    // 注册
    async register(name, passwordHash) {
        return request('/user/register', {
            method: 'POST',
            body: JSON.stringify({ name, password_hash: passwordHash })
        });
    },
    
    // 登录
    async login(name, passwordHash) {
        return request('/user/login', {
            method: 'POST',
            body: JSON.stringify({ name, password_hash: passwordHash })
        });
    },
    
    // 获取当前用户
    async me() {
        return request('/user/me');
    }
};

// 客户端API
const clientAPI = {
    // 获取列表（直接返回全部）
    async list() {
        return request('/client');
    },

    // 获取单个
    async get(id) {
        return request(`/client/${id}`);
    },

    // 获取可用的Agent列表
    async getAgents() {
        return request('/client/agents');
    },

    // 创建（可选一次性提交 repos、env_vars，与编辑页 PUT 对齐）
    async create(name, options = {}) {
        const body = { name };
        if (options.agent !== undefined) body.agent = options.agent;
        if (options.official_cloud_deploy !== undefined) body.official_cloud_deploy = options.official_cloud_deploy;
        if (options.repos !== undefined) body.repos = options.repos;
        if (options.env_vars !== undefined) body.env_vars = options.env_vars;
        if (options.infrastructure !== undefined) body.infrastructure = options.infrastructure;
        return request('/client', {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },

    // 更新（可选 repos、env_vars、infrastructure 全量同步）
    async update(id, name, options = {}) {
        const body = { name };
        if (options.agent !== undefined) body.agent = options.agent;
        if (options.official_cloud_deploy !== undefined) body.official_cloud_deploy = options.official_cloud_deploy;
        if (options.repos !== undefined) body.repos = options.repos;
        if (options.env_vars !== undefined) body.env_vars = options.env_vars;
        if (options.infrastructure !== undefined) body.infrastructure = options.infrastructure;
        return request(`/client/${id}`, {
            method: 'PUT',
            body: JSON.stringify(body)
        });
    },

    // 删除
    async delete(id) {
        return request(`/client/${id}`, {
            method: 'DELETE'
        });
    },

    // 获取客户端完整配置（repos、agent 等）
    async getConfig(id) {
        return request(`/client/${id}/config`);
    },

    // 复制客户端
    async copy(id) {
        return request(`/client/${id}/copy`, {
            method: 'POST'
        });
    }
};


// 秘钥API
const secretAPI = {
    // 获取列表
    async list() {
        return request('/user/secrets');
    },

    // 创建
    async create(name) {
        return request('/user/secrets', {
            method: 'POST',
            body: JSON.stringify({ name })
        });
    },

    // 删除
    async delete(id) {
        return request(`/user/secrets/${id}`, {
            method: 'DELETE'
        });
    }
};

// ===== 管理后台 API（admin 专用：/api/admin/...）=====
const adminSecretAPI = {
    async list() {
        return request('/admin/secrets');
    },
    async create(name) {
        return request('/admin/secrets', {
            method: 'POST',
            body: JSON.stringify({ name })
        });
    },
    async delete(id) {
        return request(`/admin/secrets/${id}`, {
            method: 'DELETE'
        });
    }
};

const adminClientAPI = {
    async getAgents() {
        return request('/admin/clients/agents');
    },
    async list() {
        return request('/admin/clients');
    },
    async get(id) {
        return request(`/admin/clients/${id}`);
    },
    async create(name, options = {}) {
        const body = { name };
        if (options.agent !== undefined) body.agent = options.agent;
        if (options.official_cloud_deploy !== undefined) body.official_cloud_deploy = options.official_cloud_deploy;
        if (options.repos !== undefined) body.repos = options.repos;
        if (options.env_vars !== undefined) body.env_vars = options.env_vars;
        if (options.infrastructure !== undefined) body.infrastructure = options.infrastructure;
        return request('/admin/clients', {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },
    async update(id, name, options = {}) {
        const body = { name };
        if (options.agent !== undefined) body.agent = options.agent;
        if (options.official_cloud_deploy !== undefined) body.official_cloud_deploy = options.official_cloud_deploy;
        if (options.repos !== undefined) body.repos = options.repos;
        if (options.env_vars !== undefined) body.env_vars = options.env_vars;
        if (options.infrastructure !== undefined) body.infrastructure = options.infrastructure;
        return request(`/admin/clients/${id}`, {
            method: 'PUT',
            body: JSON.stringify(body)
        });
    },
    async delete(id) {
        return request(`/admin/clients/${id}`, {
            method: 'DELETE'
        });
    },
    async copy(id) {
        return request(`/admin/clients/${id}/copy`, {
            method: 'POST'
        });
    }
};

const MAX_PRODUCT_ICON_BYTES = 10 * 1024 * 1024;

const adminCommerceAPI = {
    async uploadProductIcon(file) {
        if (file && file.size > MAX_PRODUCT_ICON_BYTES) {
            throw new Error('图片大小不能超过 10MB');
        }
        const fd = new FormData();
        fd.append('file', file);
        const token = getToken();
        const headers = {
            'Appid': 'ai_task',
            'traceId': generateUUID(),
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
        };
        const response = await fetch(`${API_BASE}/admin/upload/icon`, {
            method: 'POST',
            headers,
            body: fd,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401) {
                clearAuth();
                window.location.reload();
            }
            throw new Error(data.message || data.error || '上传失败');
        }
        return data;
    },
    async createProduct(productData) {
        return request('/admin/product', {
            method: 'POST',
            body: JSON.stringify(productData)
        });
    },
    async getAdminProducts() {
        return request('/admin/products', {
            method: 'GET'
        });
    },
    async offlineProduct(productId) {
        return request(`/admin/product/${productId}/offline`, {
            method: 'POST'
        });
    },
    async onlineProduct(productId) {
        return request(`/admin/product/${productId}/online`, {
            method: 'POST'
        });
    },
    async getOrders(params = {}) {
        const query = new URLSearchParams();
        if (params.page) query.set('page', String(params.page));
        if (params.page_size) query.set('page_size', String(params.page_size));
        if (params.user_id) query.set('user_id', String(params.user_id));
        if (params.status) query.set('status', params.status);
        const qs = query.toString() ? `?${query.toString()}` : '';
        return request(`/admin/orders${qs}`);
    },
    async refundOrder(orderId) {
        return request(`/admin/orders/${orderId}/refund`, {
            method: 'POST'
        });
    }
};

const adminPermissionAPI = {
    async list() {
        return request('/admin/permissions');
    },
    async create(data) {
        return request('/admin/permission', {
            method: 'POST',
            body: JSON.stringify(data)
        });
    },
    async update(id, data) {
        return request(`/admin/permission/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },
    async remove(id) {
        return request(`/admin/permission/${id}`, {
            method: 'DELETE'
        });
    }
};

// 任务API
const taskAPI = {
    // 获取列表
    async list(params = {}) {
        const query = new URLSearchParams();

        if (Array.isArray(params.status) && params.status.length > 0) {
            query.set('status', params.status.join(','));
        } else if (typeof params.status === 'string' && params.status.trim()) {
            query.set('status', params.status.trim());
        }

        if (params.page !== undefined) {
            query.set('page', String(params.page));
        }

        if (params.pageNum !== undefined) {
            query.set('pageNum', String(params.pageNum));
        }

        const queryString = query.toString();
        return request(`/task${queryString ? `?${queryString}` : ''}`);
    },

    // 获取单个任务详情
    async get(id) {
        return request(`/task/${id}`);
    },

    // 创建
    async create(title, clientId = null, status = null) {
        const body = { title };
        // clientId 可选，为 null 时不发送
        if (clientId !== null) {
            body.client_id = clientId;
        }
        if (status !== null) {
            body.status = status;
        }
        return request('/task', {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },

    // 更新状态
    async updateStatus(id, status) {
        return request(`/task/${id}/status`, {
            method: 'PATCH',
            body: JSON.stringify({ status })
        });
    },

    // 删除任务
    async delete(id) {
        return request(`/task/${id}`, {
            method: 'DELETE'
        });
    },
};

// OKR API
const okrAPI = {
    // ========== Objective ==========
    // 获取目标列表（支持周期范围过滤，后端做数据拼接）
    async listObjectives(cycleType = null, status = null, cycleStart = null, cycleEnd = null) {
        const params = new URLSearchParams();
        if (cycleType) params.append('cycle_type', cycleType);
        if (status) params.append('status', status);
        if (cycleStart) params.append('cycle_start', cycleStart);
        if (cycleEnd) params.append('cycle_end', cycleEnd);
        const query = params.toString() ? `?${params.toString()}` : '';
        return request(`/okr/objectives${query}`);
    },

    // 创建目标
    async createObjective(title, description = null, cycleType = 'quarter', cycleStart = null, cycleEnd = null) {
        return request('/okr/objectives', {
            method: 'POST',
            body: JSON.stringify({
                title,
                description,
                cycle_type: cycleType,
                cycle_start: cycleStart,
                cycle_end: cycleEnd
            })
        });
    },

    // 更新目标
    async updateObjective(id, data) {
        return request(`/okr/objectives/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },

    // 删除目标
    async deleteObjective(id) {
        return request(`/okr/objectives/${id}`, {
            method: 'DELETE'
        });
    },

    // ========== KeyResult ==========
    // 创建KR
    async createKeyResult(objectiveId, title, description = null) {
        return request(`/okr/objectives/${objectiveId}/key-results`, {
            method: 'POST',
            body: JSON.stringify({
                title,
                description
            })
        });
    },

    // 更新KR
    async updateKeyResult(id, data) {
        return request(`/okr/key-results/${id}`, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },

    // 删除KR
    async deleteKeyResult(id) {
        return request(`/okr/key-results/${id}`, {
            method: 'DELETE'
        });
    },

    // ========== Reorder ==========
    // 重新排序目标
    async reorderObjectives(objectiveIds) {
        return request('/okr/objectives/reorder', {
            method: 'POST',
            body: JSON.stringify({ objective_ids: objectiveIds })
        });
    },

    // 重新排序KR
    async reorderKeyResults(objectiveId, krIds) {
        return request(`/okr/objectives/${objectiveId}/key-results/reorder`, {
            method: 'POST',
            body: JSON.stringify({ kr_ids: krIds })
        });
    }
};

// 通用文件上传请求（不设置 Content-Type，让浏览器自动处理 multipart boundary）
async function uploadRequest(url, formData) {
    const token = getToken();
    const headers = {
        'Appid': 'ai_task',
        'traceId': generateUUID(),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
    const response = await fetch(`${API_BASE}${url}`, {
        method: 'POST',
        headers,
        body: formData,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        if (response.status === 401) {
            clearAuth();
            window.location.reload();
        }
        throw new Error(data.message || data.error || '上传失败');
    }
    return data;
}

// Chat API
const chatAPI = {
    async listChats(taskId) {
        return request(`/chat/task/${taskId}/chats`);
    },

    async createChat(taskId, title, sessionid = null) {
        const body = { title };
        if (sessionid) body.sessionid = sessionid;
        return request(`/chat/task/${taskId}/chats`, {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },

    // 独立 Chat（task_id=0）
    async listStandaloneChats(params = {}) {
        const query = new URLSearchParams();
        if (Array.isArray(params.status) && params.status.length > 0) {
            query.set('status', params.status.join(','));
        }
        if (params.page !== undefined) query.set('page', String(params.page));
        if (params.pageNum !== undefined) query.set('pageNum', String(params.pageNum));
        const qs = query.toString();
        return request(`/chat/standalone/chats${qs ? `?${qs}` : ''}`);
    },

    async createStandaloneChatWithMessage(input, clientId, extra = {}) {
        return request('/chat/standalone/messages', {
            method: 'POST',
            body: JSON.stringify({ input, client_id: clientId, extra })
        });
    },

    async updateChatStatus(taskId, chatId, status) {
        return request(`/chat/task/${taskId}/chats/${chatId}/status`, {
            method: 'PATCH',
            body: JSON.stringify({ status })
        });
    },

    async deleteChat(taskId, chatId) {
        return request(`/chat/task/${taskId}/chats/${chatId}`, {
            method: 'DELETE'
        });
    },

    async listMessages(taskId, chatId) {
        return request(`/chat/task/${taskId}/chats/${chatId}/messages`);
    },

    async createMessage(taskId, chatId, input, extra = {}) {
        return request(`/chat/task/${taskId}/chats/${chatId}/messages`, {
            method: 'POST',
            body: JSON.stringify({ input, extra })
        });
    },

    // 软删除消息（用户终止），返回 { input } 用于回填输入框
    async deleteMessage(taskId, chatId, messageId) {
        return request(`/chat/task/${taskId}/chats/${chatId}/messages/${messageId}`, {
            method: 'DELETE'
        });
    },

    // 自动创建Chat并发送消息（Chat标题取输入内容前32字符）
    async createChatWithMessage(taskId, input, extra = {}) {
        return request(`/chat/task/${taskId}/messages`, {
            method: 'POST',
            body: JSON.stringify({ input, extra })
        });
    },

    // 上传聊天图片（私有存储），返回 { oss_path, filename }
    async uploadImage(file) {
        if (file && file.size > 10 * 1024 * 1024) {
            throw new Error('图片大小不能超过 10MB');
        }
        const fd = new FormData();
        fd.append('file', file);
        return uploadRequest('/chat/upload/image', fd);
    },

    // 获取聊天图片预签名下载 URL（前端直接从 COS 下载）
    async getPresignedImageUrl(ossPath) {
        return request(`/chat/image/presign?path=${encodeURIComponent(ossPath)}`);
    },
};

// 商业化 API（商品列表、购买）
const commercialAPI = {
    // 获取商品列表（公开，无需登录）
    async getProducts() {
        return request('/commercial/products');
    },

    // 生成支付链接
    async buy(productId, orderType = 'purchase', device = null) {
        const body = { product_id: productId, order_type: orderType };
        if (device) body.device = device;
        return request('/commercial/buy', {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },

    // 获取当前用户的订单列表（分页）
    async getMyOrders(page = 1, pageSize = 20) {
        return request(`/commercial/my-orders?page=${page}&page_size=${pageSize}`);
    },

    // 获取当前用户正在生效的服务
    async getMyServices() {
        return request('/commercial/my-services');
    }
};

// 待办API
const todoAPI = {
    // 获取列表
    async list() {
        return request('/todo');
    },

    // 创建
    async create(content) {
        return request('/todo', {
            method: 'POST',
            body: JSON.stringify({ content })
        });
    },

    // 更新
    async update(id, content = null, completed = null) {
        const body = {};
        if (content !== null) body.content = content;
        if (completed !== null) body.completed = completed;
        return request(`/todo/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(body)
        });
    },

    // 删除
    async delete(id) {
        return request(`/todo/${id}`, {
            method: 'DELETE'
        });
    }
};

