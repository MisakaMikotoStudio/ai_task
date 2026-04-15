// clients-config-wizard.js - 客户端配置向导页面
// ===== 客户端配置向导 =====

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
    { id: 8, label: '部署',     required: false },
];

// 当前向导状态
let cfgClientId = null;      // null = 新建模式
let cfgClientMode = 'add';   // 'add' | 'edit' | 'view'
let cfgCurrentStep = 0;
let cfgReposList = [];

// 环境变量：扁平数组 [{key, value}, ...]
let cfgEnvVars = [];

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

// 部署配置：列表，不区分环境
let cfgDeploysList = [];
// 当前预览的 deploy 索引
let cfgDeployPreviewIdx = null;

function cfgResetClientConfigState() {
    cfgClientId = null;
    cfgClientMode = 'add';
    cfgCurrentStep = 0;
    cfgReposList = [];
    cfgEnvVars = [];
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
    cfgDeploysList = [];
    cfgDeployPreviewIdx = null;
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

            // 加载环境变量（扁平列表，忽略 env 字段）
            cfgEnvVars = (clientData.env_vars || []).map(ev => ({ key: ev.key || '', value: ev.value || '' }));

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
            cfgDeploysList = (infra.deploys || []).map(d => ({ ...d }));
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
        case 8: wizardRenderDeployStep(isView); break;
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
        case 8: wizardSyncDeploysFromDOM(); break;
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
    const envVars = cfgEnvVars.map(ev => ({ key: (ev.key || '').trim(), value: (ev.value == null ? '' : String(ev.value)) }));

    // 构建部署配置
    const deploys = cfgDeploysList.map(d => ({
        id: d.id || undefined,
        startup_command: (d.startup_command || '').trim(),
        official_configs: d.official_configs || [],
        custom_config: d.custom_config || '',
    }));

    // 构建基础设施配置
    const infrastructure = {
        servers: cfgServersByEnv,
        domains: cfgDomainsByEnv,
        databases: cfgDatabasesByEnv,
        payments: cfgPaymentsByEnv,
        oss: cfgOssByEnv,
        deploys: deploys,
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
    for (const ev of cfgEnvVars) {
        const key = (ev.key || '').trim();
        const value = (ev.value == null ? '' : String(ev.value));
        if (!key) return '存在空变量名';
        if (!value) return `变量 ${key} 的值不能为空`;
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
            cfgEnvVars.push({ key: '', value: '' });
            wizardRenderEnvVarsList();
        };
    }
    wizardRenderEnvVarsList();
}

function wizardSyncEnvVarsFromDOM() {
    const list = document.getElementById('env-vars-list');
    if (!list) return;
    const rows = list.querySelectorAll('.env-var-row');
    cfgEnvVars = [];
    rows.forEach(row => {
        const key = row.querySelector('.env-var-key-input')?.value || '';
        const value = row.querySelector('.env-var-val-input')?.value || '';
        cfgEnvVars.push({ key, value });
    });
}

