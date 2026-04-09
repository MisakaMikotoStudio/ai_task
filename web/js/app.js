/**
 * 主应用逻辑
 */

// DOM元素
const loginPage = document.getElementById('login-page');
const mainPage = document.getElementById('main-page');
const loginForm = document.getElementById('login-form');
const registerForm = document.getElementById('register-form');
const authMessage = document.getElementById('auth-message');
const logoutBtn = document.getElementById('logout-btn');
const currentUsername = document.getElementById('current-username');

// 管理后台与主应用共用 index.html：pathname 以 /admin 结尾时为管理后台（与后端 Flask 路由 /admin 一致）
const ADMIN_PAGE = /\/admin\/?$/.test(window.location.pathname);
const ADMIN_ALLOWED_VIEWS = new Set(['clients', 'secrets', 'products', 'orders']);

function getUrlBasePrefix() {
    // 把 /admin 或 /index.html 去掉，得到类似 "/v1" 的前缀（若无则返回 ""）
    const p = window.location.pathname || '/';
    if (p.endsWith('/admin')) return p.slice(0, -'/admin'.length) || '';
    if (p.endsWith('/admin/')) return p.slice(0, -'/admin/'.length) || '';
    if (p.endsWith('/index.html')) return p.slice(0, -'/index.html'.length) || '';
    return p.endsWith('/') ? p.slice(0, -1) : p;
}

function getAdminUrl() {
    const base = getUrlBasePrefix();
    return `${base}/admin`.replace(/\/$/, '');
}

function getIndexUrl() {
    const base = getUrlBasePrefix();
    return base ? `${base}/` : '/';
}

function redirectToIndex() {
    window.location.href = getIndexUrl();
}

// Admin 模式下，切换到 /api/admin/... 专用接口
const activeClientAPI = ADMIN_PAGE ? adminClientAPI : clientAPI;
const activeSecretAPI = ADMIN_PAGE ? adminSecretAPI : secretAPI;

// 客户端数据缓存
let clientsCache = [];

// 当前状态筛选值
let currentStatusFilter = ['pending', 'running', 'suspended'];
let currentTaskPage = 1;
let currentTaskPageSize = 20;
let currentTaskTotal = 0;
let currentTaskTotalPages = 0;

// 初始化应用
document.addEventListener('DOMContentLoaded', async () => {
    // 先加载API配置（获取后端地址）
    await initAPIConfig();

    await initAuth();
    initTabs();
    initNavigation();
    initForms();
    initModals();
});

// ===== 认证相关 =====

async function initAuth() {
    if (!logoutBtn) {
        console.warn('logoutBtn element not found');
    } else {
        // 避免重复绑定
        logoutBtn.onclick = logout;
    }

    if (!isLoggedIn()) {
        showLoginPage();
        return;
    }

    try {
        const resp = await userAPI.me();
        const userData = resp && resp.data;
        if (userData) {
            // 同步到 localStorage，保证后续 loadUserInfo 能拿到 name
            setCurrentUser({ user_id: userData.user_id, name: userData.name });
        }

        const isAdmin = (userData && userData.name === 'admin');
        if (ADMIN_PAGE) {
            if (!isAdmin) {
                redirectToIndex();
                return;
            }
        } else {
            if (isAdmin) {
                window.location.href = getAdminUrl();
                return;
            }
        }
    } catch (e) {
        console.warn('initAuth failed, clear auth:', e);
        clearAuth();
        showLoginPage();
        return;
    }

    showMainPage();
    loadUserInfo();
}

function showLoginPage() {
    loginPage.classList.add('active');
    mainPage.classList.remove('active');
}

function showMainPage() {
    loginPage.classList.remove('active');
    mainPage.classList.add('active');

    // 管理后台：仅 应用 / 秘钥 / 商品管理 / 订单管理（由 initNavigation 隐藏其余 nav）
    if (ADMIN_PAGE) {
        document.querySelectorAll('.nav-item[data-view=”products”], .nav-item[data-view=”orders”]').forEach((el) => {
            el.style.display = '';
        });
        initAdminCommerce();
        initSecrets();
        initClientSearch();
        loadClients();
        loadSecrets();
        return;
    }

    // 普通用户：显示商店和我的导航项
    document.querySelectorAll('.user-only-nav').forEach((el) => {
        el.style.display = '';
    });

    // 初始化任务筛选控件
    initTaskFilter();

    // 初始化待办事项
    initTodos();

    // 初始化秘钥管理
    initSecrets();

    // 初始化客户端搜索
    initClientSearch();

    // 初始化商店和个人中心
    initStore();
    initProfile();
    initUserPanel();

    // 加载数据
    loadClients();
    loadTasks();
    loadTodos();
    loadSecrets();
}

async function loadUserInfo() {
    const user = getCurrentUser();
    if (user) {
        currentUsername.textContent = user.name;
    }
    try {
        const resp = await userAPI.me();
        const u = resp && resp.data;
        if (u) {
            if (u.name) {
                currentUsername.textContent = u.name;
                setCurrentUser({ user_id: u.user_id, name: u.name });
            }
        }
    } catch {
        // 使用 localStorage 缓存值即可，无需额外处理
    }
}

function initUserPanel() {
    const panel = document.getElementById('sidebar-user-panel');
    if (!panel || ADMIN_PAGE) return;
    panel.addEventListener('click', () => {
        switchToView('profile');
        window.location.hash = '/profile';
    });
}

function logout() {
    clearAuth();
    showLoginPage();
    showToast('已退出登录', 'success');
}

// ===== Tab切换 =====

function initTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            
            // 切换tab按钮状态
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            // 切换表单显示
            document.querySelectorAll('.auth-form').forEach(form => {
                form.classList.remove('active');
            });
            document.getElementById(`${tab}-form`).classList.add('active');
            
            // 清除消息
            hideAuthMessage();
        });
    });
}

// ===== 导航切换（Hash 路由）=====

function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');

    if (ADMIN_PAGE) {
        navItems.forEach((item) => {
            const view = item.dataset.view;
            item.style.display = ADMIN_ALLOWED_VIEWS.has(view) ? '' : 'none';
        });
    }
    
    // 点击导航时更新 hash
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const view = item.dataset.view;
            const targetHash = `#/${view}`;
            // 当 hash 未变化时，hashchange 不会触发，需要手动切换视图
            if (window.location.hash === targetHash) {
                switchToView(view);
                return;
            }
            window.location.hash = `/${view}`;
        });
    });
    
    // 监听 hash 变化
    window.addEventListener('hashchange', handleHashChange);
    
    // 初始化时根据 hash 显示对应视图
    handleHashChange();
}

function handleHashChange() {
    // 从 hash 中提取视图名称，如 #/clients -> clients
    const hash = window.location.hash;
    let view = ADMIN_PAGE ? 'clients' : 'tasks'; // 默认视图
    
    if (hash.startsWith('#/')) {
        view = hash.substring(2); // 去掉 #/
    }
    
    // 验证视图是否存在
    if (!document.getElementById(`${view}-view`)) {
        view = ADMIN_PAGE ? 'clients' : 'tasks';
    }

    if (ADMIN_PAGE && !ADMIN_ALLOWED_VIEWS.has(view)) {
        view = 'clients';
    }
    
    switchToView(view);
}

function switchToView(view) {
    if (ADMIN_PAGE && !ADMIN_ALLOWED_VIEWS.has(view)) {
        view = 'clients';
    }
    const navItems = document.querySelectorAll('.nav-item');

    // 切换导航状态
    navItems.forEach(n => n.classList.remove('active'));
    document.querySelector(`[data-view="${view}"]`)?.classList.add('active');

    // 切换视图
    document.querySelectorAll('.view').forEach(v => {
        v.classList.remove('active');
    });
    document.getElementById(`${view}-view`)?.classList.add('active');

    // 视图切换时加载对应数据
    if (view === 'chats') {
        initStandaloneChatPanel();
        loadStandaloneChatList();
    } else if (view === 'okr') {
        loadObjectives();
        initOKREvents();
    } else if (view === 'secrets') {
        loadSecrets();
    } else if (view === 'products') {
        loadAdminProducts();
    } else if (view === 'orders') {
        loadAdminOrders(1);
    } else if (view === 'store') {
        loadStoreProducts();
    } else if (view === 'profile') {
        loadProfileUserInfo();
        loadMyServices();
        loadMyOrders(1);
    }
}

// ===== 表单处理 =====

function initForms() {
    // 登录表单
    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const username = document.getElementById('login-username').value.trim();
        const password = document.getElementById('login-password').value;
        
        if (!username || !password) {
            showAuthMessage('请填写用户名和密码', 'error');
            return;
        }
        
        try {
            const passwordHash = await sha256(password);
            const result = await userAPI.login(username, passwordHash);

            // 后端返回格式: {code, message, data: {id, name, token}}
            const userData = result.data;
            setToken(userData.token);
            setCurrentUser({ user_id: userData.user_id, name: userData.name });

            showToast('登录成功', 'success');

            const isAdmin = (userData && userData.name === 'admin');
            if (isAdmin && !ADMIN_PAGE) {
                window.location.href = getAdminUrl();
                return;
            }
            if (!isAdmin && ADMIN_PAGE) {
                redirectToIndex();
                return;
            }

            showMainPage();
            loadUserInfo();
        } catch (error) {
            showAuthMessage(error.message, 'error');
        }
    });
    
    // 注册表单
    registerForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const username = document.getElementById('register-username').value.trim();
        const password = document.getElementById('register-password').value;
        const confirm = document.getElementById('register-confirm').value;
        
        if (!username || !password || !confirm) {
            showAuthMessage('请填写所有字段', 'error');
            return;
        }
        
        if (password !== confirm) {
            showAuthMessage('两次输入的密码不一致', 'error');
            return;
        }
        
        if (password.length < 6) {
            showAuthMessage('密码长度至少6位', 'error');
            return;
        }
        
        try {
            const passwordHash = await sha256(password);
            await userAPI.register(username, passwordHash);
            
            showAuthMessage('注册成功，请登录', 'success');
            
            // 切换到登录tab
            document.querySelector('[data-tab="login"]').click();
            document.getElementById('login-username').value = username;
        } catch (error) {
            showAuthMessage(error.message, 'error');
        }
    });
    
    // 添加客户端按钮
    document.getElementById('add-client-btn').addEventListener('click', () => {
        openClientConfig(null, 'add');
    });
    
    // 添加任务按钮
    document.getElementById('add-task-btn').addEventListener('click', () => {
        showAddTaskModal();
    });
}

function showAuthMessage(message, type) {
    authMessage.textContent = message;
    authMessage.className = `message ${type}`;
}

function hideAuthMessage() {
    authMessage.className = 'message';
    authMessage.textContent = '';
}

// ===== 模态框 =====

function initModals() {
    const overlay = document.getElementById('modal-overlay');
    
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            closeModal();
        }
    });
    
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeModal();
        }
    });
}

function openModal(title, content, modalClass = '') {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-content').innerHTML = content;
    
    // 移除之前可能添加的自定义类
    const modal = document.querySelector('.modal');
    modal.classList.remove('modal-lg', 'modal-flow', 'modal-task-detail', 'modal-commerce-product');
    
    // 添加新的自定义类
    if (modalClass) {
        modal.classList.add(modalClass);
    }
    
    document.getElementById('modal-overlay').classList.add('active');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.remove('active');
}

// 简化版 showModal（供 OKR 模块使用）
function showModal(title, content) {
    openModal(title, content);
}

// ===== 客户端管理 =====

// 当前客户端搜索的ID
let currentClientSearchId = null;
let clientsLoading = false;
// 心跳记录缓存
let heartbeatMap = {};

// 初始化客户端搜索和筛选
function initClientSearch() {
    const searchInput = document.getElementById('client-search-input');
    const searchBtn = document.getElementById('client-search-btn');
    const clearBtn = document.getElementById('client-search-clear-btn');

    if (searchBtn) {
        searchBtn.addEventListener('click', () => {
            const inputVal = searchInput.value.trim();
            if (inputVal) {
                const searchId = parseInt(inputVal);
                if (!isNaN(searchId)) {
                    currentClientSearchId = searchId;
                    // 搜索时在当前已加载的数据中过滤
                    renderClients(filterClientsBySearch(clientsCache));
                } else {
                    showToast('请输入有效的应用ID', 'error');
                }
            }
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            searchInput.value = '';
            currentClientSearchId = null;
            renderClients(clientsCache);
        });
    }

    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                searchBtn.click();
            }
        });
    }

}

// 根据搜索ID过滤客户端列表
function filterClientsBySearch(clients) {
    if (currentClientSearchId === null) {
        return clients;
    }
    return clients.filter(client => client.id === currentClientSearchId);
}

// 重置并加载客户端列表
async function resetAndLoadClients() {
    clientsCache = [];
    currentClientSearchId = null;
    const searchInput = document.getElementById('client-search-input');
    if (searchInput) searchInput.value = '';
    
    await loadClients();
}

// 加载客户端列表（首次加载）
async function loadClients() {
    if (clientsLoading) return;
    clientsLoading = true;

    try {
        const clientsResult = await activeClientAPI.list();

        clientsCache = clientsResult.data || [];
        heartbeatMap = {};
        clientsCache.forEach(client => {
            if (client.last_sync_at) {
                heartbeatMap[client.id] = client.last_sync_at;
            }
        });

        renderClients(clientsCache);
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        clientsLoading = false;
    }
}

function renderClients(clients) {
    const tbody = document.getElementById('clients-table-body');
    const emptyState = document.getElementById('clients-empty');

    if (clients.length === 0) {
        tbody.innerHTML = '';
        emptyState.classList.add('show');
        return;
    }

    emptyState.classList.remove('show');

    tbody.innerHTML = clients.map(client => renderClientRow(client)).join('');
}

// 渲染单个客户端行
function renderClientRow(client) {
    const isCloudDeploy = Number(client.official_cloud_deploy) === 1;
    const creatorName = client.creator_name || getCurrentUser()?.name || '-';
    const heartbeatClass = getHeartbeatClass(client.last_sync_at);
    const heartbeatText = formatRelativeTime(client.last_sync_at);
    let actionsHtml = '';
    if (client.editable) {
        actionsHtml = `<div class="client-actions">
            <button class="btn-action btn-edit" onclick="openClientConfig(${client.id}, 'edit')">编辑</button>
            <button class="btn-action btn-copy" onclick="copyClient(${client.id})">复制</button>
            <button class="btn-action btn-delete" onclick="deleteClient(${client.id})">删除</button>
        </div>`;
    } else {
        actionsHtml = '<span class="text-muted">只读</span>';
    }
    return `
    <tr class="client-row">
        <td data-label="ID">#${client.id}</td>
        <td data-label="名称">
            <div class="client-name-cell">
                <strong class="client-name-text">${escapeHtml(client.name)}</strong>
            </div>
        </td>
        <td data-label="云部署">
            <span class="deployment-badge ${isCloudDeploy ? 'cloud' : 'local'}">
                ${isCloudDeploy ? '官方云' : '自部署'}
            </span>
        </td>
        <td data-label="Agent类型">
            <span class="agent-badge">${escapeHtml(client.agent || 'claude sdk')}</span>
        </td>
        <td data-label="版本号">${client.version ?? '-'}</td>
        <td data-label="创建人">
            <span class="client-creator">${escapeHtml(creatorName)}</span>
        </td>
        <td data-label="最后心跳">
            <div class="client-heartbeat ${heartbeatClass}">
                <span class="client-heartbeat-dot ${heartbeatClass}"></span>
                <span class="time-display ${heartbeatClass}">${heartbeatText}</span>
            </div>
        </td>
        <td data-label="创建时间" class="time-display">${formatDateTime(client.created_at)}</td>
        <td data-label="操作">${actionsHtml}</td>
    </tr>
`;
}

function getHeartbeatClass(lastSync) {
    if (!lastSync) return 'offline';
    const diff = new Date() - new Date(lastSync);
    return diff < 300000 ? 'online' : 'offline'; // 5分钟内为在线
}

