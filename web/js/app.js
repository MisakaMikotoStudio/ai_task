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

// 客户端数据缓存
let clientsCache = [];

// 任务列表自动刷新定时器
let tasksRefreshTimer = null;

// 当前状态筛选值
let currentStatusFilter = ['pending', 'running', 'suspended', 'completed'];

// 任务 flow_status 缓存（用于检测变化）
let taskFlowStatusCache = {};

// 浏览器通知是否已授权
let notificationPermission = 'default';

// 初始化应用
document.addEventListener('DOMContentLoaded', async () => {
    // 先加载API配置（获取后端地址）
    await initAPIConfig();

    initAuth();
    initTabs();
    initNavigation();
    initForms();
    initModals();
    initNotification();
});

// ===== 浏览器通知 =====

// 初始化通知权限
function initNotification() {
    // 检查浏览器是否支持通知
    if (!('Notification' in window)) {
        console.log('此浏览器不支持通知功能');
        return;
    }
    
    notificationPermission = Notification.permission;
    
    // 如果还没有请求过权限，在用户首次交互时请求
    if (notificationPermission === 'default') {
        // 在页面上添加一个提示，让用户点击授权
        document.addEventListener('click', requestNotificationPermission, { once: true });
    }
}

// 请求通知权限
async function requestNotificationPermission() {
    if (!('Notification' in window)) return;
    
    if (Notification.permission === 'default') {
        try {
            const permission = await Notification.requestPermission();
            notificationPermission = permission;
            if (permission === 'granted') {
                console.log('通知权限已授权');
            }
        } catch (error) {
            console.error('请求通知权限失败:', error);
        }
    }
}

// 发送浏览器通知
function sendNotification(title, body, taskKey) {
    if (!('Notification' in window)) return;
    
    if (Notification.permission !== 'granted') return;
    
    const notification = new Notification(title, {
        body: body,
        icon: 'favicon.ico', // 可选：添加图标
        tag: `task-${taskKey}`, // 相同 tag 的通知会合并
        requireInteraction: false
    });
    
    // 点击通知时聚焦到页面
    notification.onclick = function() {
        window.focus();
        notification.close();
    };
    
    // 5秒后自动关闭
    setTimeout(() => notification.close(), 5000);
}

// 检测 flow_status 变化并发送通知
function checkFlowStatusChanges(tasks) {
    if (Notification.permission !== 'granted') return;
    
    tasks.forEach(task => {
        const taskId = task.id;
        const currentFlowStatus = task.flow_status || '';
        const previousFlowStatus = taskFlowStatusCache[taskId];
        
        // 如果之前有缓存，且状态发生了变化
        if (previousFlowStatus !== undefined && previousFlowStatus !== currentFlowStatus) {
            sendNotification(
                `任务状态更新: ${task.key}`,
                `${task.title}\n执行状态: ${previousFlowStatus || '-'} → ${currentFlowStatus || '-'}`,
                task.key
            );
        }
        
        // 更新缓存
        taskFlowStatusCache[taskId] = currentFlowStatus;
    });
}

// ===== 认证相关 =====

function initAuth() {
    if (isLoggedIn()) {
        showMainPage();
        loadUserInfo();
    } else {
        showLoginPage();
    }
    
    logoutBtn.addEventListener('click', logout);
}

function showLoginPage() {
    loginPage.classList.add('active');
    mainPage.classList.remove('active');
    
    // 停止任务列表自动刷新
    stopTasksAutoRefresh();
}

function showMainPage() {
    loginPage.classList.remove('active');
    mainPage.classList.add('active');

    // 初始化任务筛选控件
    initTaskFilter();

    // 初始化待办事项
    initTodos();

    // 初始化秘钥管理
    initSecrets();

    // 初始化客户端搜索
    initClientSearch();

    // 加载数据
    loadClients();
    loadTasks();
    loadTodos();
    loadSecrets();

    // 启动任务列表自动刷新（每10秒）
    startTasksAutoRefresh();
}

async function loadUserInfo() {
    const user = getCurrentUser();
    if (user) {
        currentUsername.textContent = user.name;
    }
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
    let view = 'tasks'; // 默认视图
    
    if (hash.startsWith('#/')) {
        view = hash.substring(2); // 去掉 #/
    }
    
    // 验证视图是否存在
    if (!document.getElementById(`${view}-view`)) {
        view = 'tasks';
    }
    
    switchToView(view);
}