function wizardRenderEnvVarsList() {
    const list = document.getElementById('env-vars-list');
    const empty = document.getElementById('env-vars-empty');
    if (!list) return;
    const isView = (cfgClientMode === 'view');
    const items = cfgEnvVars;

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
    cfgEnvVars.splice(idx, 1);
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


// ---- Step 8: 部署 ----

const DEPLOY_OFFICIAL_OPTIONS = [
    { key: 'app_name', label: '应用名' },
    { key: 'domain', label: '域名' },
    { key: 'database', label: '数据库' },
    { key: 'payment', label: '支付' },
    { key: 'oss', label: '对象存储' },
];

function wizardRenderDeployStep(isView) {
    const addBtn = document.getElementById('add-deploy-btn');
    if (addBtn) {
        addBtn.style.display = isView ? 'none' : '';
        addBtn.onclick = () => {
            cfgDeploysList.push({ startup_command: '', official_configs: [], custom_config: '' });
            wizardRenderDeployList();
        };
    }
    wizardRenderDeployList();
}

function wizardSyncDeploysFromDOM() {
    const list = document.getElementById('deploys-list');
    if (!list) return;
    list.querySelectorAll('.deploy-card').forEach((card, idx) => {
        if (idx >= cfgDeploysList.length) return;
        const d = cfgDeploysList[idx];
        const cmdInput = card.querySelector('.deploy-cmd-input');
        if (cmdInput) d.startup_command = cmdInput.value;
        const customInput = card.querySelector('.deploy-custom-input');
        if (customInput) d.custom_config = customInput.value;
        // 官方配置从 checkbox 同步
        const checks = card.querySelectorAll('.deploy-official-check');
        const selected = [];
        checks.forEach(cb => { if (cb.checked) selected.push(cb.value); });
        d.official_configs = selected;
    });
}

function wizardRenderDeployList() {
    const list = document.getElementById('deploys-list');
    const empty = document.getElementById('deploys-empty');
    if (!list) return;
    const isView = (cfgClientMode === 'view');

    if (cfgDeploysList.length === 0) {
        list.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';
    const disAttr = isView ? 'disabled' : '';

    list.innerHTML = cfgDeploysList.map((d, idx) => {
        const uuidDisplay = d.uuid ? `<span class="deploy-uuid-badge">${escapeHtml(d.uuid)}</span>` : '<span class="deploy-uuid-badge" style="color:var(--text-tertiary);">保存后生成</span>';
        const officialChecks = DEPLOY_OFFICIAL_OPTIONS.map(opt => {
            const checked = (d.official_configs || []).includes(opt.key) ? 'checked' : '';
            return `<label class="deploy-official-label"><input type="checkbox" class="deploy-official-check" value="${opt.key}" ${checked} ${disAttr}><span>${opt.label}</span></label>`;
        }).join('');
        const deleteBtn = isView ? '' : `<button type="button" class="btn-action btn-delete" onclick="cfgDeleteDeploy(${idx})">删除</button>`;
        const previewBtn = cfgClientId ? `<button type="button" class="btn-action" onclick="cfgPreviewDeploy(${idx})">预览</button>` : '';
        const executeBtn = (cfgClientId && d.id && !isView) ? `<button type="button" class="btn-action" onclick="cfgExecuteDeploy(${idx})">部署</button>` : '';

        return `
        <div class="deploy-card infra-card" data-deploy-idx="${idx}">
            <div class="infra-card-header">
                <span class="infra-card-label">#${idx + 1} ${uuidDisplay}</span>
                <div style="display:flex;gap:8px;align-items:center;">${previewBtn}${executeBtn}${deleteBtn}</div>
            </div>
            <div class="form-group">
                <label>启动命令</label>
                <input type="text" class="deploy-cmd-input" value="${escapeHtml(d.startup_command || '')}" placeholder="如 gunicorn ... main:app" ${disAttr}>
            </div>
            <div class="form-group">
                <label>官方配置</label>
                <div class="deploy-official-group">${officialChecks}</div>
            </div>
            <div class="form-group">
                <label>自定义配置（TOML 格式）</label>
                <textarea class="deploy-custom-input" rows="4" placeholder="在此输入 TOML 格式的自定义配置，会与官方配置合并（冲突时保留自定义）" ${disAttr}>${escapeHtml(d.custom_config || '')}</textarea>
            </div>
        </div>`;
    }).join('');

    // 绑定输入事件
    list.querySelectorAll('.deploy-card').forEach((card, idx) => {
        card.addEventListener('input', () => {
            if (idx >= cfgDeploysList.length) return;
            const d = cfgDeploysList[idx];
            const cmdInput = card.querySelector('.deploy-cmd-input');
            if (cmdInput) d.startup_command = cmdInput.value;
            const customInput = card.querySelector('.deploy-custom-input');
            if (customInput) d.custom_config = customInput.value;
        });
        card.querySelectorAll('.deploy-official-check').forEach(cb => {
            cb.addEventListener('change', () => {
                if (idx >= cfgDeploysList.length) return;
                const selected = [];
                card.querySelectorAll('.deploy-official-check').forEach(c => { if (c.checked) selected.push(c.value); });
                cfgDeploysList[idx].official_configs = selected;
            });
        });
    });
}

function cfgDeleteDeploy(idx) {
    cfgDeploysList.splice(idx, 1);
    wizardRenderDeployList();
}

async function cfgPreviewDeploy(idx) {
    if (!cfgClientId) { showToast('请先保存应用后再预览', 'error'); return; }
    wizardSyncDeploysFromDOM();
    const d = cfgDeploysList[idx];
    if (!d) return;
    cfgDeployPreviewIdx = idx;
    const modal = document.getElementById('deploy-preview-modal');
    if (modal) modal.style.display = 'flex';
    await refreshDeployPreview();
}

async function refreshDeployPreview() {
    if (cfgDeployPreviewIdx === null || cfgDeployPreviewIdx >= cfgDeploysList.length) return;
    const d = cfgDeploysList[cfgDeployPreviewIdx];
    const env = document.getElementById('deploy-preview-env').value || 'prod';
    const contentEl = document.getElementById('deploy-preview-content');
    contentEl.textContent = '加载中...';
    try {
        const result = await activeClientAPI.deployPreview(cfgClientId, {
            official_configs: d.official_configs || [],
            custom_config: d.custom_config || '',
            env: env,
        });
        contentEl.textContent = result.data.toml_content || '（空配置）';
    } catch (e) {
        contentEl.textContent = '预览失败：' + (e.message || '未知错误');
    }
}

function closeDeployPreviewModal() {
    const modal = document.getElementById('deploy-preview-modal');
    if (modal) modal.style.display = 'none';
    cfgDeployPreviewIdx = null;
}

async function cfgExecuteDeploy(idx) {
    if (!cfgClientId) { showToast('请先保存应用', 'error'); return; }
    const d = cfgDeploysList[idx];
    if (!d || !d.id) { showToast('请先保存配置后再执行部署', 'error'); return; }
    if (!confirm('确认执行部署？将通过 SSH 写入配置文件到远程服务器。')) return;
    try {
        const result = await activeClientAPI.deployExecute(cfgClientId, d.id);
        showToast(result.message || '部署完成', 'success');
    } catch (e) {
        showToast(e.message || '部署失败', 'error');
    }
}
