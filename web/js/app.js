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
const ADMIN_ALLOWED_VIEWS = new Set(['clients', 'secrets', 'products', 'orders', 'permissions', 'resources']);

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
        document.querySelectorAll('.nav-item[data-view=”products”], .nav-item[data-view=”orders”], .nav-item[data-view=”permissions”]').forEach((el) => {
            el.style.display = '';
        });
        initAdminCommerce();
        initAdminPermissions();
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

    // 初始化个人中心
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
    } else if (view === 'permissions') {
        loadAdminPermissions();
    } else if (view === 'resources') {
        loadAdminResources();
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
        showAddClientModal();
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