function switchToView(view) {
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
    if (view === 'okr') {
        loadObjectives();
        initOKREvents();
    } else if (view === 'secrets') {
        loadSecrets();
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
            setCurrentUser({id: userData.id, name: userData.name});

            showToast('登录成功', 'success');
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
    modal.classList.remove('modal-lg', 'modal-flow', 'modal-task-detail');
    
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
// 客户端分页状态
let clientsNextCursor = null;
let clientsHasMore = false;
let clientsLoading = false;
// 只看我的筛选
let clientsOnlyMine = false;
// 心跳记录缓存
let heartbeatMap = {};

// 初始化客户端搜索和筛选
function initClientSearch() {
    const searchInput = document.getElementById('client-search-input');
    const searchBtn = document.getElementById('client-search-btn');
    const clearBtn = document.getElementById('client-search-clear-btn');
    const onlyMineCheckbox = document.getElementById('client-only-mine');

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
                    showToast('请输入有效的客户端ID', 'error');
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

    // 只看我的筛选
    if (onlyMineCheckbox) {
        onlyMineCheckbox.addEventListener('change', () => {
            clientsOnlyMine = onlyMineCheckbox.checked;
            // 重新加载客户端列表
            resetAndLoadClients();
        });
    }

    // 初始化无限滚动
    initClientsInfiniteScroll();
}

// 初始化客户端列表无限滚动
function initClientsInfiniteScroll() {
    const tableContainer = document.querySelector('#clients-view .table-container');
    if (!tableContainer) return;

    tableContainer.addEventListener('scroll', () => {
        if (clientsLoading || !clientsHasMore) return;
        
        // 当滚动到底部附近时加载更多
        const scrollTop = tableContainer.scrollTop;
        const scrollHeight = tableContainer.scrollHeight;
        const clientHeight = tableContainer.clientHeight;
        
        if (scrollTop + clientHeight >= scrollHeight - 100) {
            loadMoreClients();
        }
    });
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
    clientsNextCursor = null;
    clientsHasMore = false;
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
        const [clientsResult, heartbeatsResult] = await Promise.all([
            clientAPI.list({ limit: 20, only_mine: clientsOnlyMine }),
            clientAPI.getHeartbeats()
        ]);
        
        const data = clientsResult.data || {};
        clientsCache = data.items || [];
        clientsNextCursor = data.next_cursor;
        clientsHasMore = data.has_more || false;
        
        const heartbeats = heartbeatsResult.data || [];

        // 创建心跳记录的映射 (client_id -> last_sync_at)
        heartbeatMap = {};
        heartbeats.forEach(hb => {
            heartbeatMap[hb.client_id] = hb.last_sync_at;
        });

        // 合并心跳时间到客户端数据
        clientsCache.forEach(client => {
            if (heartbeatMap[client.id]) {
                client.last_sync_at = heartbeatMap[client.id];
            }
        });

        renderClients(clientsCache);
        updateLoadMoreIndicator();
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        clientsLoading = false;
    }
}

// 加载更多客户端
async function loadMoreClients() {
    if (clientsLoading || !clientsHasMore || !clientsNextCursor) return;
    clientsLoading = true;
    
    updateLoadMoreIndicator(true);

    try {
        const result = await clientAPI.list({
            cursor: clientsNextCursor,
            limit: 20,
            only_mine: clientsOnlyMine
        });
        
        const data = result.data || {};
        const newItems = data.items || [];
        clientsNextCursor = data.next_cursor;
        clientsHasMore = data.has_more || false;

        // 合并心跳时间
        newItems.forEach(client => {
            if (heartbeatMap[client.id]) {
                client.last_sync_at = heartbeatMap[client.id];
            }
        });

        // 追加到缓存
        clientsCache = clientsCache.concat(newItems);
        
        // 追加渲染
        appendClients(newItems);
        updateLoadMoreIndicator();
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        clientsLoading = false;
    }
}

// 更新加载更多指示器
function updateLoadMoreIndicator(loading = false) {
    let indicator = document.getElementById('clients-load-more');
    if (!indicator) {
        // 创建指示器
        const tableContainer = document.querySelector('#clients-view .table-container');
        if (tableContainer) {
            indicator = document.createElement('div');
            indicator.id = 'clients-load-more';
            indicator.className = 'load-more-indicator';
            tableContainer.appendChild(indicator);
        }
    }
    
    if (indicator) {
        if (loading) {
            indicator.innerHTML = '<span class="loading-spinner"></span> 加载中...';
            indicator.style.display = 'block';
        } else if (clientsHasMore) {
            indicator.innerHTML = '向下滚动加载更多';
            indicator.style.display = 'block';
        } else if (clientsCache.length > 0) {
            indicator.innerHTML = '已加载全部';
            indicator.style.display = 'block';
        } else {
            indicator.style.display = 'none';
        }
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

// 追加渲染客户端行
function appendClients(clients) {
    const tbody = document.getElementById('clients-table-body');
    const emptyState = document.getElementById('clients-empty');
    
    if (clients.length === 0) return;
    
    emptyState.classList.remove('show');
    tbody.innerHTML += clients.map(client => renderClientRow(client)).join('');
}

// 渲染单个客户端行
function renderClientRow(client) {
    let actionsHtml = '';
    if (client.editable) {
        actionsHtml = `<button class="btn-action btn-edit" onclick="openClientConfig(${client.id}, 'edit')">编辑</button>
            <button class="btn-action btn-copy" onclick="copyClient(${client.id})">复制</button>
            <button class="btn-action btn-delete" onclick="deleteClient(${client.id})">删除</button>`;
    } else if (client.is_public) {
        actionsHtml = `<button class="btn-action btn-info" onclick="openClientConfig(${client.id}, 'view')">查看</button>`;
    } else {
        actionsHtml = '<span class="text-muted">只读</span>';
    }
    return `
    <tr>
        <td>${client.id}</td>
        <td><strong>${escapeHtml(client.name)}</strong></td>
        <td>${client.version ?? '-'}</td>
        <td>${escapeHtml(client.creator_name || '-')}</td>
        <td>${client.is_public ? '<span class="status-tag status-running">是</span>' : '<span class="status-tag status-pending">否</span>'}</td>
        <td class="time-display ${getHeartbeatClass(client.last_sync_at)}">${formatRelativeTime(client.last_sync_at)}</td>
        <td class="time-display">${formatDateTime(client.created_at)}</td>
        <td>${actionsHtml}</td>
    </tr>
`;
}

function getHeartbeatClass(lastSync) {
    if (!lastSync) return 'offline';
    const diff = new Date() - new Date(lastSync);
    return diff < 300000 ? 'online' : 'offline'; // 5分钟内为在线
}

async function deleteClient(id) {
    if (!confirm('确定要删除这个客户端吗？')) {
        return;
    }

    try {
        await clientAPI.delete(id);
        showToast('客户端删除成功', 'success');
        loadClients();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function copyClient(id) {
    try {
        const result = await clientAPI.copy(id);
        showToast(`客户端复制成功，新名称: ${result.name}`, 'success');
        loadClients();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ===== 客户端配置页面 =====

// 当前客户端配置页面状态
let cfgClientId = null;      // null = 新建模式
let cfgClientMode = 'add';   // 'add' | 'edit' | 'view'
let cfgReposList = [];
let cfgEnvVarsData = [];     // 已加载的环境变量列表（含 _editing/_new 标记）

function backToClients() {
    switchToView('clients');
    window.location.hash = '/clients';
    loadClients();
}

// 打开客户端配置页（替代弹窗）
async function openClientConfig(id, mode) {
    cfgClientId = id;
    cfgClientMode = mode;
    cfgReposList = [];
    cfgEnvVarsData = [];

    // 切换到 client-config-view
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('client-config-view').classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    // 设置标题
    const titleMap = { add: '新建客户端', edit: '编辑客户端', view: '查看客户端' };
    document.getElementById('client-config-title').textContent = titleMap[mode] || '客户端配置';

    // Tab 切换逻辑
    const tabBtns = document.querySelectorAll('.config-tab-btn');
    const tabPanels = document.querySelectorAll('.config-tab-panel');

    tabBtns.forEach(btn => {
        btn.onclick = () => {
            const tab = btn.dataset.configTab;
            tabBtns.forEach(b => b.classList.remove('active'));
            tabPanels.forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`client-tab-${tab}`).classList.add('active');
        };
    });

    // 默认显示基本信息 tab
    tabBtns.forEach(b => b.classList.remove('active'));
    tabPanels.forEach(p => p.classList.remove('active'));
    document.querySelector('[data-config-tab="basic"]').classList.add('active');
    document.getElementById('client-tab-basic').classList.add('active');

    // 返回按钮
    document.getElementById('client-config-back-btn').onclick = backToClients;

    // 新建模式下，环境变量和仓库 tab 不可用（需先保存基本信息）
    const envTab = document.getElementById('tab-btn-env-vars');
    const reposTab = document.getElementById('tab-btn-repos');
    if (mode === 'add') {
        envTab.disabled = true;
        reposTab.disabled = true;
        envTab.title = '请先保存基本信息';
        reposTab.title = '请先保存基本信息';
    } else {
        envTab.disabled = false;
        reposTab.disabled = false;
        envTab.title = '';
        reposTab.title = '';
    }

    // 加载 Agent 列表
    let agentOptions = ['claude sdk', 'claude cli'];
    try {
        const r = await clientAPI.getAgents();
        if (r.data && r.data.length > 0) agentOptions = r.data;
    } catch (e) { console.warn('获取Agent列表失败', e); }

    const agentSelect = document.getElementById('cfg-client-agent');
    const officialCloudDeploySelect = document.getElementById('cfg-client-official-cloud-deploy');
    agentSelect.innerHTML = agentOptions.map(a =>
        `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`
    ).join('');

    // 如果是编辑/查看模式，加载现有数据
    if (id !== null) {
        try {
            const [clientResult, reposResult, envVarsResult] = await Promise.all([
                clientAPI.get(id),
                clientAPI.getRepos(id),
                clientAPI.getEnvVars(id)
            ]);
            const clientData = clientResult.data;
            cfgReposList = (reposResult.data || []).map(r => ({...r}));
            cfgEnvVarsData = (envVarsResult.data || []).map(ev => ({...ev, _editing: false}));

            // 填充基本信息
            document.getElementById('cfg-client-name').value = clientData.name;
            document.getElementById('cfg-client-is-public').value = clientData.is_public ? 'true' : 'false';
            agentSelect.value = clientData.agent || 'claude sdk';
            officialCloudDeploySelect.value = String(clientData.official_cloud_deploy ?? 0);
        } catch (error) {
            showToast(error.message, 'error');
            return;
        }
    } else {
        document.getElementById('cfg-client-name').value = '';
        document.getElementById('cfg-client-is-public').value = 'false';
        agentSelect.value = agentOptions[0] || 'claude sdk';
        officialCloudDeploySelect.value = '0';
    }

    // 只读模式下禁用基本信息表单字段
    const basicInputs = document.querySelectorAll('#client-tab-basic input, #client-tab-basic select');
    basicInputs.forEach(el => { el.disabled = (mode === 'view'); });

    // 渲染/绑定基本信息表单
    cfgInitBasicForm();

    // 渲染环境变量 tab（提示 + 列表）
    cfgRenderEnvVarsTab();

    // 渲染仓库配置 tab
    cfgRenderReposTab();
}

// ---- 基本信息表单 ----

function cfgInitBasicForm() {
    const form = document.getElementById('client-basic-form');
    const submitBtn = document.getElementById('cfg-basic-submit-btn');

    if (cfgClientMode === 'view') {
        submitBtn.style.display = 'none';
        form.onsubmit = null;
        return;
    }

    submitBtn.style.display = '';
    submitBtn.textContent = cfgClientId === null ? '创建' : '保存';

    form.onsubmit = async (e) => {
        e.preventDefault();
        const name = document.getElementById('cfg-client-name').value.trim();
        const isPublic = document.getElementById('cfg-client-is-public').value === 'true';
        const agent = document.getElementById('cfg-client-agent').value;
        const officialCloudDeploy = parseInt(document.getElementById('cfg-client-official-cloud-deploy').value, 10) || 0;

        if (!name) { showToast('客户端名称不能为空', 'error'); return; }

        try {
            if (cfgClientId === null) {
                // 新建
                const result = await clientAPI.create(name, [], {
                    is_public: isPublic,
                    agent,
                    official_cloud_deploy: officialCloudDeploy
                });
                cfgClientId = result.data.id;
                cfgClientMode = 'edit';
                showToast('客户端创建成功，请继续配置环境变量和仓库', 'success');
                // 解锁其他 tab
                document.getElementById('tab-btn-env-vars').disabled = false;
                document.getElementById('tab-btn-repos').disabled = false;
                document.getElementById('tab-btn-env-vars').title = '';
                document.getElementById('tab-btn-repos').title = '';
                document.getElementById('cfg-basic-submit-btn').textContent = '保存';
                loadClients();
            } else {
                // 更新
                await clientAPI.update(cfgClientId, name, [], {
                    is_public: isPublic,
                    agent,
                    official_cloud_deploy: officialCloudDeploy
                });
                showToast('基本信息保存成功', 'success');
                loadClients();
            }
            // 刷新环境变量 tab
            cfgRenderEnvVarsTab();
        } catch (error) {
            showToast(error.message, 'error');
        }
    };
}

// ---- 环境变量管理 ----

function cfgRenderEnvVarsTab() {
    const tipEl = document.getElementById('env-vars-tip');
    const addBtn = document.getElementById('add-env-var-btn');

    if (tipEl) {
        tipEl.textContent = '注意：仅在docker方式启动客户端或者使用cloud客户端时，环境变量才有效';
        tipEl.className = 'config-section-tip tip-warn';
    }

    // 只读或无 id 时隐藏添加按钮
    if (addBtn) {
        addBtn.style.display = (cfgClientMode === 'view' || cfgClientId === null) ? 'none' : '';
        addBtn.onclick = cfgAddEnvVar;
    }

    cfgRenderEnvVarsList();
}

function cfgRenderEnvVarsList() {
    const list = document.getElementById('env-vars-list');
    const empty = document.getElementById('env-vars-empty');
    if (!list) return;

    const activeVars = cfgEnvVarsData.filter(ev => !ev._deleted);

    if (activeVars.length === 0) {
        list.innerHTML = '';
        empty.style.display = '';
        return;
    }
    empty.style.display = 'none';

    list.innerHTML = activeVars.map((ev, idx) => {
        if (ev._editing || ev._isNew) {
            return `
            <div class="env-var-row env-var-row-editing" data-idx="${idx}">
                <input class="env-var-key-input" type="text" placeholder="变量名（如 MY_KEY）" value="${escapeHtml(ev.key || '')}">
                <span class="env-var-eq">=</span>
                <input class="env-var-val-input" type="text" placeholder="变量值" value="${escapeHtml(ev.value || '')}">
                <button class="btn-action btn-save-sm" onclick="cfgSaveEnvVar(${idx})">保存</button>
                <button class="btn-action btn-cancel-sm" onclick="cfgCancelEnvVar(${idx})">取消</button>
            </div>`;
        }
        const actions = cfgClientMode !== 'view' ? `
            <button class="btn-action btn-edit" onclick="cfgEditEnvVar(${idx})">编辑</button>
            <button class="btn-action btn-delete" onclick="cfgDeleteEnvVar(${idx})">删除</button>` : '';
        return `
        <div class="env-var-row" data-idx="${idx}">
            <span class="env-var-key">${escapeHtml(ev.key)}</span>
            <span class="env-var-eq">=</span>
            <span class="env-var-val">${escapeHtml(ev.value || '')}</span>
            <div class="env-var-actions">${actions}</div>
        </div>`;
    }).join('');
}

function cfgAddEnvVar() {
    cfgEnvVarsData.push({ id: null, key: '', value: '', _editing: false, _isNew: true });
    cfgRenderEnvVarsList();
}

function cfgEditEnvVar(idx) {
    cfgEnvVarsData[idx]._editing = true;
    cfgRenderEnvVarsList();
}

function cfgCancelEnvVar(idx) {
    if (cfgEnvVarsData[idx]._isNew) {
        cfgEnvVarsData.splice(idx, 1);
    } else {
        cfgEnvVarsData[idx]._editing = false;
    }
    cfgRenderEnvVarsList();
}

async function cfgSaveEnvVar(idx) {
    const row = document.querySelector(`.env-var-row[data-idx="${idx}"]`);
    const key = row.querySelector('.env-var-key-input').value.trim();
    const value = row.querySelector('.env-var-val-input').value;
    if (!key) { showToast('变量名不能为空', 'error'); return; }

    try {
        const ev = cfgEnvVarsData[idx];
        if (ev._isNew) {
            const result = await clientAPI.createEnvVar(cfgClientId, key, value);
            ev.id = result.data.id;
            ev._isNew = false;
        } else {
            await clientAPI.updateEnvVar(cfgClientId, ev.id, key, value);
        }
        ev.key = key;
        ev.value = value;
        ev._editing = false;
        cfgRenderEnvVarsList();
        showToast('环境变量保存成功', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function cfgDeleteEnvVar(idx) {
    if (!confirm('确定删除该环境变量？')) return;
    try {
        const ev = cfgEnvVarsData[idx];
        if (ev.id) {
            await clientAPI.deleteEnvVar(cfgClientId, ev.id);
        }
        cfgEnvVarsData.splice(idx, 1);
        cfgRenderEnvVarsList();
        showToast('环境变量已删除', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ---- 代码仓库管理 ----

function cfgRenderReposTab() {
    const addBtn = document.getElementById('cfg-add-repo-btn');
    const saveBtn = document.getElementById('cfg-repos-save-btn');
    const isView = (cfgClientMode === 'view');

    if (addBtn) {
        addBtn.style.display = isView ? 'none' : '';
        addBtn.onclick = () => {
            const isFirst = cfgReposList.length === 0;
            cfgReposList.push({ desc: '', url: '', token: '', default_branch: '', branch_prefix: 'ai_', docs_repo: isFirst });
            cfgRenderReposWaterfall();
        };
    }
    if (saveBtn) {
        saveBtn.style.display = (isView || cfgClientId === null) ? 'none' : '';
        saveBtn.onclick = cfgSaveRepos;
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

async function cfgSaveRepos() {
    if (!cfgClientId) { showToast('请先保存基本信息', 'error'); return; }
    try {
        await clientAPI.updateRepos(cfgClientId, cfgReposList);
        showToast('仓库配置保存成功', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ===== 任务管理 =====

// 初始化任务筛选控件
function initTaskFilter() {
    const statusFilter = document.getElementById('status-filter');
    const checkboxes = statusFilter.querySelectorAll('input[type="checkbox"]');
    
    checkboxes.forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            // 更新按钮样式
            const label = checkbox.parentElement;
            if (checkbox.checked) {
                label.classList.add('checked');
            } else {
                label.classList.remove('checked');
            }
            
            // 获取所有选中的值
            currentStatusFilter = Array.from(checkboxes)
                .filter(cb => cb.checked)
                .map(cb => cb.value);
            loadTasks();
        });
    });
}

// 启动任务列表自动刷新
function startTasksAutoRefresh() {
    // 清除已存在的定时器
    stopTasksAutoRefresh();
    
    // 每10秒刷新一次
    tasksRefreshTimer = setInterval(() => {
        loadTasks();
    }, 10000);
}

// 停止任务列表自动刷新
function stopTasksAutoRefresh() {
    if (tasksRefreshTimer) {
        clearInterval(tasksRefreshTimer);
        tasksRefreshTimer = null;
    }
}

async function loadTasks() {
    try {
        const result = await taskAPI.list();
        let allTasks = result.data || [];
        
        // 检测 flow_status 变化并发送通知（使用全部任务，不受筛选影响）
        checkFlowStatusChanges(allTasks);
        
        // 根据状态筛选（仅用于显示）
        let tasks = allTasks;
        if (currentStatusFilter.length > 0) {
            tasks = allTasks.filter(task => currentStatusFilter.includes(task.status));
        }
        
        // 按状态排序：进行中 > 未开始 > 已结束
        const statusOrder = { 'running': 0, 'pending': 1, 'suspended': 2, 'completed': 3 };
        tasks.sort((a, b) => {
            const orderA = statusOrder[a.status] ?? 99;
            const orderB = statusOrder[b.status] ?? 99;
            return orderA - orderB;
        });
        
        renderTasks(tasks);
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function renderTasks(tasks) {
    const tbody = document.getElementById('tasks-table-body');
    const emptyState = document.getElementById('tasks-empty');

    if (tasks.length === 0) {
        tbody.innerHTML = '';
        emptyState.classList.add('show');
        return;
    }

    emptyState.classList.remove('show');

    // 缓存任务数据用于弹窗显示
    window.tasksCache = tasks.reduce((acc, t) => { acc[t.id] = t; return acc; }, {});

    tbody.innerHTML = tasks.map(task => {
        // 直接使用后端返回的 flow_status 字段
        const flowStatusText = task.flow_status || '-';
        
        // 判断是否是 client_error 状态，如果是则添加错误提示按钮
        const isClientError = task.flow_status === 'client_error';
        let flowStatusHtml = '';
        if (isClientError) {
            // 从 flow.error 获取错误信息
            const errorMsg = task.flow && task.flow.error ? task.flow.error : '未知错误';
            flowStatusHtml = `<span class="flow-status-error" title="${escapeHtml(errorMsg)}">${flowStatusText} <button class="btn-error-detail" onclick="showClientErrorDetail(${task.id})">查看</button></span>`;
        } else {
            flowStatusHtml = flowStatusText;
        }

        return `
        <tr>
            <td><span class="task-id">${task.id ?? '-'}</span></td>
            <td>${escapeHtml(task.title)}</td>
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
            <td>
                <button class="btn-action btn-chat" onclick="openTaskChat(${task.id})">Chat</button>
                <button class="btn-action btn-delete" onclick="deleteTask(${task.id})">删除</button>
                <button class="btn-action btn-reset" onclick="resetTask(${task.id})">重置</button>
            </td>
        </tr>
    `}).join('');
}

// 解析任务desc为结构化数据（兼容历史数据）
function parseTaskDesc(desc) {
    if (!desc) return { links: [], desc: '' };
    try {
        const parsed = JSON.parse(desc);
        return {
            links: Array.isArray(parsed.links) ? parsed.links : [],
            desc: typeof parsed.desc === 'string' ? parsed.desc : ''
        };
    } catch (e) {
        // JSON解析失败，作为纯文本处理
        return { links: [], desc: desc };
    }
}

// 统一的任务编辑弹窗状态
let taskEditCurrentId = null;  // null 表示新建模式
let taskEditMode = false;      // false 表示查看模式，true 表示编辑模式
let taskEditLinks = [];
let taskEditDesc = '';
let usableClientsCache = [];   // 可用于创建任务的客户端列表

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
        
        const parsedDesc = parseTaskDesc(task.desc);
        taskEditLinks = [...parsedDesc.links];
        taskEditDesc = parsedDesc.desc;
        taskEditMode = startInEditMode;  // 默认查看模式
        
        renderTaskEditModal(task);
    } else {
        // 创建模式 - 初始化空数据并获取可用客户端列表
        taskEditLinks = [];
        taskEditDesc = '';
        taskEditMode = true;  // 创建模式始终是编辑模式
        
        // 获取可用客户端列表
        try {
            const result = await clientAPI.listUsable();
            usableClientsCache = result.data || [];
        } catch (error) {
            console.warn('获取可用客户端列表失败:', error);
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
        // 获取可用客户端列表（编辑模式下需要选择客户端）
        try {
            const result = await clientAPI.listUsable();
            usableClientsCache = result.data || [];
        } catch (error) {
            console.warn('获取可用客户端列表失败:', error);
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
            const parsedDesc = parseTaskDesc(task.desc);
            taskEditLinks = [...parsedDesc.links];
            taskEditDesc = parsedDesc.desc;
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
            `<option value="${c.id}" data-types='${JSON.stringify(c.types || [])}'>${escapeHtml(c.name)}</option>`
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
                    <label>关联客户端 <span class="required">*</span></label>
                    <select id="task-edit-client" class="status-select" required>
                        <option value="">请选择客户端</option>
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
                    <label>关联客户端</label>
                    <select id="task-edit-client" class="status-select">
                        <option value="0" ${!task.client_id ? 'selected' : ''}>不指定客户端</option>
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
                    <label>关联客户端</label>
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
    
    // 链接区域
    let linksHtml = '';
    if (isEditing) {
        // 编辑模式 - 可编辑链接
        linksHtml = `
            <div class="form-group">
                <label>相关链接 <span class="text-muted">(agent执行不会使用)</span></label>
                <div class="links-editor">
                    <table class="types-table" id="links-table">
                        <thead>
                            <tr>
                                <th>标题</th>
                                <th>链接</th>
                                <th style="width: 80px;">操作</th>
                            </tr>
                        </thead>
                        <tbody id="links-tbody">
                            ${taskEditLinks.map((link, index) => `
                                <tr>
                                    <td><input type="text" class="link-title-input" data-index="${index}" value="${escapeHtml(link.title || '')}" placeholder="链接标题"></td>
                                    <td><input type="text" class="link-url-input" data-index="${index}" value="${escapeHtml(link.url || '')}" placeholder="链接地址"></td>
                                    <td><button type="button" class="btn-small btn-delete" onclick="removeTaskEditLink(${index})">删除</button></td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                    <button type="button" class="btn-small btn-add" onclick="addTaskEditLink()" style="margin-top: 8px;">+ 添加链接</button>
                </div>
            </div>
        `;
    } else {
        // 查看模式 - 只读链接
        const linksContent = taskEditLinks.length > 0
            ? `<div class="task-links-list">${taskEditLinks.map(link => 
                `<a href="${escapeHtml(link.url || '#')}" target="_blank" class="task-link-item">${escapeHtml(link.title || link.url || '未命名链接')}</a>`
            ).join('')}</div>`
            : '<span class="text-muted">暂无相关链接</span>';
        
        linksHtml = `
            <div class="form-group">
                <label>相关链接</label>
                ${linksContent}
            </div>
        `;
    }
    
    // 描述区域
    let descHtml = '';
    if (isCreateMode || isEditing) {
        // 创建/编辑模式 - 可编辑描述
        descHtml = `
            <div class="form-group">
                <label>任务描述 <span class="required">*</span></label>
                <textarea id="task-edit-desc" placeholder="请输入任务描述" required>${escapeHtml(taskEditDesc)}</textarea>
            </div>
        `;
    } else {
        // 查看模式 - 描述只读
        const descContent = taskEditDesc 
            ? `<div class="task-desc-text">${escapeHtml(taskEditDesc)}</div>`
            : '<span class="text-muted">暂无任务描述</span>';
        
        descHtml = `
            <div class="form-group">
                <label>任务描述</label>
                ${descContent}
            </div>
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
                ${isCreateMode ? '' : linksHtml}
                ${isCreateMode ? '' : descHtml}
            </div>
            ${actionsHtml}
        </div>
    `;
    
    openModal(modalTitle, content, 'modal-task-edit');
    
    // 绑定事件
    if (isEditing) {
        bindTaskEditEvents(isCreateMode);
    }
}

// 绑定任务编辑弹窗事件
function bindTaskEditEvents(isCreateMode) {
    // 绑定链接输入事件
    document.querySelectorAll('.link-title-input').forEach(input => {
        input.addEventListener('input', (e) => {
            const index = parseInt(e.target.dataset.index);
            if (taskEditLinks[index]) {
                taskEditLinks[index].title = e.target.value;
            }
        });
    });
    
    document.querySelectorAll('.link-url-input').forEach(input => {
        input.addEventListener('input', (e) => {
            const index = parseInt(e.target.dataset.index);
            if (taskEditLinks[index]) {
                taskEditLinks[index].url = e.target.value;
            }
        });
    });
    
    // 创建模式不再需要客户端选择事件，因为任务类型已改为手动输入
}

// 添加链接
function addTaskEditLink() {
    taskEditLinks.push({ title: '', url: '' });
    const task = taskEditCurrentId ? (window.tasksCache && window.tasksCache[taskEditCurrentId]) : null;
    renderTaskEditModal(task);
}

// 删除链接
function removeTaskEditLink(index) {
    taskEditLinks.splice(index, 1);
    const task = taskEditCurrentId ? (window.tasksCache && window.tasksCache[taskEditCurrentId]) : null;
    renderTaskEditModal(task);
}

// 保存任务编辑
async function saveTaskEdit() {
    const isCreateMode = taskEditCurrentId === null;
    
    // 获取描述内容
    const descTextarea = document.getElementById('task-edit-desc');
    if (descTextarea) {
        taskEditDesc = descTextarea.value;
    }

    taskEditDesc = (taskEditDesc ?? '').trim();
    if (!isCreateMode && !taskEditDesc) {
        showToast('请输入任务描述', 'error');
        return;
    }

    // 过滤掉空的链接
    const validLinks = taskEditLinks.filter(link => link.title || link.url);
    
    // 构建desc JSON
    const descJson = JSON.stringify({
        links: validLinks,
        desc: taskEditDesc
    });
    
    if (isCreateMode) {
        // 创建模式 - 验证并创建
        const title = document.getElementById('task-edit-title')?.value.trim();
        const clientId = document.getElementById('task-edit-client')?.value;
        const type = document.getElementById('task-edit-type')?.value.trim();
        
        if (!title) {
            showToast('请输入任务标题', 'error');
            return;
        }
        
        if (!clientId) {
            showToast('请选择客户端', 'error');
            return;
        }
        
        try {
            const selectedStatus = document.getElementById('task-edit-status')?.value || 'running';
            const parsedClientId = parseInt(clientId);
            await taskAPI.create(title, type, parsedClientId, descJson, selectedStatus);
            showToast('任务创建成功', 'success');
            closeModal();
            loadTasks();
        } catch (error) {
            showToast(error.message, 'error');
        }
    } else {
        // 编辑模式 - 更新客户端/状态/描述（标题不可编辑）
        try {
            const newStatus = document.getElementById('task-edit-status')?.value;
            const newClientId = document.getElementById('task-edit-client')?.value;
            const parsedClientId = newClientId ? parseInt(newClientId) : 0;
            
            // 更新描述（包含相关链接）
            await taskAPI.updateDesc(taskEditCurrentId, descJson);

            // 更新客户端
            await taskAPI.updateClient(taskEditCurrentId, parsedClientId);
            
            // 更新状态
            if (newStatus) {
                await taskAPI.updateStatus(taskEditCurrentId, newStatus);
            }
            
            showToast('任务保存成功', 'success');

            // 更新缓存
            if (window.tasksCache && window.tasksCache[taskEditCurrentId]) {
                window.tasksCache[taskEditCurrentId].client_id = parsedClientId;
                window.tasksCache[taskEditCurrentId].desc = descJson;
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
        // 1. 创建新任务（使用原任务的标题、描述、客户端、任务类型）
        const createResult = await taskAPI.create(
            task.title,
            task.type,
            task.client_id,
            task.desc || null
        );
        
        const newTaskId = createResult.data.id;
        
        // 2. 如果原任务状态不是默认的 pending，更新新任务的状态
        if (task.status && task.status !== 'pending') {
            await taskAPI.updateStatus(newTaskId, task.status);
        }
        
        // 3. 删除旧任务
        await taskAPI.delete(id);
        
        showToast('任务重置成功', 'success');
        loadTasks();
    } catch (error) {
        showToast('任务重置失败：' + error.message, 'error');
        loadTasks(); // 重新加载以刷新列表状态
    }
}

// 重试任务：将 flow_status 改为 pending
async function retryTask(taskId) {
    try {
        await taskAPI.updateFlow(taskId, null, 'pending');
        showToast('任务已重新加入队列', 'success');
        loadTasks();
    } catch (error) {
        showToast('重试失败：' + error.message, 'error');
    }
}

// 审核通过任务
async function approveTask(taskId) {
    if (!confirm('确定要通过审核吗？')) {
        return;
    }
    
    try {
        await taskAPI.review(taskId, 'approve');
        showToast('审核通过成功', 'success');
        loadTasks();
    } catch (error) {
        showToast('审核通过失败：' + error.message, 'error');
    }
}

// 显示修订反馈弹窗
let reviseTaskId = null;

function showReviseModal(taskId) {
    reviseTaskId = taskId;
    const modal = document.getElementById('revise-modal');
    const textarea = document.getElementById('revise-feedback');
    textarea.value = '';
    modal.classList.add('show');
    textarea.focus();
}

function hideReviseModal() {
    const modal = document.getElementById('revise-modal');
    modal.classList.remove('show');
    reviseTaskId = null;
}

async function submitRevise() {
    const feedback = document.getElementById('revise-feedback').value.trim();
    
    if (!feedback) {
        showToast('请填写反馈内容', 'error');
        return;
    }
    
    try {
        await taskAPI.review(reviseTaskId, 'revise', feedback);
        showToast('已提交修订反馈', 'success');
        hideReviseModal();
        loadTasks();
    } catch (error) {
        showToast('提交修订失败：' + error.message, 'error');
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
        'client_error': '客户端异常'
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

// 兼容旧的调用方式
function showAddTaskModal() {
    showTaskEditModal(null);
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
        const result = await secretAPI.list();
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
        await secretAPI.delete(id);
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
            await secretAPI.create(name);
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

// ===== Chat 跳转 =====

function openTaskChat(taskId) {
    window.open(`chat.html?task_id=${taskId}`, '_blank');
}

// ===== 工具函数 =====

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