async function deleteClient(id) {
    if (!confirm('确定要删除这个应用吗？')) {
        return;
    }

    try {
        await activeClientAPI.delete(id);
        showToast('应用删除成功', 'success');
        loadClients();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function copyClient(id) {
    try {
        const result = await activeClientAPI.copy(id);
        showToast(`应用复制成功，新名称: ${result.name}`, 'success');
        loadClients();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ===== 客户端配置向导页面 =====

// 向导步骤定义
const WIZARD_STEPS = [
    { id: 0, label: '基本信息', required: true },
    { id: 1, label: '环境变量', required: false },
    { id: 2, label: '代码仓库', required: true },
    { id: 3, label: '云服务器', required: false },
    { id: 4, label: '域名',     required: false },
    { id: 5, label: '数据库',   required: false },
    { id: 6, label: '支付',     required: false },
    { id: 7, label: '对象存储', required: false },
];

// 当前向导状态
let cfgClientId = null;      // null = 新建模式
let cfgClientMode = 'add';   // 'add' | 'edit' | 'view'
let cfgCurrentStep = 0;
let cfgReposList = [];

// 环境变量：按 env 分组 { test: [{key,value,...}], prod: [...] }
let cfgEnvVarsByEnv = { test: [], prod: [] };
let cfgEnvVarsCurrentEnv = 'test';

// 云服务器：按 env 存储 { test: {name,password,ip}, prod: {name,password,ip} }
let cfgServersByEnv = { test: { name: '', password: '', ip: '' }, prod: { name: '', password: '', ip: '' } };
let cfgServerCurrentEnv = 'test';

// 域名：按 env 存储 { test: ['...'], prod: ['...'] }
let cfgDomainsByEnv = { test: [], prod: [] };
let cfgDomainCurrentEnv = 'test';

// 数据库：按 env 存储 { test: [{...}], prod: [{...}] }
let cfgDatabasesByEnv = { test: [], prod: [] };
let cfgDatabaseCurrentEnv = 'test';

// 支付：按 env 存储 { test: {...}, prod: {...} }
let cfgPaymentsByEnv = { test: {}, prod: {} };
let cfgPaymentCurrentEnv = 'test';

// 对象存储：按 env 存储 { test: {...}, prod: {...} }
let cfgOssByEnv = { test: {}, prod: {} };
let cfgOssCurrentEnv = 'test';

function cfgResetClientConfigState() {
    cfgClientId = null;
    cfgClientMode = 'add';
    cfgCurrentStep = 0;
    cfgReposList = [];
    cfgEnvVarsByEnv = { test: [], prod: [] };
    cfgEnvVarsCurrentEnv = 'test';
    cfgServersByEnv = { test: { name: '', password: '', ip: '' }, prod: { name: '', password: '', ip: '' } };
    cfgServerCurrentEnv = 'test';
    cfgDomainsByEnv = { test: [], prod: [] };
    cfgDomainCurrentEnv = 'test';
    cfgDatabasesByEnv = { test: [], prod: [] };
    cfgDatabaseCurrentEnv = 'test';
    cfgPaymentsByEnv = { test: {}, prod: {} };
    cfgPaymentCurrentEnv = 'test';
    cfgOssByEnv = { test: {}, prod: {} };
    cfgOssCurrentEnv = 'test';
}

function backToClients() {
    switchToView('clients');
    window.location.hash = '/clients';
    loadClients();
}

// 打开客户端配置向导
async function openClientConfig(id, mode) {
    cfgResetClientConfigState();
    cfgClientId = id;
    cfgClientMode = mode;

    // 切换到 client-config-view
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('client-config-view').classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    // 设置标题
    const titleMap = { add: '新建应用', edit: '编辑应用', view: '查看应用' };
    document.getElementById('client-config-title').textContent = titleMap[mode] || '应用配置';

    // 返回按钮
    document.getElementById('client-config-back-btn').onclick = () => {
        cfgResetClientConfigState();
        backToClients();
    };

    // 取消 / 保存按钮
    document.getElementById('wizard-cancel-btn').onclick = () => wizardCancel();
    document.getElementById('wizard-save-btn').onclick = () => wizardSaveAll();

    // 加载 Agent 列表
    let agentOptions = ['claude sdk', 'claude cli'];
    try {
        const r = await activeClientAPI.getAgents();
        if (r.data && r.data.length > 0) agentOptions = r.data;
    } catch (e) { console.warn('获取Agent列表失败', e); }

    const agentSelect = document.getElementById('cfg-client-agent');
    agentSelect.innerHTML = agentOptions.map(a =>
        `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`
    ).join('');

    // 如果是编辑/查看模式，加载已有数据（一个接口返回所有内容，含基础设施）
    if (id !== null) {
        try {
            const clientResult = await activeClientAPI.get(id);
            const clientData = clientResult.data;
            cfgReposList = (clientData.repos || []).map(r => ({ ...r }));

            // 按环境分组加载环境变量
            const envVars = clientData.env_vars || [];
            cfgEnvVarsByEnv = { test: [], prod: [] };
            envVars.forEach(ev => {
                const envKey = ev.env || 'test';
                if (!cfgEnvVarsByEnv[envKey]) cfgEnvVarsByEnv[envKey] = [];
                cfgEnvVarsByEnv[envKey].push({ ...ev });
            });

            document.getElementById('cfg-client-name').value = clientData.name;
            agentSelect.value = clientData.agent || 'claude sdk';
            document.getElementById('cfg-client-official-cloud-deploy').value = String(clientData.official_cloud_deploy ?? 0);

            // 基础设施配置（已包含在 client detail 响应中）
            const infra = clientData.infrastructure || {};
            cfgServersByEnv.test = infra.servers && infra.servers.test ? { ...infra.servers.test } : { name: '', password: '', ip: '' };
            cfgServersByEnv.prod = infra.servers && infra.servers.prod ? { ...infra.servers.prod } : { name: '', password: '', ip: '' };
            cfgDomainsByEnv.test = (infra.domains && infra.domains.test) || [];
            cfgDomainsByEnv.prod = (infra.domains && infra.domains.prod) || [];
            cfgDatabasesByEnv.test = (infra.databases && infra.databases.test) || [];
            cfgDatabasesByEnv.prod = (infra.databases && infra.databases.prod) || [];
            cfgPaymentsByEnv.test = (infra.payments && infra.payments.test) ? { ...infra.payments.test } : {};
            cfgPaymentsByEnv.prod = (infra.payments && infra.payments.prod) ? { ...infra.payments.prod } : {};
            cfgOssByEnv.test = (infra.oss && infra.oss.test) ? { ...infra.oss.test } : {};
            cfgOssByEnv.prod = (infra.oss && infra.oss.prod) ? { ...infra.oss.prod } : {};
        } catch (error) {
            showToast(error.message, 'error');
            return;
        }
    } else {
        document.getElementById('cfg-client-name').value = '';
        agentSelect.value = agentOptions[0] || 'claude sdk';
        document.getElementById('cfg-client-official-cloud-deploy').value = '0';
    }

    // 初始化向导
    wizardGoToStep(0);
}

// ---- 向导步骤导航 ----

function wizardGoToStep(stepIndex) {
    // 切换前先同步当前步骤的 DOM 数据到内存
    wizardSyncCurrentStepFromDOM(cfgCurrentStep);
    cfgCurrentStep = stepIndex;
    wizardUpdateSidebar();
    wizardShowStepPanel(stepIndex);
    wizardRenderStepContent(stepIndex);
    wizardUpdateHeaderActions();
}

function wizardUpdateSidebar() {
    const tabs = document.querySelectorAll('.wizard-sidebar-tab');
    tabs.forEach((tab, idx) => {
        tab.classList.toggle('active', idx === cfgCurrentStep);
        tab.onclick = () => wizardGoToStep(idx);
    });
}

function wizardShowStepPanel(stepIndex) {
    document.querySelectorAll('.wizard-step-panel').forEach((p, idx) => {
        p.classList.toggle('active', idx === stepIndex);
    });
}

function wizardRenderStepContent(stepIndex) {
    const isView = (cfgClientMode === 'view');
    switch (stepIndex) {
        case 0: wizardRenderBasicStep(isView); break;
        case 1: wizardRenderEnvVarsStep(isView); break;
        case 2: wizardRenderReposStep(isView); break;
        case 3: wizardRenderServerStep(isView); break;
        case 4: wizardRenderDomainStep(isView); break;
        case 5: wizardRenderDatabaseStep(isView); break;
        case 6: wizardRenderPaymentStep(isView); break;
        case 7: wizardRenderOssStep(isView); break;
    }
}

function wizardUpdateHeaderActions() {
    const isView = (cfgClientMode === 'view');
    const actionsDiv = document.getElementById('wizard-header-actions');
    if (actionsDiv) actionsDiv.style.display = isView ? 'none' : '';
}

// 在切换 tab 前，同步当前步骤的 DOM 输入到内存状态
function wizardSyncCurrentStepFromDOM(stepIndex) {
    switch (stepIndex) {
        case 1: wizardSyncEnvVarsFromDOM(); break;
        case 3: wizardSyncServerFromDOM(); break;
        case 4: wizardSyncDomainsFromDOM(); break;
        case 6: wizardSyncPaymentFromDOM(); break;
        case 7: wizardSyncOssFromDOM(); break;
    }
}

// ---- 取消 / 保存 ----

function wizardCancel() {
    cfgResetClientConfigState();
    backToClients();
}

async function wizardSaveAll() {
    // 先同步当前步骤 DOM 数据
    wizardSyncCurrentStepFromDOM(cfgCurrentStep);

    // 1. 验证基本信息
    const name = document.getElementById('cfg-client-name').value.trim();
    const agent = document.getElementById('cfg-client-agent').value;
    const officialCloudDeploy = parseInt(document.getElementById('cfg-client-official-cloud-deploy').value, 10) || 0;
    if (!name) { showToast('应用名称不能为空', 'error'); wizardGoToStep(0); return; }
    if (name.length > 16) { showToast('应用名称最多 16 个字符', 'error'); wizardGoToStep(0); return; }

    // 2. 验证代码仓库
    const reposErr = wizardValidateReposOnly();
    if (reposErr) { showToast(reposErr, 'error'); wizardGoToStep(2); return; }

    // 3. 验证环境变量
    const envVarsErr = wizardValidateEnvVars();
    if (envVarsErr) { showToast(envVarsErr, 'error'); wizardGoToStep(1); return; }

    // 构建仓库数据
    const repos = cfgReposList.map(r => ({
        desc: (r.desc || '').trim(),
        url: (r.url || '').trim(),
        token: r.token,
        default_branch: r.default_branch || '',
        branch_prefix: r.branch_prefix || 'ai_',
        docs_repo: !!r.docs_repo
    }));

    // 构建环境变量数据
    const envVars = [];
    for (const envKey of ['test', 'prod']) {
        const items = cfgEnvVarsByEnv[envKey] || [];
        for (const ev of items) {
            const key = (ev.key || '').trim();
            const value = (ev.value == null ? '' : String(ev.value));
            envVars.push({ key, value, env: envKey });
        }
    }

    // 构建基础设施配置
    const infrastructure = {
        servers: cfgServersByEnv,
        domains: cfgDomainsByEnv,
        databases: cfgDatabasesByEnv,
        payments: cfgPaymentsByEnv,
        oss: cfgOssByEnv,
    };

    try {
        // 4. 一次性创建或更新应用（基本信息 + 仓库 + 环境变量 + 基础设施）
        if (cfgClientId === null) {
            const result = await activeClientAPI.create(name, {
                agent,
                official_cloud_deploy: officialCloudDeploy,
                repos,
                env_vars: envVars,
                infrastructure,
            });
            cfgClientId = result.data.id;
        } else {
            await activeClientAPI.update(cfgClientId, name, {
                agent,
                official_cloud_deploy: officialCloudDeploy,
                repos,
                env_vars: envVars,
                infrastructure,
            });
        }

        showToast('应用配置保存成功', 'success');
        cfgResetClientConfigState();
        backToClients();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// 纯前端验证仓库（不调用 API）
function wizardValidateReposOnly() {
    if (!cfgReposList.length) return '请至少添加一个代码仓库';
    let docs = 0;
    for (let i = 0; i < cfgReposList.length; i++) {
        const r = cfgReposList[i];
        const n = i + 1;
        if (!(r.url || '').trim()) return `仓库 #${n} 的 URL 不能为空`;
        if (!(r.desc || '').trim()) return `仓库 #${n} 的简介不能为空`;
        if (String(r.url).trim().startsWith('http') && !(r.token || '').trim()) {
            return `仓库 #${n} 使用 HTTP 地址时必须填写 Token`;
        }
        if (r.docs_repo) docs++;
    }
    if (docs === 0) return '请指定一个文档仓库（单选）';
    if (docs > 1) return '只能指定一个文档仓库';
    return null;
}

// 纯前端验证环境变量
function wizardValidateEnvVars() {
    for (const envKey of ['test', 'prod']) {
        const items = cfgEnvVarsByEnv[envKey] || [];
        for (const ev of items) {
            const key = (ev.key || '').trim();
            const value = (ev.value == null ? '' : String(ev.value));
            if (!key) return `环境 ${envKey} 中存在空变量名`;
            if (!value) return `环境 ${envKey} 变量 ${key} 的值不能为空`;
        }
    }
    return null;
}

// ---- Step 0: 基本信息 ----

function wizardRenderBasicStep(isView) {
    const form = document.getElementById('client-basic-form');
    if (form) form.onsubmit = (e) => e.preventDefault();
    document.querySelectorAll('#wizard-step-0 input, #wizard-step-0 select').forEach(el => {
        el.disabled = isView;
    });
}

// ---- Step 1: 环境变量 ----

function wizardRenderEnvVarsStep(isView) {
    const addBtn = document.getElementById('add-env-var-btn');
    if (addBtn) {
        addBtn.style.display = isView ? 'none' : '';
        addBtn.onclick = () => {
            cfgEnvVarsByEnv[cfgEnvVarsCurrentEnv].push({ key: '', value: '', env: cfgEnvVarsCurrentEnv });
            wizardRenderEnvVarsList();
        };
    }
    // 绑定环境切换 tab
    const tabs = document.querySelectorAll('#env-var-env-tabs .wizard-env-tab');
    tabs.forEach(tab => {
        tab.onclick = () => {
            // 保存当前 DOM 值到内存
            wizardSyncEnvVarsFromDOM();
            cfgEnvVarsCurrentEnv = tab.dataset.env;
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            wizardRenderEnvVarsList();
        };
    });
    // 确保当前 tab 显示正确
    tabs.forEach(t => t.classList.toggle('active', t.dataset.env === cfgEnvVarsCurrentEnv));
    wizardRenderEnvVarsList();
}

function wizardSyncEnvVarsFromDOM() {
    const list = document.getElementById('env-vars-list');
    if (!list) return;
    const rows = list.querySelectorAll('.env-var-row');
    cfgEnvVarsByEnv[cfgEnvVarsCurrentEnv] = [];
    rows.forEach(row => {
        const key = row.querySelector('.env-var-key-input')?.value || '';
        const value = row.querySelector('.env-var-val-input')?.value || '';
        cfgEnvVarsByEnv[cfgEnvVarsCurrentEnv].push({ key, value, env: cfgEnvVarsCurrentEnv });
    });
}

function wizardRenderEnvVarsList() {
    const list = document.getElementById('env-vars-list');
    const empty = document.getElementById('env-vars-empty');
    if (!list) return;
    const isView = (cfgClientMode === 'view');
    const items = cfgEnvVarsByEnv[cfgEnvVarsCurrentEnv] || [];

    if (items.length === 0) {
        list.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';
    const disabledAttr = isView ? 'disabled' : '';
    list.innerHTML = items.map((ev, idx) => {
        const actions = !isView
            ? `<button type="button" class="btn-action btn-delete" onclick="cfgDeleteEnvVar(${idx})">删除</button>`
            : '';
        return `
        <div class="env-var-row env-var-row-editing" data-idx="${idx}">
            <input class="env-var-key-input" type="text" placeholder="变量名（如 MY_KEY）" value="${escapeHtml(ev.key || '')}" ${disabledAttr}>
            <span class="env-var-eq">=</span>
            <input class="env-var-val-input" type="text" placeholder="变量值" value="${escapeHtml(ev.value || '')}" ${disabledAttr}>
            <div class="env-var-actions">${actions}</div>
        </div>`;
    }).join('');
}

function cfgDeleteEnvVar(idx) {
    cfgEnvVarsByEnv[cfgEnvVarsCurrentEnv].splice(idx, 1);
    wizardRenderEnvVarsList();
}


// ---- Step 2: 代码仓库 ----

function wizardRenderReposStep(isView) {
    const addBtn = document.getElementById('cfg-add-repo-btn');
    if (addBtn) {
        addBtn.style.display = isView ? 'none' : '';
        addBtn.onclick = () => {
            const isFirst = cfgReposList.length === 0;
            cfgReposList.push({ desc: '', url: '', token: '', default_branch: '', branch_prefix: 'ai_', docs_repo: isFirst });
            cfgRenderReposWaterfall();
        };
    }
    cfgRenderReposWaterfall();
}

function cfgRenderReposWaterfall() {
    const container = document.getElementById('cfg-repos-waterfall');
    if (!container) return;
    const isView = (cfgClientMode === 'view');

    if (cfgReposList.length === 0) {
        container.innerHTML = `<div class="repos-empty-tip">${isView ? '暂无仓库配置' : '点击上方按钮添加仓库'}</div>`;
        return;
    }

    container.innerHTML = cfgReposList.map((repo, index) => {
        const docsRadio = isView
            ? (repo.docs_repo ? '<span class="repo-docs-label">文档仓库</span>' : '')
            : `<label class="repo-docs-toggle">
                <input type="radio" name="cfg-docs-repo" class="cfg-repo-is-docs" data-index="${index}" ${repo.docs_repo ? 'checked' : ''}>
                <span class="repo-docs-label">文档仓库</span>
               </label>`;
        const deleteBtn = isView ? '' : `<button type="button" class="btn-small btn-delete" onclick="cfgRemoveRepo(${index})">删除</button>`;

        const urlField = isView
            ? `<div class="readonly-field">${escapeHtml(repo.url || '-')}</div>`
            : `<input type="text" class="cfg-repo-url" data-index="${index}" value="${escapeHtml(repo.url || '')}" placeholder="仓库克隆地址">`;
        const branchField = isView
            ? `<div class="readonly-field">${escapeHtml(repo.default_branch || '-')}</div>`
            : `<input type="text" class="cfg-repo-branch" data-index="${index}" value="${escapeHtml(repo.default_branch || '')}" placeholder="可不填，自动获取">`;
        const prefixField = isView
            ? `<div class="readonly-field">${escapeHtml(repo.branch_prefix || 'ai_')}</div>`
            : `<input type="text" class="cfg-repo-branch-prefix" data-index="${index}" value="${escapeHtml(repo.branch_prefix || 'ai_')}" placeholder="ai_">`;
        const tokenField = isView
            ? `<div class="readonly-field">${repo.token ? '********' : '-'}</div>`
            : `<input type="text" class="cfg-repo-token" data-index="${index}" value="${escapeHtml(repo.token || '')}" placeholder="访问令牌，http地址必填">`;
        const descField = isView
            ? `<div class="readonly-field">${escapeHtml(repo.desc || '-')}</div>`
            : `<textarea class="cfg-repo-desc" data-index="${index}" placeholder="仓库简介说明（必填）" rows="2">${escapeHtml(repo.desc || '')}</textarea>`;

        return `
        <div class="repo-card ${repo.docs_repo ? 'repo-card-docs' : ''}" data-index="${index}">
            <div class="repo-card-header">
                <span class="repo-card-index">#${index + 1}</span>
                ${docsRadio}
                ${deleteBtn}
            </div>
            <div class="repo-card-body">
                <div class="repo-field-row repo-field-row-3">
                    <div class="repo-field repo-field-url"><label>URL</label>${urlField}</div>
                    <div class="repo-field repo-field-short"><label>默认主分支</label>${branchField}</div>
                    <div class="repo-field repo-field-short"><label>分支前缀</label>${prefixField}</div>
                </div>
                <div class="repo-field"><label>Token</label>${tokenField}</div>
                <div class="repo-field"><label>简介</label>${descField}</div>
            </div>
        </div>`;
    }).join('');

    // 绑定文档仓库单选事件
    container.querySelectorAll('.cfg-repo-is-docs').forEach(radio => {
        radio.addEventListener('change', (e) => {
            const sel = parseInt(e.target.dataset.index);
            cfgReposList.forEach((r, i) => { r.docs_repo = (i === sel); });
            cfgRenderReposWaterfall();
        });
    });

    // 绑定输入变化
    container.addEventListener('input', (e) => {
        const index = parseInt(e.target.dataset.index);
        if (isNaN(index)) return;
        if (e.target.classList.contains('cfg-repo-desc')) cfgReposList[index].desc = e.target.value;
        if (e.target.classList.contains('cfg-repo-url')) cfgReposList[index].url = e.target.value;
        if (e.target.classList.contains('cfg-repo-token')) cfgReposList[index].token = e.target.value;
        if (e.target.classList.contains('cfg-repo-branch')) cfgReposList[index].default_branch = e.target.value;
        if (e.target.classList.contains('cfg-repo-branch-prefix')) cfgReposList[index].branch_prefix = e.target.value;
    });
}

function cfgRemoveRepo(index) {
    cfgReposList.splice(index, 1);
    cfgRenderReposWaterfall();
}

function wizardValidateRepos() {
    return wizardValidateReposOnly();
}

// ---- Step 3: 云服务器 ----

function wizardRenderServerStep(isView) {
    const tabs = document.querySelectorAll('#server-env-tabs .wizard-env-tab');
    tabs.forEach(tab => {
        tab.onclick = () => {
            wizardSyncServerFromDOM();
            cfgServerCurrentEnv = tab.dataset.env;
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            wizardFillServerForm();
        };
    });
    tabs.forEach(t => t.classList.toggle('active', t.dataset.env === cfgServerCurrentEnv));
    // 禁用/启用表单
    document.querySelectorAll('#server-form-container input').forEach(el => { el.disabled = isView; });
    wizardFillServerForm();
}

function wizardFillServerForm() {
    const cfg = cfgServersByEnv[cfgServerCurrentEnv] || {};
    document.getElementById('cfg-server-name').value = cfg.name || '';
    document.getElementById('cfg-server-password').value = cfg.password || '';
    document.getElementById('cfg-server-ip').value = cfg.ip || '';
}

function wizardSyncServerFromDOM() {
    cfgServersByEnv[cfgServerCurrentEnv] = {
        name: document.getElementById('cfg-server-name').value.trim(),
        password: document.getElementById('cfg-server-password').value,
        ip: document.getElementById('cfg-server-ip').value.trim(),
    };
}


// ---- Step 4: 域名 ----

function wizardRenderDomainStep(isView) {
    const addBtn = document.getElementById('add-domain-btn');
    if (addBtn) {
        addBtn.style.display = isView ? 'none' : '';
        addBtn.onclick = () => {
            cfgDomainsByEnv[cfgDomainCurrentEnv].push('');
            wizardRenderDomainList();
        };
    }
    const tabs = document.querySelectorAll('#domain-env-tabs .wizard-env-tab');
    tabs.forEach(tab => {
        tab.onclick = () => {
            wizardSyncDomainsFromDOM();
            cfgDomainCurrentEnv = tab.dataset.env;
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            wizardRenderDomainList();
        };
    });
    tabs.forEach(t => t.classList.toggle('active', t.dataset.env === cfgDomainCurrentEnv));
    wizardRenderDomainList();
}

function wizardSyncDomainsFromDOM() {
    const list = document.getElementById('domains-list');
    if (!list) return;
    cfgDomainsByEnv[cfgDomainCurrentEnv] = Array.from(list.querySelectorAll('.domain-input'))
        .map(inp => inp.value.trim()).filter(Boolean);
}

function wizardRenderDomainList() {
    const list = document.getElementById('domains-list');
    const empty = document.getElementById('domains-empty');
    if (!list) return;
    const isView = (cfgClientMode === 'view');
    const items = cfgDomainsByEnv[cfgDomainCurrentEnv] || [];
    if (items.length === 0) {
        list.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';
    list.innerHTML = items.map((d, idx) => `
        <div class="infra-list-row">
            <input class="domain-input" type="text" value="${escapeHtml(d)}" placeholder="如 example.com" ${isView ? 'disabled' : ''}>
            ${!isView ? `<button type="button" class="btn-action btn-delete" onclick="cfgDeleteDomain(${idx})">删除</button>` : ''}
        </div>`).join('');
}

function cfgDeleteDomain(idx) {
    cfgDomainsByEnv[cfgDomainCurrentEnv].splice(idx, 1);
    wizardRenderDomainList();
}


// ---- Step 5: 数据库 ----

function wizardRenderDatabaseStep(isView) {
    const addBtn = document.getElementById('add-database-btn');
    if (addBtn) {
        addBtn.style.display = isView ? 'none' : '';
        addBtn.onclick = () => {
            cfgDatabasesByEnv[cfgDatabaseCurrentEnv].push({
                db_type: 'mysql', host: '', port: 3306, username: '', password: '', db_name: ''
            });
            wizardRenderDatabaseList();
        };
    }
    const tabs = document.querySelectorAll('#database-env-tabs .wizard-env-tab');
    tabs.forEach(tab => {
        tab.onclick = () => {
            cfgDatabaseCurrentEnv = tab.dataset.env;
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            wizardRenderDatabaseList();
        };
    });
    tabs.forEach(t => t.classList.toggle('active', t.dataset.env === cfgDatabaseCurrentEnv));
    wizardRenderDatabaseList();
}

function wizardRenderDatabaseList() {
    const list = document.getElementById('databases-list');
    const empty = document.getElementById('databases-empty');
    if (!list) return;
    const isView = (cfgClientMode === 'view');
    const items = cfgDatabasesByEnv[cfgDatabaseCurrentEnv] || [];
    if (items.length === 0) {
        list.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';
    const disAttr = isView ? 'disabled' : '';
    list.innerHTML = items.map((db, idx) => `
        <div class="infra-card" data-db-idx="${idx}">
            <div class="infra-card-header">
                <span class="infra-card-label">#${idx + 1} MySQL</span>
                ${!isView ? `<button type="button" class="btn-action btn-delete" onclick="cfgDeleteDatabase(${idx})">删除</button>` : ''}
            </div>
            <div class="infra-form-grid">
                <div class="form-group">
                    <label>数据库地址</label>
                    <input type="text" class="db-host" value="${escapeHtml(db.host || '')}" placeholder="如 127.0.0.1" ${disAttr}>
                </div>
                <div class="form-group">
                    <label>端口</label>
                    <input type="number" class="db-port" value="${db.port || 3306}" placeholder="3306" ${disAttr}>
                </div>
                <div class="form-group">
                    <label>用户名</label>
                    <input type="text" class="db-username" value="${escapeHtml(db.username || '')}" placeholder="root" ${disAttr}>
                </div>
                <div class="form-group">
                    <label>密码</label>
                    <input type="password" class="db-password" value="${escapeHtml(db.password || '')}" placeholder="数据库密码" ${disAttr}>
                </div>
                <div class="form-group">
                    <label>数据库名称</label>
                    <input type="text" class="db-name" value="${escapeHtml(db.db_name || '')}" placeholder="mydb" ${disAttr}>
                </div>
            </div>
        </div>`).join('');

    // 绑定输入事件
    list.querySelectorAll('.infra-card').forEach(card => {
        const idx = parseInt(card.dataset.dbIdx);
        card.addEventListener('input', (e) => {
            const db = cfgDatabasesByEnv[cfgDatabaseCurrentEnv][idx];
            if (!db) return;
            if (e.target.classList.contains('db-host')) db.host = e.target.value;
            if (e.target.classList.contains('db-port')) db.port = parseInt(e.target.value) || 3306;
            if (e.target.classList.contains('db-username')) db.username = e.target.value;
            if (e.target.classList.contains('db-password')) db.password = e.target.value;
            if (e.target.classList.contains('db-name')) db.db_name = e.target.value;
        });
    });
}

function cfgDeleteDatabase(idx) {
    cfgDatabasesByEnv[cfgDatabaseCurrentEnv].splice(idx, 1);
    wizardRenderDatabaseList();
}


// ---- Step 6: 支付 ----

function wizardRenderPaymentStep(isView) {
    const tabs = document.querySelectorAll('#payment-env-tabs .wizard-env-tab');
    tabs.forEach(tab => {
        tab.onclick = () => {
            wizardSyncPaymentFromDOM();
            cfgPaymentCurrentEnv = tab.dataset.env;
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            wizardFillPaymentForm();
        };
    });
    tabs.forEach(t => t.classList.toggle('active', t.dataset.env === cfgPaymentCurrentEnv));
    document.querySelectorAll('#payment-form-container input, #payment-form-container textarea, #payment-form-container select').forEach(el => {
        el.disabled = isView;
    });
    wizardFillPaymentForm();
}

function wizardFillPaymentForm() {
    const cfg = cfgPaymentsByEnv[cfgPaymentCurrentEnv] || {};
    document.getElementById('cfg-payment-type').value = cfg.payment_type || 'alipay';
    document.getElementById('cfg-payment-appid').value = cfg.appid || '';
    document.getElementById('cfg-payment-notify-url').value = cfg.notify_url || '';
    document.getElementById('cfg-payment-return-url').value = cfg.return_url || '';
    document.getElementById('cfg-payment-gateway').value = cfg.gateway || '';
    document.getElementById('cfg-payment-app-encrypt-key').value = cfg.app_encrypt_key || '';
    document.getElementById('cfg-payment-app-private-key').value = cfg.app_private_key || '';
    document.getElementById('cfg-payment-alipay-public-key').value = cfg.alipay_public_key || '';
}

function wizardSyncPaymentFromDOM() {
    cfgPaymentsByEnv[cfgPaymentCurrentEnv] = {
        payment_type: document.getElementById('cfg-payment-type').value,
        appid: document.getElementById('cfg-payment-appid').value.trim(),
        notify_url: document.getElementById('cfg-payment-notify-url').value.trim(),
        return_url: document.getElementById('cfg-payment-return-url').value.trim(),
        gateway: document.getElementById('cfg-payment-gateway').value.trim(),
        app_encrypt_key: document.getElementById('cfg-payment-app-encrypt-key').value.trim(),
        app_private_key: document.getElementById('cfg-payment-app-private-key').value.trim(),
        alipay_public_key: document.getElementById('cfg-payment-alipay-public-key').value.trim(),
    };
}


// ---- Step 7: 对象存储 ----

function wizardRenderOssStep(isView) {
    const tabs = document.querySelectorAll('#oss-env-tabs .wizard-env-tab');
    tabs.forEach(tab => {
        tab.onclick = () => {
            wizardSyncOssFromDOM();
            cfgOssCurrentEnv = tab.dataset.env;
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            wizardFillOssForm();
        };
    });
    tabs.forEach(t => t.classList.toggle('active', t.dataset.env === cfgOssCurrentEnv));
    document.querySelectorAll('#oss-form-container input, #oss-form-container select').forEach(el => {
        el.disabled = isView;
    });
    wizardFillOssForm();
}

function wizardFillOssForm() {
    const cfg = cfgOssByEnv[cfgOssCurrentEnv] || {};
    document.getElementById('cfg-oss-type').value = cfg.oss_type || 'cos';
    document.getElementById('cfg-oss-secret-id').value = cfg.secret_id || '';
    document.getElementById('cfg-oss-secret-key').value = cfg.secret_key || '';
    document.getElementById('cfg-oss-region').value = cfg.region || '';
    document.getElementById('cfg-oss-bucket').value = cfg.bucket || '';
    document.getElementById('cfg-oss-base-url').value = cfg.base_url || '';
}

function wizardSyncOssFromDOM() {
    cfgOssByEnv[cfgOssCurrentEnv] = {
        oss_type: document.getElementById('cfg-oss-type').value,
        secret_id: document.getElementById('cfg-oss-secret-id').value.trim(),
        secret_key: document.getElementById('cfg-oss-secret-key').value.trim(),
        region: document.getElementById('cfg-oss-region').value.trim(),
        bucket: document.getElementById('cfg-oss-bucket').value.trim(),
        base_url: document.getElementById('cfg-oss-base-url').value.trim(),
    };
}


// 兼容旧代码的 showAddTaskModal 别名
function showAddTaskModal() {
    showTaskEditModal(null, true);
}

// ===== 任务管理 =====

// 初始化任务筛选控件
function initTaskFilter() {
    const statusFilter = document.getElementById('status-filter');
    const pageSizeSelect = document.getElementById('task-page-num');
    const prevBtn = document.getElementById('task-page-prev');
    const nextBtn = document.getElementById('task-page-next');

    if (statusFilter && statusFilter.dataset.initialized !== 'true') {
        const checkboxes = statusFilter.querySelectorAll('input[type="checkbox"]');

        checkboxes.forEach(checkbox => {
            checkbox.addEventListener('change', () => {
                const label = checkbox.parentElement;
                if (checkbox.checked) {
                    label.classList.add('checked');
                } else {
                    label.classList.remove('checked');
                }

                currentStatusFilter = Array.from(checkboxes)
                    .filter(cb => cb.checked)
                    .map(cb => cb.value);
                currentTaskPage = 1;
                loadTasks();
            });
        });

        statusFilter.dataset.initialized = 'true';
    }

    if (pageSizeSelect && pageSizeSelect.dataset.initialized !== 'true') {
        pageSizeSelect.value = String(currentTaskPageSize);
        pageSizeSelect.addEventListener('change', () => {
            currentTaskPageSize = parseInt(pageSizeSelect.value, 10) || 20;
            currentTaskPage = 1;
            loadTasks();
        });
        pageSizeSelect.dataset.initialized = 'true';
    }

    if (prevBtn && prevBtn.dataset.initialized !== 'true') {
        prevBtn.addEventListener('click', () => {
            if (currentTaskPage <= 1) {
                return;
            }
            currentTaskPage -= 1;
            loadTasks();
        });
        prevBtn.dataset.initialized = 'true';
    }

    if (nextBtn && nextBtn.dataset.initialized !== 'true') {
        nextBtn.addEventListener('click', () => {
            if (currentTaskPage >= currentTaskTotalPages) {
                return;
            }
            currentTaskPage += 1;
            loadTasks();
        });
        nextBtn.dataset.initialized = 'true';
    }

    updateTaskPagination();
}

async function loadTasks() {
    try {
        const result = await taskAPI.list({
            status: currentStatusFilter,
            page: currentTaskPage,
            pageNum: currentTaskPageSize
        });
        let tasks = [];
        let total = 0;
        let totalPages = 0;

        if (Array.isArray(result.data)) {
            const allTasks = result.data || [];
            const filteredTasks = currentStatusFilter.length > 0
                ? allTasks.filter(task => currentStatusFilter.includes(task.status))
                : allTasks;
            const statusOrder = { running: 0, pending: 1, suspended: 2, completed: 3 };
            filteredTasks.sort((a, b) => {
                const orderA = statusOrder[a.status] ?? 99;
                const orderB = statusOrder[b.status] ?? 99;
                return orderA - orderB;
            });

            total = filteredTasks.length;
            totalPages = total > 0 ? Math.ceil(total / currentTaskPageSize) : 0;
            const start = (currentTaskPage - 1) * currentTaskPageSize;
            tasks = filteredTasks.slice(start, start + currentTaskPageSize);
        } else {
            const pageData = result.data || {};
            tasks = pageData.items || [];
            total = pageData.total || 0;
            totalPages = pageData.total_pages || 0;
        }

        if (totalPages > 0 && currentTaskPage > totalPages) {
            currentTaskPage = totalPages;
            await loadTasks();
            return;
        }

        currentTaskTotal = total;
        currentTaskTotalPages = totalPages;
        renderTasks(tasks);
        updateTaskPagination();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function renderTasks(tasks) {
    const tbody = document.getElementById('tasks-table-body');
    const emptyState = document.getElementById('tasks-empty');

    if (tasks.length === 0) {
        window.tasksCache = {};
        tbody.innerHTML = '';
        emptyState.classList.add('show');
        return;
    }

    emptyState.classList.remove('show');

    // 缓存任务数据用于弹窗显示
    window.tasksCache = tasks.reduce((acc, t) => { acc[t.id] = t; return acc; }, {});

    tbody.innerHTML = tasks.map(task => {
        const safeTitle = escapeHtml(task.title);

        return `
        <tr>
            <td><span class="task-id">${task.id ?? '-'}</span></td>
            <td class="task-title-cell" title="${safeTitle}">
                <span class="task-title-text">${safeTitle}</span>
            </td>
            <td>
                <select class="status-select status-${task.status}" onchange="updateTaskStatus(${task.id}, this.value, this)">
                    <option value="pending" ${task.status === 'pending' ? 'selected' : ''}>未开始</option>
                    <option value="running" ${task.status === 'running' ? 'selected' : ''}>进行中</option>
                    <option value="suspended" ${task.status === 'suspended' ? 'selected' : ''}>已挂起</option>
                    <option value="completed" ${task.status === 'completed' ? 'selected' : ''}>已结束</option>
                </select>
            </td>
            <td>${escapeHtml(task.client_name || '-')}</td>
            <td class="time-display">${formatDateTime(task.created_at)}</td>
            <td class="task-actions-cell">
                <div class="task-actions">
                    <button class="btn-action btn-chat" onclick="openTaskChat(${task.id})">Chat</button>
                    <button class="btn-action btn-delete" onclick="deleteTask(${task.id})">删除</button>
                    <button class="btn-action btn-reset" onclick="resetTask(${task.id})">重置</button>
                </div>
            </td>
        </tr>
    `}).join('');
}

function updateTaskPagination() {
    const totalInfo = document.getElementById('task-total-info');
    const pageInfo = document.getElementById('task-page-info');
    const prevBtn = document.getElementById('task-page-prev');
    const nextBtn = document.getElementById('task-page-next');

    if (totalInfo) {
        totalInfo.textContent = `共 ${currentTaskTotal} 条`;
    }

    if (pageInfo) {
        if (currentTaskTotalPages > 0) {
            pageInfo.textContent = `第 ${currentTaskPage} / ${currentTaskTotalPages} 页`;
        } else {
            pageInfo.textContent = '第 0 / 0 页';
        }
    }

    if (prevBtn) {
        prevBtn.disabled = currentTaskPage <= 1 || currentTaskTotalPages === 0;
    }

    if (nextBtn) {
        nextBtn.disabled = currentTaskPage >= currentTaskTotalPages || currentTaskTotalPages === 0;
    }
}

// 统一的任务编辑弹窗状态
let taskEditCurrentId = null;  // null 表示新建模式
let taskEditMode = false;      // false 表示查看模式，true 表示编辑模式
let usableClientsCache = [];   // 客户端列表（用于任务创建/编辑）

// 显示统一的任务编辑弹窗（创建或编辑）
async function showTaskEditModal(taskId = null, startInEditMode = false) {
    taskEditCurrentId = taskId;
    
    if (taskId) {
        // 查看/编辑模式 - 从缓存获取任务信息
        const task = window.tasksCache && window.tasksCache[taskId];
        if (!task) {
            showToast('无法获取任务信息', 'error');
            return;
        }
        
        taskEditMode = startInEditMode;  // 默认查看模式
        
        renderTaskEditModal(task);
    } else {
        // 创建模式 - 初始化空数据并获取可用客户端列表
        taskEditMode = true;  // 创建模式始终是编辑模式
        
        // 获取客户端列表
        try {
            const result = await activeClientAPI.list();
            usableClientsCache = result.data || [];
        } catch (error) {
            console.warn('获取客户端列表失败:', error);
            usableClientsCache = [];
        }
        
        renderTaskEditModal(null);
    }
}

// 兼容旧的调用方式
function showTaskDetailModal(taskId) {
    showTaskEditModal(taskId, false);  // 查看模式
}

// 进入编辑模式
async function enterTaskEditMode() {
    taskEditMode = true;
    const task = taskEditCurrentId ? (window.tasksCache && window.tasksCache[taskEditCurrentId]) : null;
    if (task) {
        // 获取客户端列表（编辑模式下需要选择客户端）
        try {
            const result = await activeClientAPI.list();
            usableClientsCache = result.data || [];
        } catch (error) {
            console.warn('获取客户端列表失败:', error);
            usableClientsCache = [];
        }
        renderTaskEditModal(task);
    }
}

// 取消编辑
function cancelTaskEdit() {
    if (taskEditCurrentId) {
        // 编辑模式 - 重置数据并返回查看模式
        const task = window.tasksCache && window.tasksCache[taskEditCurrentId];
        if (task) {
            taskEditMode = false;
            renderTaskEditModal(task);
        }
    } else {
        closeModal();
    }
}

// 渲染任务编辑弹窗
function renderTaskEditModal(task) {
    const isCreateMode = taskEditCurrentId === null;
    const isEditing = taskEditMode;
    const modalTitle = isCreateMode ? '新建任务' : `任务详情 - ${escapeHtml(task.title)}`;
    
    // 构建客户端和任务类型区域
    let headerInfoHtml = '';
    let titleInputHtml = '';
    
    // 状态选择器文本映射
    const statusText = { pending: '未开始', running: '进行中', suspended: '已挂起', completed: '已结束' };

    if (isCreateMode) {
        // 创建模式 - 可编辑的标题和选择框
        const clientOptions = usableClientsCache.map(c =>
            `<option value="${c.id}">${escapeHtml(c.name)}</option>`
        ).join('');

        titleInputHtml = `
            <div class="form-group">
                <label>任务标题 <span class="required">*</span></label>
                <input type="text" id="task-edit-title" placeholder="请输入任务标题（最多45字符）" maxlength="45" required>
            </div>
        `;

        headerInfoHtml = `
            <div class="form-row">
                <div class="form-group form-group-half">
                    <label>关联应用 <span class="required">*</span></label>
                    <select id="task-edit-client" class="status-select" required>
                        <option value="">请选择应用</option>
                        ${clientOptions}
                    </select>
                </div>
                <div class="form-group form-group-half">
                    <label>任务状态</label>
                    <select id="task-edit-status" class="status-select status-running">
                        <option value="pending">未开始</option>
                        <option value="running" selected>进行中</option>
                        <option value="suspended">已挂起</option>
                        <option value="completed">已结束</option>
                    </select>
                </div>
            </div>
        `;
    } else {
        // 查看/编辑模式 - 只读信息并排显示
        titleInputHtml = `
            <div class="form-group">
                <label>任务标题</label>
                <div class="readonly-field">${escapeHtml(task.title)}</div>
            </div>
        `;

        // 状态区域：编辑模式显示选择框，查看模式显示只读标签
        let statusHtml = '';
        let clientHtml = '';
        if (isEditing) {
            // 编辑模式 - 客户端可选择
            const clientOptions = usableClientsCache.map(c =>
                `<option value="${c.id}" ${c.id === task.client_id ? 'selected' : ''}>${escapeHtml(c.name)}</option>`
            ).join('');
            
            clientHtml = `
                <div class="form-group form-group-half">
                    <label>关联应用</label>
                    <select id="task-edit-client" class="status-select">
                        <option value="0" ${!task.client_id ? 'selected' : ''}>不指定应用</option>
                        ${clientOptions}
                    </select>
                </div>
            `;
            
            statusHtml = `
                <div class="form-group form-group-half">
                    <label>任务状态</label>
                    <select id="task-edit-status" class="status-select status-${task.status}">
                        <option value="pending" ${task.status === 'pending' ? 'selected' : ''}>未开始</option>
                        <option value="running" ${task.status === 'running' ? 'selected' : ''}>进行中</option>
                        <option value="suspended" ${task.status === 'suspended' ? 'selected' : ''}>已挂起</option>
                        <option value="completed" ${task.status === 'completed' ? 'selected' : ''}>已结束</option>
                    </select>
                </div>
            `;
        } else {
            // 查看模式 - 只读
            clientHtml = `
                <div class="form-group form-group-half">
                    <label>关联应用</label>
                    <div class="readonly-field">${task.client_name ? escapeHtml(task.client_name) : '-'}</div>
                </div>
            `;
            
            statusHtml = `
                <div class="form-group form-group-half">
                    <label>任务状态</label>
                    <div class="readonly-field"><span class="status-tag status-${task.status}">${statusText[task.status] || task.status}</span></div>
                </div>
            `;
        }

        const timesHtml = !isEditing ? `
            <div class="form-row">
                <div class="form-group form-group-half">
                    <label>创建时间</label>
                    <div class="readonly-field text-muted">${formatDateTime(task.created_at)}</div>
                </div>
                <div class="form-group form-group-half">
                    <label>更新时间</label>
                    <div class="readonly-field text-muted">${formatDateTime(task.updated_at)}</div>
                </div>
            </div>
        ` : '';

        headerInfoHtml = `
            <div class="form-row">
                ${clientHtml}
                ${statusHtml}
            </div>
            ${timesHtml}
        `;
    }
    
    // 底部按钮
    let actionsHtml = '';
    if (isCreateMode) {
        actionsHtml = `
            <div class="modal-actions">
                <button type="button" class="btn-secondary" onclick="closeModal()">取消</button>
                <button type="button" class="btn-primary" onclick="saveTaskEdit()">创建任务</button>
            </div>
        `;
    } else if (isEditing) {
        actionsHtml = `
            <div class="modal-actions">
                <button type="button" class="btn-secondary" onclick="cancelTaskEdit()">取消</button>
                <button type="button" class="btn-primary" onclick="saveTaskEdit()">保存</button>
            </div>
        `;
    } else {
        actionsHtml = `
            <div class="modal-actions">
                <button type="button" class="btn-secondary" onclick="closeModal()">关闭</button>
                <button type="button" class="btn-primary" onclick="enterTaskEditMode()">编辑</button>
            </div>
        `;
    }
    
    const content = `
        <div class="task-edit-content">
            <div class="task-edit-scroll">
                ${titleInputHtml}
                ${headerInfoHtml}
            </div>
            ${actionsHtml}
        </div>
    `;
    
    openModal(modalTitle, content, 'modal-task-edit');
    
}

// 保存任务编辑
async function saveTaskEdit() {
    const isCreateMode = taskEditCurrentId === null;
    
    if (isCreateMode) {
        // 创建模式 - 验证并创建
        const title = document.getElementById('task-edit-title')?.value.trim();
        const clientId = document.getElementById('task-edit-client')?.value;
        
        if (!title) {
            showToast('请输入任务标题', 'error');
            return;
        }
        
        if (!clientId) {
            showToast('请选择应用', 'error');
            return;
        }
        
        try {
            const selectedStatus = document.getElementById('task-edit-status')?.value || 'running';
            const parsedClientId = parseInt(clientId);
            await taskAPI.create(title, parsedClientId, selectedStatus);
            showToast('任务创建成功', 'success');
            closeModal();
            loadTasks();
        } catch (error) {
            showToast(error.message, 'error');
        }
    } else {
        // 编辑模式 - 仅更新状态（标题不可编辑）
        try {
            const newStatus = document.getElementById('task-edit-status')?.value;
            
            // 更新状态
            if (newStatus) {
                await taskAPI.updateStatus(taskEditCurrentId, newStatus);
            }
            
            showToast('任务保存成功', 'success');

            // 更新缓存
            if (window.tasksCache && window.tasksCache[taskEditCurrentId]) {
                if (newStatus) {
                    window.tasksCache[taskEditCurrentId].status = newStatus;
                }
            }

            closeModal();
            loadTasks();
        } catch (error) {
            showToast('保存失败：' + error.message, 'error');
        }
    }
}

// 删除任务
async function deleteTask(id) {
    if (!confirm('确定要删除这个任务吗？')) {
        return;
    }

    try {
        await taskAPI.delete(id);
        showToast('任务删除成功', 'success');
        loadTasks();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// 重置任务：以当前任务信息创建新任务，然后删除旧任务
async function resetTask(id) {
    // 从缓存获取任务信息
    const task = window.tasksCache && window.tasksCache[id];
    if (!task) {
        showToast('无法获取任务信息', 'error');
        return;
    }

    if (!confirm('确定要重置这个任务吗？将以当前任务信息创建新任务并删除旧任务。')) {
        return;
    }

    try {
        // 1. 创建新任务（使用原任务的标题、客户端和状态）
        await taskAPI.create(
            task.title,
            task.client_id,
            task.status || 'pending'
        );
        
        // 2. 删除旧任务
        await taskAPI.delete(id);
        
        showToast('任务重置成功', 'success');
        loadTasks();
    } catch (error) {
        showToast('任务重置失败：' + error.message, 'error');
        loadTasks(); // 重新加载以刷新列表状态
    }
}

// 显示 client_error 错误详情弹窗
function showClientErrorDetail(taskId) {
    const task = window.tasksCache && window.tasksCache[taskId];
    if (!task) {
        showToast('无法获取任务信息', 'error');
        return;
    }
    
    const errorMsg = task.flow && task.flow.error ? task.flow.error : '未知错误';
    
    const content = `
        <div class="error-detail-content">
            <div class="error-detail-icon">⚠️</div>
            <div class="error-detail-title">任务执行异常</div>
            <div class="error-detail-message">
                <pre class="error-pre">${escapeHtml(errorMsg)}</pre>
            </div>
            <div class="modal-actions">
                <button type="button" class="btn-secondary" onclick="closeModal()">关闭</button>
            </div>
        </div>
    `;
    
    openModal('错误详情', content);
}

// 显示执行详情弹窗（显示最新节点信息）
async function showFlowDetailModal(taskId) {
    const task = window.tasksCache && window.tasksCache[taskId];
    if (!task) {
        showToast('无法获取任务信息', 'error');
        return;
    }
    
    // 检查是否有 flow 数据和节点
    const hasNodes = task.flow && task.flow.nodes && task.flow.nodes.length > 0;
    
    if (!hasNodes) {
        const content = `
            <div class="flow-modal-content">
                <div class="flow-modal-empty">
                    <span class="empty-icon">📊</span>
                    <p>该任务暂无执行记录</p>
                </div>
            </div>
        `;
        openModal('执行详情', content, 'modal-flow');
        return;
    }
    
    // 获取最新的节点（数组最后一个）
    const latestNode = task.flow.nodes[task.flow.nodes.length - 1];
    
    // 渲染节点详情
    const nodeDetailHtml = renderNodeDetailForModal(latestNode);
    
    const content = `
        <div class="flow-modal-content">
            <div class="flow-modal-header-info">
                <span class="flow-modal-label">流程状态:</span>
                <span class="flow-status-badge status-${task.flow_status || ''}">${getFlowStatusText(task.flow_status)}</span>
                <span class="flow-modal-label" style="margin-left: 16px;">节点数量:</span>
                <span>${task.flow.nodes.length}</span>
            </div>
            <div class="flow-modal-node-title">
                <span class="node-status-icon">${getNodeStatusIcon(latestNode.status)}</span>
                <span>最新节点: ${escapeHtml(latestNode.label || latestNode.id)}</span>
                <span class="node-status-badge status-${latestNode.status}">${getNodeStatusText(latestNode.status)}</span>
            </div>
            <div class="flow-modal-node-detail">
                ${nodeDetailHtml}
            </div>
        </div>
    `;
    
    openModal('执行详情 - ' + escapeHtml(task.title), content, 'modal-flow');
}

// 渲染节点详情（用于弹窗）
function renderNodeDetailForModal(node) {
    if (!node.fields || node.fields.length === 0) {
        return '<div class="node-panel-empty-fields">暂无字段信息</div>';
    }
    
    // 对字段进行排序，link 类型排在最前面
    const sortedFields = [...node.fields].sort((a, b) => {
        const aIsLink = a.field_type === 'link' || a.fieldType === 'link' ? 0 : 1;
        const bIsLink = b.field_type === 'link' || b.fieldType === 'link' ? 0 : 1;
        return aIsLink - bIsLink;
    });
    
    return sortedFields.map(field => {
        const fieldType = field.field_type || field.fieldType || 'text';
        const fieldLabel = field.label || field.key;
        let valueHtml = '';
        
        switch (fieldType) {
            case 'link':
                // 链接类型
                const linkUrl = field.value || '';
                if (linkUrl) {
                    valueHtml = `<a href="${escapeHtml(linkUrl)}" target="_blank" rel="noopener noreferrer" class="node-link-btn">🔗 ${escapeHtml(fieldLabel)}</a>`;
                } else {
                    valueHtml = '<span class="text-muted">-</span>';
                }
                break;
                
            case 'link_list':
                // 链接列表类型
                if (Array.isArray(field.value) && field.value.length > 0) {
                    valueHtml = `<div class="node-link-list">${field.value.map(link => {
                        const linkLabel = link.label || link.title || '链接';
                        const linkUrl = link.url || '';
                        return linkUrl ? `<a href="${escapeHtml(linkUrl)}" target="_blank" rel="noopener noreferrer" class="node-link-btn">🔗 ${escapeHtml(linkLabel)}</a>` : '';
                    }).join('')}</div>`;
                } else {
                    valueHtml = '<span class="text-muted">-</span>';
                }
                break;
                
            case 'table':
                // 表格类型
                valueHtml = renderTableFieldForModal(field.value);
                break;
                
            case 'textarea':
            case 'markdown':
                // 文本区域/Markdown
                valueHtml = `<div class="node-field-html">${parseSimpleMarkdown(field.value || '-')}</div>`;
                break;
                
            default:
                // 默认文本
                valueHtml = `<div class="node-field-html">${parseSimpleMarkdown(String(field.value || '-'))}</div>`;
        }
        
        return `
            <div class="node-field">
                <label class="node-field-label">${escapeHtml(fieldLabel)}</label>
                ${valueHtml}
            </div>
        `;
    }).join('');
}

// 渲染表格字段（用于弹窗）
function renderTableFieldForModal(tableData) {
    if (!tableData || !tableData.headers || !tableData.rows) {
        return '<span class="text-muted">-</span>';
    }
    
    const headers = tableData.headers;
    const rows = tableData.rows;
    
    const headerHtml = headers.map(h => `<th class="node-table-th">${escapeHtml(String(h))}</th>`).join('');
    
    const rowsHtml = rows.map(row => {
        const cells = row.map(cell => {
            return `<td class="node-table-td">${parseSimpleMarkdown(String(cell ?? ''))}</td>`;
        }).join('');
        return `<tr class="node-table-tr">${cells}</tr>`;
    }).join('');
    
    return `
        <div class="node-table-wrapper">
            <table class="node-table">
                <thead class="node-table-thead">
                    <tr class="node-table-tr">${headerHtml}</tr>
                </thead>
                <tbody class="node-table-tbody">
                    ${rowsHtml}
                </tbody>
            </table>
        </div>
    `;
}

// 简单的 Markdown 解析
function parseSimpleMarkdown(text) {
    if (!text) return '';
    
    let html = escapeHtml(text);
    
    // 链接: [text](url)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    
    // 加粗: **text**
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    
    // 行内代码: `code`
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    
    // 换行
    html = html.replace(/\n/g, '<br>');
    
    return html;
}

// 获取流程状态文本
function getFlowStatusText(status) {
    const texts = {
        '': '无',
        'init': '初始化',
        'ready': '就绪',
        'running': '执行中',
        'paused': '暂停',
        'completed': '已完成',
        'error': '异常',
        'client_error': '应用异常'
    };
    return texts[status] || status || '无';
}

// 获取节点状态图标
function getNodeStatusIcon(status) {
    const icons = {
        pending: '⏳',
        running: '🔄',
        reviewing: '👀',
        reviewed: '✅',
        revising: '✍️',
        done: '🎉',
        completed: '✅',
        in_progress: '🔄',
        skipped: '⏭️',
        failed: '❌',
        error: '⚠️'
    };
    return icons[status] || '⏳';
}

// 获取节点状态文本
function getNodeStatusText(status) {
    const texts = {
        pending: '待处理',
        running: '进行中',
        reviewing: '待审核',
        reviewed: '已审核',
        revising: '修订中',
        done: '已完成',
        completed: '已完成',
        in_progress: '进行中',
        skipped: '已跳过',
        failed: '失败',
        error: '异常'
    };
    return texts[status] || '待处理';
}


async function updateTaskStatus(taskId, status, selectElement) {
    try {
        await taskAPI.updateStatus(taskId, status);
        // 更新 select 元素的状态类
        if (selectElement) {
            selectElement.classList.remove('status-pending', 'status-running', 'status-completed');
            selectElement.classList.add('status-' + status);
        }
        showToast('状态更新成功', 'success');
    } catch (error) {
        showToast(error.message, 'error');
        loadTasks(); // 重新加载以恢复正确状态
    }
}


// ===== 待办事项管理 =====

let todosCache = [];
let currentTodoFilter = 'pending'; // 默认显示未完成

async function loadTodos() {
    try {
        const result = await todoAPI.list();
        todosCache = result.data || [];
        renderTodos(getFilteredTodos());
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function getFilteredTodos() {
    if (currentTodoFilter === 'all') {
        return todosCache;
    } else if (currentTodoFilter === 'completed') {
        return todosCache.filter(t => t.completed);
    } else {
        return todosCache.filter(t => !t.completed);
    }
}

function renderTodos(todos) {
    const todoList = document.getElementById('todo-list');
    const emptyState = document.getElementById('todos-empty');

    if (todos.length === 0) {
        todoList.innerHTML = '';
        emptyState.classList.add('show');
        return;
    }

    emptyState.classList.remove('show');

    todoList.innerHTML = todos.map(todo => `
        <div class="todo-item ${todo.completed ? 'completed' : ''}" data-id="${todo.id}">
            <input type="checkbox" class="todo-checkbox" ${todo.completed ? 'checked' : ''} onchange="toggleTodoComplete(${todo.id}, this.checked)">
            <span class="todo-content" onclick="startEditTodo(${todo.id})">${escapeHtml(todo.content)}</span>
            <button class="todo-delete" onclick="deleteTodo(${todo.id})">删除</button>
        </div>
    `).join('');
}

async function addTodo() {
    const input = document.getElementById('new-todo-input');
    const content = input.value.trim();

    if (!content) {
        showToast('请输入待办内容', 'error');
        return;
    }

    try {
        await todoAPI.create(content);
        input.value = '';
        loadTodos();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function toggleTodoComplete(id, completed) {
    try {
        await todoAPI.update(id, null, completed);
        loadTodos();
    } catch (error) {
        showToast(error.message, 'error');
        loadTodos();
    }
}

function startEditTodo(id) {
    const todo = todosCache.find(t => t.id === id);
    if (!todo) return;

    const todoItem = document.querySelector(`.todo-item[data-id="${id}"]`);
    const contentSpan = todoItem.querySelector('.todo-content');

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'todo-content-input';
    input.value = todo.content;
    input.maxLength = 500;

    const saveEdit = async () => {
        const newContent = input.value.trim();
        if (newContent && newContent !== todo.content) {
            try {
                await todoAPI.update(id, newContent, null);
                loadTodos();
            } catch (error) {
                showToast(error.message, 'error');
                loadTodos();
            }
        } else {
            loadTodos();
        }
    };

    input.addEventListener('blur', saveEdit);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
        }
        if (e.key === 'Escape') {
            loadTodos();
        }
    });

    contentSpan.replaceWith(input);
    input.focus();
    input.select();
}

async function deleteTodo(id) {
    try {
        await todoAPI.delete(id);
        loadTodos();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function initTodos() {
    const addBtn = document.getElementById('add-todo-btn');
    const input = document.getElementById('new-todo-input');

    if (addBtn) {
        addBtn.addEventListener('click', addTodo);
    }

    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                addTodo();
            }
        });
    }

    // 初始化筛选控件
    initTodoFilter();
}

function initTodoFilter() {
    const todoFilter = document.getElementById('todo-filter');
    if (!todoFilter) return;

    const radios = todoFilter.querySelectorAll('input[type="radio"]');
    radios.forEach(radio => {
        // 设置初始选中状态的样式
        if (radio.checked) {
            radio.parentElement.classList.add('checked');
        }

        radio.addEventListener('change', () => {
            // 更新样式
            radios.forEach(r => r.parentElement.classList.remove('checked'));
            radio.parentElement.classList.add('checked');

            // 更新筛选值并重新渲染
            currentTodoFilter = radio.value;
            renderTodos(getFilteredTodos());
        });
    });
}

// ===== 秘钥管理 =====

let secretsCache = [];

async function loadSecrets() {
    try {
        const result = await activeSecretAPI.list();
        secretsCache = result.data || [];
        renderSecrets(secretsCache);
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function renderSecrets(secrets) {
    const tbody = document.getElementById('secrets-table-body');
    const emptyState = document.getElementById('secrets-empty');

    if (!tbody) return;

    if (secrets.length === 0) {
        tbody.innerHTML = '';
        emptyState.classList.add('show');
        return;
    }

    emptyState.classList.remove('show');

    const sorted = [...secrets].sort((a, b) => {
        if (a.type === 'cloud' && b.type !== 'cloud') return -1;
        if (a.type !== 'cloud' && b.type === 'cloud') return 1;
        return new Date(a.created_at) - new Date(b.created_at);
    });

    tbody.innerHTML = sorted.map(secret => `
        <tr>
            <td>${escapeHtml(secret.name)}</td>
            <td><code style="font-size: 12px; word-break: break-all;">${escapeHtml(secret.secret)}</code></td>
            <td class="time-display">${formatDateTime(secret.last_used_at)}</td>
            <td class="time-display">${formatDateTime(secret.created_at)}</td>
            <td>
                ${secret.type !== 'cloud' ? `<button class="btn-action btn-delete" onclick="deleteSecret(${secret.id})">删除</button>` : ''}
            </td>
        </tr>
    `).join('');
}

async function deleteSecret(id) {
    if (!confirm('确定要删除这个秘钥吗？')) return;

    try {
        await activeSecretAPI.delete(id);
        showToast('秘钥删除成功', 'success');
        loadSecrets();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function showAddSecretModal() {
    const content = `
        <form id="add-secret-form">
            <div class="form-group">
                <label>秘钥名称</label>
                <input type="text" id="secret-name" placeholder="请输入秘钥名称" maxlength="64" required>
            </div>
            <button type="submit" class="btn-primary">创建</button>
        </form>
    `;

    openModal('新增秘钥', content);

    document.getElementById('add-secret-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('secret-name').value.trim();

        try {
            await activeSecretAPI.create(name);
            showToast('秘钥创建成功', 'success');
            closeModal();
            loadSecrets();
        } catch (error) {
            showToast(error.message, 'error');
        }
    });
}

function initSecrets() {
    const addBtn = document.getElementById('add-secret-btn');
    if (addBtn) {
        addBtn.addEventListener('click', showAddSecretModal);
    }
}

// ===== Admin 商品/订单（/admin 下嵌入 index.html，样式见 style.css .commerce-*）=====
let adminOrderPage = 1;

const ORDER_STATUS_LABELS = {
    pending: '待支付',
    paid: '已支付',
    failed: '失败',
    refunded: '已退款'
};

function commerceOrderStatusKey(status) {
    const allowed = ['pending', 'paid', 'failed', 'refunded'];
    return allowed.includes(status) ? status : 'unknown';
}

function initAdminCommerce() {
    const openBtn = document.getElementById('open-create-product-btn');
    if (openBtn && openBtn.dataset.bound !== 'true') {
        openBtn.addEventListener('click', showAdminCreateProductModal);
        openBtn.dataset.bound = 'true';
    }

    const loadOrdersBtn = document.getElementById('load-orders-btn');
    if (loadOrdersBtn && loadOrdersBtn.dataset.bound !== 'true') {
        loadOrdersBtn.addEventListener('click', () => loadAdminOrders(1));
        loadOrdersBtn.dataset.bound = 'true';
    }
}

const COMMERCE_MAX_EXPIRE_SECONDS = 1e8;
const COMMERCE_MAX_DESC_LEN = 10000;

function plainPreviewFromDesc(text, maxLen) {
    const flat = String(text || '').replace(/\r\n/g, '\n').replace(/\n/g, ' ').replace(/\s+/g, ' ').trim();
    if (!flat) return '';
    if (flat.length > maxLen) return `${flat.slice(0, maxLen)}…`;
    return flat;
}

function parseAdminProductPriceInput(raw) {
    const s = String(raw ?? '').trim();
    if (!s) return { ok: false, message: '请填写价格' };
    if (!/^\d+(\.\d{1,2})?$/.test(s)) {
        return { ok: false, message: '价格须为数字，最多两位小数' };
    }
    const n = parseFloat(s);
    if (!(n > 0)) return { ok: false, message: '价格必须大于 0' };
    return { ok: true, value: Math.round(n * 100) / 100 };
}

function renderAdminProductCard(p) {
    const offline = p.offline;
    const statusHtml = offline
        ? '<span class="commerce-badge commerce-product-offline">已下架</span>'
        : '<span class="commerce-badge commerce-product-online">上架中</span>';
    const actionHtml = offline
        ? `<button type="button" class="commerce-online-btn btn-online-product" data-id="${p.id}">上架</button>`
        : `<button type="button" class="commerce-offline-btn btn-offline-product" data-id="${p.id}">下架</button>`;
    const validity = p.expire_time ? `${Math.round(p.expire_time / 86400)} 天` : '永久';
    const renew = p.support_continue ? '支持续费' : '不支持续费';
    const preview = plainPreviewFromDesc(p.desc, 140);
    const previewBlock = preview
        ? `<p class="commerce-product-card-desc">${escapeHtml(preview)}</p>`
        : '';
    const media = p.icon
        ? `<div class="commerce-product-card-media"><img src="${escapeHtml(p.icon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'"></div>`
        : '<div class="commerce-product-card-media commerce-product-card-media-placeholder" aria-hidden="true">📦</div>';
    return `<article class="commerce-product-card${offline ? ' is-offline' : ''}">
  ${media}
  <div class="commerce-product-card-body">
    <div class="commerce-product-card-top">
      <h4 class="commerce-product-card-name">${escapeHtml(p.title)}</h4>
      ${statusHtml}
    </div>
    <div class="commerce-product-card-key"><code class="commerce-code">${escapeHtml(p.key)}</code><span class="commerce-product-card-id">#${p.id}</span></div>
    <div class="commerce-product-card-price commerce-amount">¥${Number(p.price || 0).toFixed(2)}</div>
    ${previewBlock}
    <ul class="commerce-product-card-meta">
      <li><span>有效期</span><strong>${validity}</strong></li>
      <li><span>续费</span><strong>${renew}</strong></li>
      <li><span>创建</span><strong class="commerce-meta-time">${formatDateTime(p.created_at)}</strong></li>
    </ul>
    <div class="commerce-product-card-actions">${actionHtml}</div>
  </div>
</article>`;
}

/* ===== 图片裁剪工具 ===== */
const CROP_ASPECT = 3 / 2;
const CROP_OUTPUT_WIDTH = 600;
const CROP_OUTPUT_HEIGHT = Math.round(CROP_OUTPUT_WIDTH / CROP_ASPECT);
const CROP_JPEG_QUALITY = 0.85;

function openImageCropDialog(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('读取文件失败'));
        reader.onload = () => {
            const img = new Image();
            img.onerror = () => reject(new Error('无法加载图片'));
            img.onload = () => _showCropUI(img, resolve, reject);
            img.src = reader.result;
        };
        reader.readAsDataURL(file);
    });
}

function _showCropUI(sourceImg, resolve, reject) {
    const overlay = document.createElement('div');
    overlay.className = 'image-crop-overlay';
    overlay.innerHTML = `
        <div class="image-crop-dialog">
            <div class="image-crop-header">
                <h4>裁剪封面图</h4>
                <button type="button" class="image-crop-close">&times;</button>
            </div>
            <div class="image-crop-body">
                <p class="image-crop-hint">拖拽图片调整位置，滑块控制缩放</p>
                <div class="image-crop-canvas-wrap" id="crop-viewport">
                    <img id="crop-source-img" alt="">
                </div>
                <div class="image-crop-zoom-row">
                    <span class="image-crop-zoom-label">缩放</span>
                    <input type="range" class="image-crop-zoom-slider" id="crop-zoom-slider"
                           min="100" max="300" value="100" step="1">
                </div>
            </div>
            <div class="image-crop-footer">
                <button type="button" class="btn-secondary" id="crop-cancel-btn">取消</button>
                <button type="button" class="btn-primary" id="crop-confirm-btn">确认裁剪</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const viewport = overlay.querySelector('#crop-viewport');
    const imgEl = overlay.querySelector('#crop-source-img');
    const zoomSlider = overlay.querySelector('#crop-zoom-slider');
    const cancelBtn = overlay.querySelector('#crop-cancel-btn');
    const closeBtn = overlay.querySelector('.image-crop-close');
    const confirmBtn = overlay.querySelector('#crop-confirm-btn');

    imgEl.src = sourceImg.src;

    let scale = 1;
    let imgX = 0;
    let imgY = 0;
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let dragStartImgX = 0;
    let dragStartImgY = 0;

    function getViewportRect() {
        return viewport.getBoundingClientRect();
    }

    function fitInitialScale() {
        const vr = getViewportRect();
        const vw = vr.width;
        const vh = vr.height;
        const iw = sourceImg.naturalWidth;
        const ih = sourceImg.naturalHeight;
        const scaleToFitW = vw / iw;
        const scaleToFitH = vh / ih;
        const minFit = Math.max(scaleToFitW, scaleToFitH);
        scale = minFit;
        zoomSlider.min = Math.round(minFit * 100);
        zoomSlider.max = Math.round(Math.max(minFit * 3, 3) * 100);
        zoomSlider.value = Math.round(scale * 100);
        imgX = (vw - iw * scale) / 2;
        imgY = (vh - ih * scale) / 2;
    }

    function clampPosition() {
        const vr = getViewportRect();
        const vw = vr.width;
        const vh = vr.height;
        const iw = sourceImg.naturalWidth * scale;
        const ih = sourceImg.naturalHeight * scale;
        if (iw <= vw) {
            imgX = (vw - iw) / 2;
        } else {
            imgX = Math.min(0, Math.max(vw - iw, imgX));
        }
        if (ih <= vh) {
            imgY = (vh - ih) / 2;
        } else {
            imgY = Math.min(0, Math.max(vh - ih, imgY));
        }
    }

    function render() {
        imgEl.style.width = (sourceImg.naturalWidth * scale) + 'px';
        imgEl.style.height = (sourceImg.naturalHeight * scale) + 'px';
        imgEl.style.left = imgX + 'px';
        imgEl.style.top = imgY + 'px';
    }

    function update() {
        clampPosition();
        render();
    }

    function onPointerDown(e) {
        if (e.button && e.button !== 0) return;
        isDragging = true;
        dragStartX = e.clientX ?? e.touches[0].clientX;
        dragStartY = e.clientY ?? e.touches[0].clientY;
        dragStartImgX = imgX;
        dragStartImgY = imgY;
        e.preventDefault();
    }

    function onPointerMove(e) {
        if (!isDragging) return;
        const cx = e.clientX ?? (e.touches && e.touches[0] ? e.touches[0].clientX : dragStartX);
        const cy = e.clientY ?? (e.touches && e.touches[0] ? e.touches[0].clientY : dragStartY);
        imgX = dragStartImgX + (cx - dragStartX);
        imgY = dragStartImgY + (cy - dragStartY);
        update();
    }

    function onPointerUp() {
        isDragging = false;
    }

    viewport.addEventListener('mousedown', onPointerDown);
    viewport.addEventListener('touchstart', onPointerDown, { passive: false });
    document.addEventListener('mousemove', onPointerMove);
    document.addEventListener('touchmove', onPointerMove, { passive: false });
    document.addEventListener('mouseup', onPointerUp);
    document.addEventListener('touchend', onPointerUp);

    viewport.addEventListener('wheel', (e) => {
        e.preventDefault();
        const vr = getViewportRect();
        const oldScale = scale;
        const delta = e.deltaY > 0 ? -0.03 : 0.03;
        const minScale = parseInt(zoomSlider.min) / 100;
        const maxScale = parseInt(zoomSlider.max) / 100;
        scale = Math.min(maxScale, Math.max(minScale, scale + delta));
        const pointerX = e.clientX - vr.left;
        const pointerY = e.clientY - vr.top;
        imgX = pointerX - (pointerX - imgX) * (scale / oldScale);
        imgY = pointerY - (pointerY - imgY) * (scale / oldScale);
        zoomSlider.value = Math.round(scale * 100);
        update();
    }, { passive: false });

    zoomSlider.addEventListener('input', () => {
        const vr = getViewportRect();
        const oldScale = scale;
        scale = parseInt(zoomSlider.value) / 100;
        const cx = vr.width / 2;
        const cy = vr.height / 2;
        imgX = cx - (cx - imgX) * (scale / oldScale);
        imgY = cy - (cy - imgY) * (scale / oldScale);
        update();
    });

    function cleanup() {
        document.removeEventListener('mousemove', onPointerMove);
        document.removeEventListener('touchmove', onPointerMove);
        document.removeEventListener('mouseup', onPointerUp);
        document.removeEventListener('touchend', onPointerUp);
        overlay.remove();
    }

    function cancel() {
        cleanup();
        reject(new Error('用户取消裁剪'));
    }

    cancelBtn.addEventListener('click', cancel);
    closeBtn.addEventListener('click', cancel);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) cancel();
    });

    confirmBtn.addEventListener('click', () => {
        const vr = getViewportRect();
        const vw = vr.width;
        const vh = vr.height;
        const srcX = -imgX / scale;
        const srcY = -imgY / scale;
        const srcW = vw / scale;
        const srcH = vh / scale;
        const canvas = document.createElement('canvas');
        canvas.width = CROP_OUTPUT_WIDTH;
        canvas.height = CROP_OUTPUT_HEIGHT;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(sourceImg, srcX, srcY, srcW, srcH, 0, 0, CROP_OUTPUT_WIDTH, CROP_OUTPUT_HEIGHT);
        canvas.toBlob((blob) => {
            cleanup();
            if (!blob) {
                reject(new Error('裁剪失败'));
                return;
            }
            resolve(blob);
        }, 'image/jpeg', CROP_JPEG_QUALITY);
    });

    requestAnimationFrame(() => {
        fitInitialScale();
        update();
    });
}

function showAdminCreateProductModal() {
    const content = `
        <form id="admin-create-product-form" class="commerce-modal-form commerce-modal-form-refined">
            <div class="commerce-modal-grid">
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label" for="admin-product-key">商品 Key <span class="commerce-req">*</span></label>
                    <input id="admin-product-key" type="text" name="key" required placeholder="如 pro_monthly" autocomplete="off" maxlength="64">
                </div>
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label" for="admin-product-title">商品名称 <span class="commerce-req">*</span></label>
                    <input id="admin-product-title" type="text" name="title" required placeholder="展示名称" autocomplete="off" maxlength="128">
                </div>
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label" for="admin-product-price">价格（元）<span class="commerce-req">*</span></label>
                    <input id="admin-product-price" type="text" name="price" required placeholder="如 9.99" inputmode="decimal" autocomplete="off">
                </div>
                <div class="commerce-modal-field commerce-expire-row">
                    <label class="commerce-modal-label" for="admin-product-expire-val">有效期</label>
                    <div class="commerce-expire-inputs">
                        <input id="admin-product-expire-val" type="text" name="expire_val" placeholder="留空表示永久" inputmode="numeric" autocomplete="off">
                        <select id="admin-product-expire-unit" name="expire_unit" aria-label="有效期单位">
                            <option value="day" selected>天</option>
                            <option value="hour">小时</option>
                        </select>
                    </div>
                </div>
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label">封面图</label>
                    <input type="hidden" name="icon" id="admin-product-icon-url" value="">
                    <input type="file" id="admin-product-icon-file" class="commerce-file-input-hidden" accept="image/jpeg,image/png,image/gif,image/webp">
                    <div class="commerce-icon-upload-row">
                        <button type="button" class="btn-secondary commerce-btn-upload" id="admin-product-icon-trigger">上传图片</button>
                        <span id="admin-product-icon-status" class="commerce-icon-status"></span>
                    </div>
                    <div id="admin-product-icon-preview" class="commerce-icon-preview" hidden>
                        <img id="admin-product-icon-preview-img" alt="封面预览">
                    </div>
                </div>
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label" for="admin-product-renew">支持续费</label>
                    <select id="admin-product-renew" name="support_continue" class="commerce-modal-select">
                        <option value="0" selected>否</option>
                        <option value="1">是</option>
                    </select>
                </div>
            </div>
            <div class="commerce-modal-field commerce-modal-field-span commerce-modal-desc-block">
                <label class="commerce-modal-label" for="admin-product-desc">商品描述</label>
                <textarea id="admin-product-desc" name="desc" class="commerce-desc-textarea" rows="4" maxlength="${COMMERCE_MAX_DESC_LEN}" placeholder="支持换行，前台按原格式展示"></textarea>
            </div>
            <div class="modal-actions commerce-modal-actions commerce-modal-actions-compact">
                <button type="button" class="btn-secondary commerce-btn-modal-cancel" onclick="closeModal()">取消</button>
                <button type="submit" class="btn-primary commerce-btn-submit-create">创建</button>
            </div>
        </form>
    `;
    openModal('新增商品', content, 'modal-commerce-product');

    const fileInput = document.getElementById('admin-product-icon-file');
    const trigger = document.getElementById('admin-product-icon-trigger');
    const statusEl = document.getElementById('admin-product-icon-status');
    const hiddenIcon = document.getElementById('admin-product-icon-url');
    const previewWrap = document.getElementById('admin-product-icon-preview');
    const previewImg = document.getElementById('admin-product-icon-preview-img');

    if (trigger && fileInput) {
        trigger.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', async () => {
            const f = fileInput.files && fileInput.files[0];
            if (!f) return;
            fileInput.value = '';
            let croppedBlob;
            try {
                croppedBlob = await openImageCropDialog(f);
            } catch (cropErr) {
                return;
            }
            statusEl.textContent = '上传中…';
            try {
                const croppedFile = new File([croppedBlob], f.name.replace(/\.[^.]+$/, '.jpg'), { type: 'image/jpeg' });
                const res = await adminCommerceAPI.uploadProductIcon(croppedFile);
                const url = res.data && res.data.url;
                if (!url) throw new Error('未返回链接');
                hiddenIcon.value = url;
                previewImg.src = url;
                previewWrap.hidden = false;
                statusEl.textContent = '已上传';
            } catch (err) {
                hiddenIcon.value = '';
                previewWrap.hidden = true;
                statusEl.textContent = err.message || '上传失败';
                showToast(statusEl.textContent, 'error');
            }
        });
    }

    const form = document.getElementById('admin-create-product-form');
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const key = form.querySelector('[name=key]').value.trim();
            if (!/^[a-zA-Z0-9_-]+$/.test(key) || key.length > 64) {
                showToast('Key 仅允许字母、数字、下划线与短横线，且不超过 64 字符', 'error');
                return;
            }
            const title = form.querySelector('[name=title]').value.trim();
            if (!title || title.length > 128) {
                showToast('请填写商品名称（不超过 128 字）', 'error');
                return;
            }
            const pr = parseAdminProductPriceInput(form.querySelector('[name=price]').value);
            if (!pr.ok) {
                showToast(pr.message, 'error');
                return;
            }
            const desc = (form.querySelector('[name=desc]').value || '');
            if (desc.length > COMMERCE_MAX_DESC_LEN) {
                showToast(`描述过长（最多 ${COMMERCE_MAX_DESC_LEN} 字符）`, 'error');
                return;
            }
            const ev = (form.querySelector('[name=expire_val]').value || '').trim();
            let expireSeconds = null;
            if (ev !== '') {
                if (!/^\d+$/.test(ev)) {
                    showToast('有效期须为正整数', 'error');
                    return;
                }
                const n = parseInt(ev, 10);
                const unit = form.querySelector('[name=expire_unit]').value;
                const mult = unit === 'hour' ? 3600 : 86400;
                expireSeconds = n * mult;
                if (expireSeconds < 1 || expireSeconds > COMMERCE_MAX_EXPIRE_SECONDS) {
                    showToast(`换算后有效期须对应 1～${COMMERCE_MAX_EXPIRE_SECONDS} 秒`, 'error');
                    return;
                }
            }
            const icon = (hiddenIcon && hiddenIcon.value || '').trim() || null;

            const productData = {
                key,
                title,
                desc,
                price: pr.value,
                expire_time: expireSeconds,
                support_continue: form.querySelector('[name=support_continue]').value === '1',
                icon,
            };
            try {
                await adminCommerceAPI.createProduct(productData);
                closeModal();
                await loadAdminProducts();
                showToast('商品创建成功', 'success');
            } catch (err) {
                showToast(`创建失败：${err.message}`, 'error');
            }
        });
    }
}

async function loadAdminProducts() {
    const root = document.getElementById('products-list');
    if (!root) return;
    try {
        const resp = await adminCommerceAPI.getAdminProducts();
        const items = resp.data || [];
        if (!items.length) {
            root.innerHTML = '<div class="commerce-products-empty">暂无商品，点击右上角「新增商品」开始添加。</div>';
            return;
        }
        root.innerHTML = items.map((p) => renderAdminProductCard(p)).join('');

        root.querySelectorAll('.btn-offline-product').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const productId = Number(btn.dataset.id);
                if (!productId) return;
                if (!confirm('确认下架该商品？前台将不再展示。')) return;
                try {
                    await adminCommerceAPI.offlineProduct(productId);
                    await loadAdminProducts();
                    showToast('已下架', 'success');
                } catch (err) {
                    showToast(`下架失败：${err.message}`, 'error');
                }
            });
        });

        root.querySelectorAll('.btn-online-product').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const productId = Number(btn.dataset.id);
                if (!productId) return;
                if (!confirm('确认上架该商品？前台将重新展示。')) return;
                try {
                    await adminCommerceAPI.onlineProduct(productId);
                    await loadAdminProducts();
                    showToast('已上架', 'success');
                } catch (err) {
                    showToast(`上架失败：${err.message}`, 'error');
                }
            });
        });
    } catch (err) {
        showToast(`加载商品失败：${err.message}`, 'error');
    }
}

async function loadAdminOrders(page) {
    adminOrderPage = page;
    const raw = (document.getElementById('order-user-filter') || {}).value;
    const statusFilter = (document.getElementById('order-status-filter') || {}).value || null;
    const s = (raw || '').trim();
    let userIdFilter;
    if (s) {
        if (!/^[1-9]\d{5}$/.test(s)) {
            showToast('用户编号须为 6 位数字且首位不能为 0', 'error');
            return;
        }
        userIdFilter = s;
    }
    try {
        const resp = await adminCommerceAPI.getOrders({
            page,
            page_size: 20,
            user_id: userIdFilter,
            status: statusFilter || undefined
        });
        renderAdminOrdersTable(resp.data || {});
    } catch (err) {
        showToast(`加载订单失败：${err.message}`, 'error');
    }
}

function renderAdminOrdersTable(data) {
    const tbody = document.querySelector('#orders-table tbody');
    if (!tbody) return;
    const items = data.items || [];
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="commerce-empty-cell">暂无订单</td></tr>';
    } else {
        tbody.innerHTML = items.map((o) => {
            const canRefund = o.status === 'paid';
            const sk = commerceOrderStatusKey(o.status);
            const statusLabel = ORDER_STATUS_LABELS[o.status] || escapeHtml(o.status);
            return `<tr>
  <td>${o.id}</td>
  <td>${o.user_id}</td>
  <td><code class="commerce-code">${escapeHtml(o.product_key)}</code></td>
  <td class="commerce-amount">¥${Number(o.amount || 0).toFixed(2)}</td>
  <td><span class="commerce-badge commerce-order-${sk}">${statusLabel}</span></td>
  <td class="commerce-muted">${o.trade_no ? escapeHtml(o.trade_no) : '—'}</td>
  <td class="commerce-muted">${formatDateTime(o.created_at)}</td>
  <td>${canRefund ? `<button type="button" class="commerce-refund-btn refund-btn" data-id="${o.id}">退款</button>` : '<span class="commerce-muted">—</span>'}</td>
</tr>`;
        }).join('');
    }

    const pageInfo = document.getElementById('orders-page-info');
    if (pageInfo) pageInfo.textContent = `共 ${data.total || 0} 条，第 ${data.page || 1} 页`;

    const prevBtn = document.getElementById('orders-prev-btn');
    const nextBtn = document.getElementById('orders-next-btn');
    if (prevBtn) {
        prevBtn.disabled = (data.page || 1) <= 1;
        prevBtn.onclick = () => loadAdminOrders((data.page || 1) - 1);
    }
    if (nextBtn) {
        nextBtn.disabled = (data.page || 1) * (data.page_size || 20) >= (data.total || 0);
        nextBtn.onclick = () => loadAdminOrders((data.page || 1) + 1);
    }

    tbody.querySelectorAll('.refund-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const orderId = Number(btn.dataset.id);
            if (!orderId) return;
            if (!confirm(`确认退款订单 #${orderId}？`)) return;
            try {
                await adminCommerceAPI.refundOrder(orderId);
                await loadAdminOrders(adminOrderPage);
                showToast('退款成功', 'success');
            } catch (err) {
                showToast(`退款失败：${err.message}`, 'error');
            }
        });
    });
}

// ===== Chat 跳转 =====

function openTaskChat(taskId) {
    window.open(`chat.html?task_id=${taskId}`, '_blank');
}

function openStandaloneChat(chatId, clientId) {
    window.open(`chat.html?task_id=0&chat_id=${chatId}&client_id=${clientId}`, '_blank');
}

// ===== 独立 Chat（split-panel, inline rendering） =====

let scStatusFilter = ['pending', 'running', 'completed'];
let scChatList = [];
let scCurrentPage = 1;
let scPageSize = 20;
let scTotal = 0;
let scSelectedChatId = null;
let scSelectedClientId = null;
let scClientsCache = [];
let scInitialized = false;

// Chat detail state
let scMessagesCache = [];
let scMessagesFingerprint = '';
const scOutputHtmlCache = new Map();
let scRunningMessageId = null;
let scPollTimer = null;
let scMergeRequestStore = {};
let scClientConfigCache = null;

const SC_CLIENT_CACHE_KEY = 'sc_last_client_id';

// ── Init marked.js with highlight.js ──
(function scInitMarked() {
    if (typeof marked === 'undefined') return;
    if (typeof markedHighlight !== 'undefined' && typeof hljs !== 'undefined') {
        marked.use(markedHighlight.markedHighlight({
            emptyLangClass: 'hljs',
            langPrefix: 'hljs language-',
            highlight: function (code, lang) {
                if (lang && hljs.getLanguage(lang)) {
                    try { return hljs.highlight(code, { language: lang }).value; } catch (_) {}
                }
                try { return hljs.highlightAuto(code).value; } catch (_) {}
                return '';
            }
        }));
    }
    const renderer = new marked.Renderer();
    const defaultCodeRenderer = renderer.code.bind(renderer);
    renderer.code = function (token) {
        const html = defaultCodeRenderer(token);
        const lang = (token.lang || '').split(/\s/)[0];
        if (lang) {
            return html.replace('<pre>', `<pre><span class="code-lang-label">${lang}</span>`);
        }
        return html;
    };
    const defaultLinkRenderer = renderer.link.bind(renderer);
    renderer.link = function (token) {
        const html = defaultLinkRenderer(token);
        return html.replace('<a ', '<a target="_blank" rel="noopener noreferrer" ');
    };
    marked.use({ renderer, breaks: true, gfm: true });
})();

// ── Helpers ──
function scFormatTime(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ' ' +
        d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function scParseMsgExtra(extra) {
    if (!extra) return {};
    if (typeof extra === 'string') {
        try { return JSON.parse(extra); } catch { return {}; }
    }
    return extra;
}

function scRenderOutput(output) {
    if (!output) return '';
    if (typeof marked !== 'undefined') {
        try { return marked.parse(output); } catch (_) {}
    }
    return `<p>${escapeHtml(output).replace(/\n/g, '<br>')}</p>`;
}

function scGetOutputCacheKey(msg) {
    const updated = msg.updated_at || msg.created_at || '';
    const outputLen = msg.output ? String(msg.output).length : 0;
    return `${msg.id}|${msg.status}|${updated}|${outputLen}`;
}

function scRenderOutputCached(msg) {
    if (!msg || !msg.output) return '';
    const key = scGetOutputCacheKey(msg);
    const cached = scOutputHtmlCache.get(key);
    if (cached) return cached;
    const html = scRenderOutput(msg.output);
    scOutputHtmlCache.set(key, html);
    if (scOutputHtmlCache.size > 1000) scOutputHtmlCache.clear();
    return html;
}

function scAutoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

function scGetMessagesFingerprint(list) {
    return (list || []).map(m => {
        const updated = m.updated_at || m.created_at || '';
        return `${m.id}:${m.status}:${updated}`;
    }).join('|');
}

// ── Init panel ──
function initStandaloneChatPanel() {
    if (scInitialized) return;
    scInitialized = true;

    // 状态筛选
    const filterEl = document.getElementById('chat-status-filter');
    if (filterEl) {
        filterEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', () => {
                const label = cb.parentElement;
                if (cb.checked) { label.classList.add('checked'); }
                else { label.classList.remove('checked'); }
                scStatusFilter = Array.from(filterEl.querySelectorAll('input:checked')).map(c => c.value);
                scCurrentPage = 1;
                scChatList = [];
                loadStandaloneChatList();
            });
        });
    }

    // 新建按钮
    const newBtn = document.getElementById('sc-new-chat-btn');
    if (newBtn) {
        newBtn.addEventListener('click', () => scShowWelcome());
    }

    // 加载更多
    const loadMoreBtn = document.getElementById('sc-load-more-btn');
    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', () => {
            scCurrentPage++;
            loadStandaloneChatList(true);
        });
    }

    // Welcome composer: 发送按钮
    const sendBtn = document.getElementById('sc-welcome-send-btn');
    if (sendBtn) {
        sendBtn.addEventListener('click', () => scSendNewChat());
    }

    // Welcome composer: Ctrl+Enter
    const textarea = document.getElementById('sc-welcome-input');
    if (textarea) {
        textarea.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault();
                scSendNewChat();
            }
        });
    }

    // 客户端选择变化时缓存
    const clientSel = document.getElementById('sc-client-select');
    if (clientSel) {
        clientSel.addEventListener('change', () => {
            if (clientSel.value) {
                localStorage.setItem(SC_CLIENT_CACHE_KEY, clientSel.value);
            }
        });
    }

    // Detail composer: 发送按钮
    const detailSendBtn = document.getElementById('sc-detail-send-btn');
    if (detailSendBtn) {
        detailSendBtn.addEventListener('click', () => scSendMessage());
    }

    // Detail composer: 终止按钮
    const detailStopBtn = document.getElementById('sc-detail-stop-btn');
    if (detailStopBtn) {
        detailStopBtn.addEventListener('click', () => scTerminateMessage());
    }

    // Detail composer: Ctrl+Enter
    const detailInput = document.getElementById('sc-detail-input');
    if (detailInput) {
        detailInput.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault();
                scSendMessage();
            }
        });
        detailInput.addEventListener('input', () => scAutoResize(detailInput));
    }

    // Merge default branch
    const mergeDefaultBtn = document.getElementById('sc-merge-default-btn');
    if (mergeDefaultBtn) {
        mergeDefaultBtn.addEventListener('click', () => scMergeToDefaultBranch());
    }

    // Merge request modal: click overlay to close
    const mergeModal = document.getElementById('sc-merge-modal');
    if (mergeModal) {
        mergeModal.addEventListener('click', (e) => {
            if (e.target === mergeModal) scCloseMergeRequestModal();
        });
    }

    // 加载客户端列表
    scLoadClients();
}

