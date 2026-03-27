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
    // 获取列表（支持游标分页）
    async list(options = {}) {
        const params = new URLSearchParams();
        if (options.cursor !== undefined && options.cursor !== null) {
            params.append('cursor', options.cursor);
        }
        if (options.limit !== undefined) {
            params.append('limit', options.limit);
        }
        if (options.only_mine !== undefined) {
            params.append('only_mine', options.only_mine);
        }
        const query = params.toString() ? `?${params.toString()}` : '';
        return request(`/client${query}`);
    },

    // 获取可用于创建任务的客户端列表
    async listUsable() {
        return request('/client/usable');
    },

    // 获取单个
    async get(id) {
        return request(`/client/${id}`);
    },

    // 获取可用的Agent列表
    async getAgents() {
        return request('/client/agents');
    },

    // 创建
    async create(name, types, options = {}) {
        const body = { name, types };
        if (options.is_public !== undefined) body.is_public = options.is_public;
        if (options.agent !== undefined) body.agent = options.agent;
        return request('/client', {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },

    // 更新
    async update(id, name, types, options = {}) {
        const body = { name, types };
        if (options.is_public !== undefined) body.is_public = options.is_public;
        if (options.agent !== undefined) body.agent = options.agent;
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

    // 心跳
    async heartbeat(id) {
        return request(`/client/${id}/heartbeat`, {
            method: 'POST'
        });
    },

    // 获取仓库配置列表
    async getRepos(id) {
        return request(`/client/${id}/repos`);
    },

    // 批量更新仓库配置
    async updateRepos(id, repos) {
        return request(`/client/${id}/repos`, {
            method: 'PUT',
            body: JSON.stringify({ repos })
        });
    },

    // 获取心跳记录
    async getHeartbeats() {
        return request('/client/heartbeats');
    },

    // 获取环境变量列表
    async getEnvVars(id) {
        return request(`/client/${id}/env-vars`);
    },

    // 新增环境变量
    async createEnvVar(id, key, value) {
        return request(`/client/${id}/env-vars`, {
            method: 'POST',
            body: JSON.stringify({ key, value })
        });
    },

    // 更新环境变量
    async updateEnvVar(id, envVarId, key, value) {
        return request(`/client/${id}/env-vars/${envVarId}`, {
            method: 'PUT',
            body: JSON.stringify({ key, value })
        });
    },

    // 删除环境变量
    async deleteEnvVar(id, envVarId) {
        return request(`/client/${id}/env-vars/${envVarId}`, {
            method: 'DELETE'
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

// 任务API
const taskAPI = {
    // 获取列表
    async list() {
        return request('/task');
    },

    // 获取单个任务详情
    async get(id) {
        return request(`/task/${id}`);
    },

    // 创建
    async create(title, type, clientId = null, desc = null, status = null) {
        const body = { title, type, desc };
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

    // 更新流程
    async updateFlow(id, flow, flowStatus = null) {
        const body = { flow };
        if (flowStatus !== null) {
            body.flow_status = flowStatus;
        }
        return request(`/task/${id}/flow`, {
            method: 'PUT',
            body: JSON.stringify(body)
        });
    },

    // 删除任务
    async delete(id) {
        return request(`/task/${id}`, {
            method: 'DELETE'
        });
    },

    // 更新任务描述
    async updateDesc(id, desc, status = null) {
        const body = { desc };
        if (status !== null) {
            body.status = status;
        }
        return request(`/task/${id}/desc`, {
            method: 'PATCH',
            body: JSON.stringify(body)
        });
    },

    // 审核任务（通过/修订）
    async review(id, action, feedback = null) {
        const body = { action };
        if (feedback !== null) {
            body.feedback = feedback;
        }
        return request(`/task/${id}/review`, {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },

    // 更新任务关联的客户端
    async updateClient(id, clientId) {
        return request(`/task/${id}/client`, {
            method: 'PATCH',
            body: JSON.stringify({ client_id: clientId })
        });
    }
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

    // 获取目标详情
    async getObjective(id) {
        return request(`/okr/objectives/${id}`);
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
    async createKeyResult(objectiveId, title, description = null, targetValue = null, unit = null) {
        return request(`/okr/objectives/${objectiveId}/key-results`, {
            method: 'POST',
            body: JSON.stringify({
                title,
                description,
                target_value: targetValue,
                unit
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

// 模型管理 API（仅 Admin）
const modelAPI = {
    // 获取供应商列表
    async getProviders() {
        return request('/model/providers');
    },

    // ── 虚拟秘钥 ──
    async listVirtualKeys() {
        return request('/model/virtual-keys');
    },
    async createVirtualKey(provider, realKey, dailyLimit, targetUrl) {
        const body = { provider, real_key: realKey, daily_limit: dailyLimit };
        if (targetUrl) body.target_url = targetUrl;
        return request('/model/virtual-keys', {
            method: 'POST',
            body: JSON.stringify(body)
        });
    },
    async deleteVirtualKey(id) {
        return request(`/model/virtual-keys/${id}`, { method: 'DELETE' });
    },

    // ── 模型价格 ──
    async listPrices() {
        return request('/model/prices');
    },
    async createPrice(provider, modelName, inputPrice, outputPrice) {
        return request('/model/prices', {
            method: 'POST',
            body: JSON.stringify({
                provider,
                model_name: modelName,
                input_price_per_million: inputPrice,
                output_price_per_million: outputPrice
            })
        });
    },
    async deletePrice(id) {
        return request(`/model/prices/${id}`, { method: 'DELETE' });
    },

    // ── 监控 ──
    async getMonitor(days = 30) {
        return request(`/model/monitor?days=${days}`);
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

