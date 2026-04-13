// clients.js - 客户端管理与配置向导
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

// 显示添加应用选择弹窗
function showAddClientModal() {
    const content = `
        <div class="add-client-choice">
            <div class="choice-group">
                <label class="choice-option">
                    <input type="radio" name="add-client-method" value="manual" checked>
                    <span class="choice-label">手动填写应用配置</span>
                    <span class="choice-desc">自定义配置应用的所有信息</span>
                </label>
                <label class="choice-option">
                    <input type="radio" name="add-client-method" value="template">
                    <span class="choice-label">从模板生成默认应用</span>
                    <span class="choice-desc">快速创建包含默认数据库配置的应用</span>
                </label>
            </div>
            <div id="template-config-section" style="display:none; margin-top: 16px;">
                <div class="form-group">
                    <label class="form-label">应用名称</label>
                    <input type="text" id="template-app-name" class="form-input" placeholder="留空则自动生成" maxlength="16">
                </div>
                <div class="form-group">
                    <label class="form-label">应用形态（多选）</label>
                    <div class="checkbox-group">
                        <label class="checkbox-option">
                            <input type="checkbox" name="app-type" value="web" checked>
                            <span>web - 网站</span>
                        </label>
                    </div>
                </div>
            </div>
            <div class="modal-actions" style="margin-top: 20px; display: flex; justify-content: flex-end; gap: 10px;">
                <button class="btn-secondary" id="add-client-modal-cancel">取消</button>
                <button class="btn-primary" id="add-client-modal-confirm">确认</button>
            </div>
        </div>
    `;
    openModal('添加应用', content);

    // 切换模板/手动时显示/隐藏模板配置区
    const radios = document.querySelectorAll('input[name="add-client-method"]');
    const templateSection = document.getElementById('template-config-section');
    const confirmBtn = document.getElementById('add-client-modal-confirm');
    radios.forEach(radio => {
        radio.addEventListener('change', () => {
            const isTemplate = document.querySelector('input[name="add-client-method"]:checked').value === 'template';
            templateSection.style.display = isTemplate ? 'block' : 'none';
            confirmBtn.textContent = isTemplate ? '创建' : '确认';
        });
    });

    // 取消按钮
    document.getElementById('add-client-modal-cancel').addEventListener('click', () => {
        closeModal();
    });

    // 确认/创建按钮
    confirmBtn.addEventListener('click', async () => {
        const method = document.querySelector('input[name="add-client-method"]:checked').value;
        if (method === 'manual') {
            closeModal();
            openClientConfig(null, 'add');
        } else {
            // 模板创建
            const appTypeCheckboxes = document.querySelectorAll('input[name="app-type"]:checked');
            const appTypes = Array.from(appTypeCheckboxes).map(cb => cb.value);
            if (appTypes.length === 0) {
                showToast('请选择至少一种应用形态', 'error');
                return;
            }
            const appName = (document.getElementById('template-app-name').value || '').trim();
            confirmBtn.disabled = true;
            confirmBtn.textContent = '创建中...';
            try {
                await activeClientAPI.createFromTemplate(appTypes, appName);
                closeModal();
                showToast('应用创建成功', 'success');
                loadClients();
            } catch (error) {
                showToast(error.message || '创建失败', 'error');
                confirmBtn.disabled = false;
                confirmBtn.textContent = '创建';
            }
        }
    });
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
    const genBtn = document.getElementById('gen-default-db-btn');
    if (genBtn) {
        genBtn.style.display = isView ? 'none' : '';
        genBtn.onclick = async () => {
            genBtn.disabled = true;
            genBtn.textContent = '创建中...';
            try {
                const apiObj = (typeof adminClientAPI !== 'undefined' && window.location.hash.includes('admin')) ? adminClientAPI : clientAPI;
                const res = await apiObj.generateDefaultDatabase();
                if (res.code === 200 && res.data) {
                    cfgDatabasesByEnv[cfgDatabaseCurrentEnv].push(res.data);
                    wizardRenderDatabaseList();
                    showToast('默认数据库创建成功：' + res.data.db_name, 'success');
                } else {
                    showToast(res.message || '创建失败', 'error');
                }
            } catch (e) {
                showToast(e.message || '创建数据库失败', 'error');
            } finally {
                genBtn.disabled = false;
                genBtn.textContent = '生成默认数据库';
            }
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