// ── Clients ──
async function scLoadClients() {
    try {
        const result = await activeClientAPI.list();
        scClientsCache = result.data || [];
    } catch (e) {
        scClientsCache = [];
    }
    scRenderClientSelect();
}

function scRenderClientSelect() {
    const sel = document.getElementById('sc-client-select');
    if (!sel) return;
    const lastClientId = localStorage.getItem(SC_CLIENT_CACHE_KEY) || '';
    let html = '<option value="">-- 选择应用 --</option>';
    for (const c of scClientsCache) {
        const selected = String(c.id) === lastClientId ? ' selected' : '';
        html += `<option value="${c.id}"${selected}>${escapeHtml(c.name)}</option>`;
    }
    sel.innerHTML = html;
}

// ── Chat list ──
async function loadStandaloneChatList(append = false) {
    try {
        const res = await chatAPI.listStandaloneChats({
            status: scStatusFilter,
            page: scCurrentPage,
            pageNum: scPageSize,
        });
        const pageData = res.data || {};
        const items = pageData.items || [];
        scTotal = pageData.total || 0;

        if (append) {
            scChatList = scChatList.concat(items);
        } else {
            scChatList = items;
        }

        scRenderChatList();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function scRenderChatList() {
    const listEl = document.getElementById('sc-chat-list');
    const loadMoreEl = document.getElementById('sc-load-more');

    if (scChatList.length === 0) {
        listEl.innerHTML = '<div class="sc-chat-empty">暂无 Chat</div>';
        if (loadMoreEl) loadMoreEl.style.display = 'none';
        return;
    }

    const statusLabels = {
        pending: '等待',
        running: '执行中',
        completed: '完成',
        terminated: '终止'
    };

    listEl.innerHTML = scChatList.map(chat => {
        const isActive = chat.id === scSelectedChatId ? ' active' : '';
        const safeTitle = escapeHtml(chat.title || '无标题');
        const statusText = statusLabels[chat.status] || chat.status;
        const clientName = escapeHtml(chat.client_name || '');
        const timeStr = formatDateTime(chat.updated_at || chat.created_at);

        return `
        <div class="sc-chat-item${isActive}" onclick="scSelectChat(${chat.id}, ${chat.client_id || 0})" data-chat-id="${chat.id}">
            <div class="sc-chat-item-row1">
                <span class="sc-chat-item-id">#${chat.id}</span>
                <span class="sc-chat-status-dot ${chat.status}"></span>
                <span class="sc-chat-status-label ${chat.status}">${statusText}</span>
            </div>
            <div class="sc-chat-item-title">${safeTitle}</div>
            <div class="sc-chat-item-meta">
                ${clientName ? `<span>${clientName}</span>` : ''}
                <span>${timeStr}</span>
            </div>
        </div>`;
    }).join('');

    if (loadMoreEl) {
        loadMoreEl.style.display = scChatList.length < scTotal ? '' : 'none';
    }
}

// ── Welcome / Detail switching ──
function scShowWelcome() {
    scStopPolling();
    scSelectedChatId = null;
    scSelectedClientId = null;
    scMessagesCache = [];
    scRunningMessageId = null;

    document.querySelectorAll('.sc-chat-item.active').forEach(el => el.classList.remove('active'));
    document.getElementById('sc-welcome').style.display = '';
    document.getElementById('sc-detail').style.display = 'none';

    const textarea = document.getElementById('sc-welcome-input');
    if (textarea) textarea.value = '';
    scRenderClientSelect();
}

async function scSelectChat(chatId, clientId) {
    scStopPolling();
    scSelectedChatId = chatId;
    scSelectedClientId = clientId;

    // 高亮左侧
    document.querySelectorAll('.sc-chat-item').forEach(el => {
        el.classList.toggle('active', Number(el.dataset.chatId) === chatId);
    });

    // 切换视图
    document.getElementById('sc-welcome').style.display = 'none';
    document.getElementById('sc-detail').style.display = '';

    // Update topbar
    const chat = scChatList.find(c => c.id === chatId);
    if (chat) scUpdateTopbar(chat);

    // 加载客户端配置（合并按钮需要）
    await scLoadClientConfig(clientId);

    // 加载消息
    await scLoadMessages(chatId);
}

function scUpdateTopbar(chat) {
    document.getElementById('sc-topbar-title').textContent = chat.title || `Chat #${chat.id}`;
    const badge = document.getElementById('sc-topbar-badge');
    const labels = { running: '执行中', completed: '执行完成', terminated: '已终止' };
    badge.textContent = labels[chat.status] || '';
    badge.className = `sc-detail-topbar-badge ${chat.status || ''}`;
}

// ── Client config ──
async function scLoadClientConfig(clientId) {
    if (!clientId) { scClientConfigCache = null; return; }
    try {
        const res = await clientAPI.getConfig(clientId);
        scClientConfigCache = res.data;
    } catch (e) {
        scClientConfigCache = null;
    }
}

// ── Messages ──
async function scLoadMessages(chatId) {
    try {
        const res = await chatAPI.listMessages(0, chatId);
        scMessagesCache = res.data || [];
        scMessagesFingerprint = scGetMessagesFingerprint(scMessagesCache);
        scRenderFeed();
        scUpdateComposerState();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function scRenderFeed() {
    const feed = document.getElementById('sc-feed');

    if (scMessagesCache.length === 0) {
        feed.innerHTML = `
            <div class="sc-feed-empty">
                <div class="sc-feed-empty-icon">✨</div>
                <div class="sc-feed-empty-text">开始对话</div>
                <div class="sc-feed-empty-sub">在下方输入框输入您的问题</div>
            </div>`;
        return;
    }

    const statusChipMap = {
        pending: ['pending', '等待执行'],
        running: ['running', '执行中…'],
        completed: ['completed', '执行完成'],
        terminated: ['terminated', '已终止']
    };

    feed.innerHTML = scMessagesCache.map(msg => {
        const [chipClass, chipLabel] = statusChipMap[msg.status] || ['', msg.status];
        const extra = scParseMsgExtra(msg.extra);

        // User row
        const userRow = `
        <div class="sc-msg-user-row">
            <div class="sc-msg-avatar user">你</div>
            <div class="sc-msg-body">
                <div class="sc-msg-header">
                    <span class="sc-msg-role">You</span>
                    <span class="sc-msg-time">${scFormatTime(msg.created_at)}</span>
                </div>
                <div class="sc-msg-text">${escapeHtml(msg.input)}</div>
            </div>
        </div>`;

        // Agent output
        let outputHtml;
        if (msg.output) {
            outputHtml = `<div class="sc-msg-output">${scRenderOutputCached(msg)}</div>`;
        } else if (msg.status === 'pending' || msg.status === 'running') {
            outputHtml = `
                <div class="sc-typing-indicator">
                    <div class="sc-typing-dot"></div>
                    <div class="sc-typing-dot"></div>
                    <div class="sc-typing-dot"></div>
                </div>`;
        } else {
            outputHtml = `<div class="sc-msg-output" style="color:var(--text-muted);font-style:italic">无输出</div>`;
        }

        // Extra buttons
        let extraBtns = '';
        if (extra.develop_doc) {
            extraBtns += `<a class="sc-msg-extra-btn doc-btn" href="${escapeHtml(extra.develop_doc)}" target="_blank" rel="noopener noreferrer">📄 开发文档</a>`;
        }
        if (extra && extra.merge_request !== undefined) {
            const storeKey = `sc_msg_${msg.id}`;
            const mrData = Array.isArray(extra.merge_request) ? extra.merge_request : [];
            scMergeRequestStore[storeKey] = mrData;
            extraBtns += `<button class="sc-msg-extra-btn mr-btn" onclick="scShowMergeRequestModal('${storeKey}')">🔀 变更详情</button>`;
        }

        const agentRow = `
        <div class="sc-msg-agent-row">
            <div class="sc-msg-avatar agent">⚡</div>
            <div class="sc-msg-body">
                <div class="sc-msg-header">
                    <span class="sc-msg-role">Agent</span>
                    <span class="sc-msg-time">${scFormatTime(msg.updated_at)}</span>
                </div>
                ${outputHtml}
                <div class="sc-msg-status-row">
                    <span class="sc-msg-status-chip ${chipClass}">${chipLabel}</span>
                    ${extraBtns}
                </div>
            </div>
        </div>`;

        return `<div class="sc-msg-turn">${userRow}${agentRow}</div>`;
    }).join('');

    // Scroll to bottom
    setTimeout(() => { feed.scrollTop = feed.scrollHeight; }, 50);
}

// ── Composer state ──
function scUpdateComposerState() {
    const running = scMessagesCache.find(m => m.status === 'pending' || m.status === 'running');
    scRunningMessageId = running ? running.id : null;

    const box = document.getElementById('sc-detail-composer-box');
    const input = document.getElementById('sc-detail-input');
    const sendBtn = document.getElementById('sc-detail-send-btn');
    const stopBtn = document.getElementById('sc-detail-stop-btn');
    const hintEl = document.getElementById('sc-detail-hint');
    const hintText = document.getElementById('sc-detail-hint-text');
    const mergeDefaultBtn = document.getElementById('sc-merge-default-btn');
    const showMerge = !!scClientConfigCache;

    if (scRunningMessageId) {
        box.classList.add('locked');
        input.disabled = true;
        sendBtn.style.display = 'none';
        if (mergeDefaultBtn) mergeDefaultBtn.style.display = 'none';
        stopBtn.style.display = 'flex';
        hintEl.className = 'sc-detail-hint warn';
        hintText.textContent = '当前有消息正在执行，无法输入新消息';
        scStartPolling();
    } else {
        box.classList.remove('locked');
        input.disabled = false;
        sendBtn.style.display = 'flex';
        if (mergeDefaultBtn) mergeDefaultBtn.style.display = showMerge ? 'flex' : 'none';
        stopBtn.style.display = 'none';
        hintEl.className = 'sc-detail-hint';
        hintText.textContent = '尽管问，带图也行';
        scStopPolling();
    }

    // Update topbar
    const chat = scChatList.find(c => c.id === scSelectedChatId);
    if (chat) scUpdateTopbar(chat);
}

// ── Polling ──
function scStartPolling() {
    if (scPollTimer) return;
    scPollTimer = setInterval(async () => {
        if (!scSelectedChatId) return;
        try {
            const res = await chatAPI.listMessages(0, scSelectedChatId);
            const fresh = res.data || [];
            const nextFingerprint = scGetMessagesFingerprint(fresh);
            if (nextFingerprint !== scMessagesFingerprint) {
                const prevRunningId = scRunningMessageId;
                scMessagesFingerprint = nextFingerprint;
                scMessagesCache = fresh;
                scRenderFeed();
                scUpdateComposerState();
                if (prevRunningId !== scRunningMessageId) {
                    scCurrentPage = 1;
                    scChatList = [];
                    await loadStandaloneChatList();
                }
            }
        } catch { /* silent */ }
    }, 3000);
}

function scStopPolling() {
    if (scPollTimer) { clearInterval(scPollTimer); scPollTimer = null; }
}

// ── Send message (active chat) ──
async function scSendMessage() {
    if (!scSelectedChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const input = document.getElementById('sc-detail-input');
    const text = input.value.trim();
    if (!text) return;

    const btn = document.getElementById('sc-detail-send-btn');
    btn.disabled = true;
    try {
        await chatAPI.createMessage(0, scSelectedChatId, text);
        input.value = '';
        scAutoResize(input);
        await scLoadMessages(scSelectedChatId);
        scCurrentPage = 1;
        scChatList = [];
        await loadStandaloneChatList();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ── Terminate message ──
async function scTerminateMessage() {
    if (!scRunningMessageId || !scSelectedChatId) return;
    const btn = document.getElementById('sc-detail-stop-btn');
    btn.disabled = true;
    btn.textContent = '终止中…';
    try {
        const res = await chatAPI.deleteMessage(0, scSelectedChatId, scRunningMessageId);
        const inputText = res?.data?.input || '';
        showToast('已撤销，内容已回填', 'success');
        await scLoadMessages(scSelectedChatId);
        scCurrentPage = 1;
        scChatList = [];
        await loadStandaloneChatList();
        if (inputText) {
            const inputEl = document.getElementById('sc-detail-input');
            inputEl.value = inputText;
            scAutoResize(inputEl);
            inputEl.focus();
            inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
        }
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '⬛ 终止';
    }
}

// ── Create new chat ──
async function scSendNewChat() {
    const sel = document.getElementById('sc-client-select');
    const textarea = document.getElementById('sc-welcome-input');
    const sendBtn = document.getElementById('sc-welcome-send-btn');

    const clientId = sel ? parseInt(sel.value) : 0;
    const inputText = textarea ? textarea.value.trim() : '';

    if (!clientId) {
        showToast('请选择一个应用', 'error');
        return;
    }
    if (!inputText) {
        showToast('请输入内容', 'error');
        return;
    }

    localStorage.setItem(SC_CLIENT_CACHE_KEY, String(clientId));
    sendBtn.disabled = true;
    try {
        const res = await chatAPI.createStandaloneChatWithMessage(inputText, clientId);
        const newChatId = res.data.chat.id;
        scCurrentPage = 1;
        scChatList = [];
        await loadStandaloneChatList();
        await scSelectChat(newChatId, clientId);
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        sendBtn.disabled = false;
    }
}

// ── Delete chat ──
async function deleteStandaloneChat(chatId) {
    if (!confirm('确定要删除这个 Chat 吗？')) return;
    try {
        await chatAPI.deleteChat(0, chatId);
        showToast('删除成功', 'success');
        if (scSelectedChatId === chatId) {
            scShowWelcome();
        }
        scCurrentPage = 1;
        scChatList = [];
        loadStandaloneChatList();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ── Merge actions ──
function _scGetRepoName(url) {
    if (!url) return '';
    const m = url.match(/[/:]([\w.-]+?)(?:\.git)?$/);
    return m ? m[1] : url;
}

function _scBuildRepoTable(repos) {
    const lines = ['| 仓库 | 分支前缀 | 默认分支 | chat 分支 |', '|------|---------|---------|----------|'];
    for (const repo of repos) {
        const name = _scGetRepoName(repo.url);
        const prefix = repo.branch_prefix || 'ai_';
        const defaultBr = repo.default_branch || 'main';
        const chatBr = `${prefix}0_${scSelectedChatId}`;
        lines.push(`| ${name} | ${prefix} | ${defaultBr} | ${chatBr} |`);
    }
    return lines.join('\n');
}

function _scBuildMergeToDefaultBranchPrompt() {
    if (!scClientConfigCache || !scClientConfigCache.repos) return null;
    const repos = scClientConfigCache.repos;
    const repoTable = _scBuildRepoTable(repos);

    return `# 合并 Chat 分支到默认分支

## 背景信息

- chat_id: ${scSelectedChatId}
- 当前工作目录下有多个独立 git 仓库
- 本 Chat 不归属特定 Task，直接合并到默认分支

${repoTable}

## 操作步骤

对当前工作目录下的 **每一个 git 仓库** 执行以下操作：

1. **整理差异**：对比 chat 分支与默认分支的差异
2. **Rebase 合并**：将 chat 分支 rebase 到默认分支上，确保 commit 历史是线性的。推荐方式：
   - \`git rebase origin/<默认分支> <chat分支>\`
   - \`git checkout <默认分支>\`
   - \`git merge --ff-only <chat分支>\`
3. **推送默认分支**：\`git push origin <默认分支>\`
4. **关闭 PR**：如果 chat 分支在远端有对应的 PR，通过删除远端 chat 分支来关闭：\`git push origin --delete <chat分支>\`
5. **操作完成后**：切回 chat 分支继续工作

## 注意事项

- 如果 chat 分支与默认分支没有差异，跳过该仓库
- 每个仓库独立操作，一个失败不影响其他仓库
- 操作过程中如遇到冲突，尝试解决；无法解决时报告错误
- 确保默认分支的 commit 历史是清爽的线性记录
`;
}

async function scMergeToDefaultBranch() {
    if (!scSelectedChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const prompt = _scBuildMergeToDefaultBranchPrompt();
    if (!prompt) { showToast('未获取到仓库配置信息', 'error'); return; }

    const btn = document.getElementById('sc-merge-default-btn');
    btn.disabled = true;
    try {
        await chatAPI.createMessage(0, scSelectedChatId, prompt);
        await scLoadMessages(scSelectedChatId);
        scCurrentPage = 1;
        scChatList = [];
        await loadStandaloneChatList();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ── Merge request modal ──
function scShowMergeRequestModal(storeKey) {
    const body = document.getElementById('sc-merge-body');
    const data = scMergeRequestStore[storeKey];
    const list = Array.isArray(data) ? data : [];

    if (list.length === 0) {
        body.innerHTML = '<div class="sc-mr-empty">暂无变更记录</div>';
        document.getElementById('sc-merge-modal').classList.add('active');
        return;
    }

    body.innerHTML = `
        <table class="sc-mr-table">
            <thead>
                <tr><th>项目</th><th>分支</th><th>提交</th><th>PR</th></tr>
            </thead>
            <tbody>
                ${list.map(item => {
                    const repoName = item.repo_name || '';
                    const branchName = item.branch_name || '';
                    const commitId = item.latest_commitId || '';
                    const mergeUrl = item.merge_url || '';
                    const prLinks = mergeUrl
                        ? `<a href="${escapeHtml(mergeUrl)}" target="_blank" rel="noopener noreferrer">PR</a>`
                        : '';
                    const commitShort = commitId ? commitId.substring(0, 12) : '';
                    return `
                        <tr>
                            <td>${escapeHtml(repoName)}</td>
                            <td><code>${escapeHtml(branchName || '-')}</code></td>
                            <td>${commitShort ? `<code>${escapeHtml(commitShort)}</code>` : '-'}</td>
                            <td>${prLinks || '-'}</td>
                        </tr>`;
                }).join('')}
            </tbody>
        </table>`;

    document.getElementById('sc-merge-modal').classList.add('active');
}

function scCloseMergeRequestModal() {
    document.getElementById('sc-merge-modal').classList.remove('active');
}

// ===== 商店 =====

function initStore() {
    // 商店视图首次切换时由 switchToView 触发 loadStoreProducts
}

async function loadStoreProducts() {
    const grid = document.getElementById('store-products-grid');
    if (!grid) return;

    grid.innerHTML = '<p class="store-loading-tip">加载中…</p>';
    try {
        const resp = await commercialAPI.getProducts();
        const products = resp.data || [];
        if (products.length === 0) {
            grid.innerHTML = '<p class="store-empty-tip">暂无商品</p>';
            return;
        }
        grid.innerHTML = products.map(renderStoreProductCard).join('');
        grid.querySelectorAll('.store-buy-btn').forEach((btn) => {
            btn.addEventListener('click', handleStoreBuy);
        });
    } catch (err) {
        grid.innerHTML = `<p class="store-error-tip">加载失败：${escapeHtml(err.message)}</p>`;
    }
}

function renderStoreProductCard(product) {
    const iconHtml = product.icon
        ? `<img src="${escapeHtml(product.icon)}" alt="${escapeHtml(product.title)}" class="store-product-icon">`
        : `<div class="store-product-icon-placeholder">🛍️</div>`;
    const expireText = product.expire_time
        ? `有效期 ${Math.round(product.expire_time / 86400)} 天`
        : '永久有效';
    const renewBtn = product.support_continue
        ? `<button class="store-buy-btn btn-primary" data-id="${product.id}" data-type="renew">续费</button>`
        : '';
    return `
<div class="store-product-card">
  <div class="store-product-header">${iconHtml}</div>
  <div class="store-product-body">
    <div class="store-product-title">${escapeHtml(product.title)}</div>
    <div class="store-product-desc">${product.desc || ''}</div>
    <div class="store-product-meta">
      <span class="store-product-price">¥${product.price.toFixed(2)}</span>
      <span class="store-product-expire">${escapeHtml(expireText)}</span>
    </div>
  </div>
  <div class="store-product-actions">
    <button class="store-buy-btn btn-primary" data-id="${product.id}" data-type="purchase">购买</button>
    ${renewBtn}
  </div>
</div>`;
}

async function handleStoreBuy(e) {
    const btn = e.currentTarget;
    const productId = parseInt(btn.dataset.id, 10);
    const orderType = btn.dataset.type || 'purchase';

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = '跳转中…';
    try {
        const isMobile = /mobile|android|iphone|ipad/i.test(navigator.userAgent);
        const resp = await commercialAPI.buy(productId, orderType, isMobile ? 'mobile' : 'pc');
        const payUrl = resp.data && resp.data.pay_url;
        if (payUrl) {
            window.location.href = payUrl;
        } else {
            throw new Error('未获取到支付链接');
        }
    } catch (err) {
        showToast(`购买失败：${err.message}`, 'error');
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ===== 我的（个人中心）=====

let profileOrderPage = 1;

function loadProfileUserInfo() {
    const user = getCurrentUser();
    const nameEl = document.getElementById('profile-username');
    const idEl = document.getElementById('profile-user-id');
    if (nameEl) nameEl.textContent = (user && user.name) ? user.name : '用户';
    if (idEl) idEl.textContent = (user && user.user_id != null) ? String(user.user_id) : '—';
}

function initProfile() {
    // 初始化分页按钮
    const prevBtn = document.getElementById('profile-orders-prev-btn');
    const nextBtn = document.getElementById('profile-orders-next-btn');
    if (prevBtn && prevBtn.dataset.bound !== 'true') {
        prevBtn.addEventListener('click', () => loadMyOrders(profileOrderPage - 1));
        prevBtn.dataset.bound = 'true';
    }
    if (nextBtn && nextBtn.dataset.bound !== 'true') {
        nextBtn.addEventListener('click', () => loadMyOrders(profileOrderPage + 1));
        nextBtn.dataset.bound = 'true';
    }
}

async function loadMyServices() {
    const container = document.getElementById('profile-services-list');
    const emptyEl = document.getElementById('profile-services-empty');
    if (!container) return;

    container.innerHTML = '<p class="store-loading-tip">加载中…</p>';
    if (emptyEl) emptyEl.style.display = 'none';

    try {
        const resp = await commercialAPI.getMyServices();
        const services = resp.data || [];
        if (services.length === 0) {
            container.innerHTML = '';
            if (emptyEl) emptyEl.style.display = '';
            return;
        }
        container.innerHTML = services.map(renderServiceCard).join('');
    } catch (err) {
        container.innerHTML = `<p class="store-error-tip">加载失败：${escapeHtml(err.message)}</p>`;
    }
}

function renderServiceCard(service) {
    const iconHtml = service.product_icon
        ? `<img src="${escapeHtml(service.product_icon)}" alt="" class="service-card-icon">`
        : `<div class="service-card-icon-placeholder">✨</div>`;

    let expireHtml;
    if (service.is_permanent) {
        expireHtml = `<span class="service-expire-badge service-expire-permanent">永久有效</span>`;
    } else {
        const expireDate = new Date(service.expire_at);
        const now = new Date();
        const diffDays = Math.ceil((expireDate - now) / 86400000);
        const expireDateStr = expireDate.toLocaleDateString('zh-CN');
        const urgentClass = diffDays <= 7 ? 'service-expire-urgent' : 'service-expire-normal';
        expireHtml = `<span class="service-expire-badge ${urgentClass}">到期：${expireDateStr}（剩 ${diffDays} 天）</span>`;
    }

    return `
<div class="service-card">
  <div class="service-card-left">${iconHtml}</div>
  <div class="service-card-body">
    <div class="service-card-title">${escapeHtml(service.product_title)}</div>
    <div class="service-card-expire">${expireHtml}</div>
  </div>
</div>`;
}

async function loadMyOrders(page) {
    profileOrderPage = Math.max(1, page || 1);
    const tbody = document.getElementById('profile-orders-tbody');
    const emptyEl = document.getElementById('profile-orders-empty');
    const footer = document.getElementById('profile-orders-footer');
    if (!tbody) return;

    try {
        const resp = await commercialAPI.getMyOrders(profileOrderPage, 20);
        const data = resp.data || {};
        const orders = data.orders || [];
        const total = data.total || 0;
        const totalPages = Math.ceil(total / (data.page_size || 20));

        if (orders.length === 0 && profileOrderPage === 1) {
            tbody.innerHTML = '';
            if (emptyEl) emptyEl.style.display = '';
            if (footer) footer.style.display = 'none';
            return;
        }

        if (emptyEl) emptyEl.style.display = 'none';
        if (footer) footer.style.display = '';

        tbody.innerHTML = orders.map((order) => {
            const statusMap = { pending: '待支付', paid: '已支付', failed: '失败', refunded: '已退款' };
            const typeMap = { purchase: '购买', renew: '续费' };
            const expireText = order.expire_at
                ? new Date(order.expire_at).toLocaleDateString('zh-CN')
                : (order.status === 'paid' ? '永久' : '-');
            const statusClass = order.status === 'paid' ? 'status-paid'
                : order.status === 'refunded' ? 'status-refunded'
                : order.status === 'failed' ? 'status-failed'
                : 'status-pending';
            return `<tr>
  <td class="text-muted" style="font-size:12px;">${escapeHtml(order.out_trade_no || String(order.id))}</td>
  <td>${escapeHtml(order.product_title || order.product_key)}</td>
  <td>¥${Number(order.amount).toFixed(2)}</td>
  <td>${typeMap[order.order_type] || order.order_type}</td>
  <td><span class="order-status-badge ${statusClass}">${statusMap[order.status] || order.status}</span></td>
  <td>${expireText}</td>
  <td>${order.created_at ? new Date(order.created_at).toLocaleDateString('zh-CN') : '-'}</td>
</tr>`;
        }).join('');

        // 更新分页信息
        const totalInfoEl = document.getElementById('profile-orders-total-info');
        const pageInfoEl = document.getElementById('profile-orders-page-info');
        const prevBtn = document.getElementById('profile-orders-prev-btn');
        const nextBtn = document.getElementById('profile-orders-next-btn');

        if (totalInfoEl) totalInfoEl.textContent = `共 ${total} 条`;
        if (pageInfoEl) pageInfoEl.textContent = `第 ${profileOrderPage} / ${totalPages} 页`;
        if (prevBtn) prevBtn.disabled = profileOrderPage <= 1;
        if (nextBtn) nextBtn.disabled = profileOrderPage >= totalPages;
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--accent-danger);">加载失败：${escapeHtml(err.message)}</td></tr>`;
    }
}

// ===== 工具函数 =====

