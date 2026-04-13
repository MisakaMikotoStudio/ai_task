// admin.js - 权限配置管理、资源管理（admin）

// ===== 权限配置管理（admin） =====

let _permEditId = null; // null=新增, number=编辑

function initAdminPermissions() {
    const openBtn = document.getElementById('open-create-permission-btn');
    if (openBtn && openBtn.dataset.bound !== 'true') {
        openBtn.addEventListener('click', () => showPermissionModal(null));
        openBtn.dataset.bound = 'true';
    }

    const closeBtn = document.getElementById('close-permission-modal');
    if (closeBtn && closeBtn.dataset.bound !== 'true') {
        closeBtn.addEventListener('click', hidePermissionModal);
        closeBtn.dataset.bound = 'true';
    }

    const cancelBtn = document.getElementById('cancel-permission-btn');
    if (cancelBtn && cancelBtn.dataset.bound !== 'true') {
        cancelBtn.addEventListener('click', hidePermissionModal);
        cancelBtn.dataset.bound = 'true';
    }

    const typeSelect = document.getElementById('perm-type');
    if (typeSelect && typeSelect.dataset.bound !== 'true') {
        typeSelect.addEventListener('change', () => {
            const limitGroup = document.getElementById('perm-limit-group');
            limitGroup.style.display = typeSelect.value === 'count_limit' ? '' : 'none';
        });
        typeSelect.dataset.bound = 'true';
    }

    const form = document.getElementById('permission-form');
    if (form && form.dataset.bound !== 'true') {
        form.addEventListener('submit', handlePermissionSubmit);
        form.dataset.bound = 'true';
    }
}

function showPermissionModal(editItem) {
    _permEditId = editItem ? editItem.id : null;
    const modal = document.getElementById('permission-modal');
    const title = document.getElementById('permission-modal-title');
    const keyInput = document.getElementById('perm-key');
    const typeSelect = document.getElementById('perm-type');
    const productKeyInput = document.getElementById('perm-product-key');
    const limitInput = document.getElementById('perm-limit');
    const limitGroup = document.getElementById('perm-limit-group');

    title.textContent = editItem ? '编辑权限配置' : '新增权限配置';

    if (editItem) {
        keyInput.value = editItem.key || '';
        typeSelect.value = editItem.type || 'subscribed';
        productKeyInput.value = editItem.product_key || '';
        const detail = editItem.config_detail || {};
        limitInput.value = detail.limit != null ? detail.limit : '';
    } else {
        keyInput.value = '';
        typeSelect.value = 'subscribed';
        productKeyInput.value = '';
        limitInput.value = '';
    }

    limitGroup.style.display = typeSelect.value === 'count_limit' ? '' : 'none';
    modal.style.display = '';
}

function hidePermissionModal() {
    document.getElementById('permission-modal').style.display = 'none';
    _permEditId = null;
}

async function handlePermissionSubmit(e) {
    e.preventDefault();
    const key = document.getElementById('perm-key').value.trim();
    const type = document.getElementById('perm-type').value;
    const productKey = document.getElementById('perm-product-key').value.trim();
    const limitVal = document.getElementById('perm-limit').value.trim();

    if (!key || !type || !productKey) { alert('请填写所有必填项'); return; }

    if (!/^[a-zA-Z0-9_-]+$/.test(key)) { alert('权限 Key 仅允许字母、数字、下划线、短横线'); return; }
    if (!/^[a-zA-Z0-9_-]+$/.test(productKey)) { alert('产品 Key 仅允许字母、数字、下划线、短横线'); return; }

    const payload = { key, type, product_key: productKey };

    if (type === 'count_limit') {
        const limit = parseInt(limitVal, 10);
        if (!limit || limit <= 0) { alert('count_limit 类型必须填写正整数的数量上限'); return; }
        payload.config_detail = { limit };
    } else {
        payload.config_detail = {};
    }

    const saveBtn = document.getElementById('save-permission-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';

    try {
        let res;
        if (_permEditId) {
            res = await adminPermissionAPI.update(_permEditId, payload);
        } else {
            res = await adminPermissionAPI.create(payload);
        }
        if (res.code === 200 || res.code === 201) {
            hidePermissionModal();
            loadAdminPermissions();
        } else {
            alert(res.message || '保存失败');
        }
    } catch (err) {
        alert(err.message || '请求失败');
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
    }
}

async function loadAdminPermissions() {
    const container = document.getElementById('permissions-list');
    if (!container) return;
    container.innerHTML = '<p class="perm-loading">加载中…</p>';

    try {
        const res = await adminPermissionAPI.list();
        if (res.code !== 200) {
            container.innerHTML = `<p class="perm-empty">加载失败: ${res.message || '未知错误'}</p>`;
            return;
        }
        const groups = res.data || [];
        if (groups.length === 0) {
            container.innerHTML = '<p class="perm-empty">暂无权限配置，点击右上角「新增配置」添加</p>';
            return;
        }
        container.innerHTML = groups.map(renderPermissionGroup).join('');

        // 绑定操作按钮
        container.querySelectorAll('.perm-edit-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const item = JSON.parse(btn.dataset.item);
                showPermissionModal(item);
            });
        });
        container.querySelectorAll('.perm-delete-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = parseInt(btn.dataset.id, 10);
                if (!confirm('确定删除该权限配置？')) return;
                try {
                    const res = await adminPermissionAPI.remove(id);
                    if (res.code === 200) {
                        loadAdminPermissions();
                    } else {
                        alert(res.message || '删除失败');
                    }
                } catch (err) {
                    alert(err.message || '删除失败');
                }
            });
        });
    } catch (err) {
        container.innerHTML = `<p class="perm-empty">加载失败: ${err.message || '网络错误'}</p>`;
    }
}

function renderPermissionGroup(group) {
    const typeLabel = group.type === 'count_limit' ? '总量限制' : '订阅限制';
    const typeBadge = group.type === 'count_limit'
        ? '<span class="perm-type-badge perm-type-count">count_limit</span>'
        : '<span class="perm-type-badge perm-type-sub">subscribed</span>';

    const productRows = (group.products || []).map(p => {
        const detail = p.config_detail || {};
        const limitText = detail.limit != null ? `上限: ${detail.limit}` : '-';
        const itemData = JSON.stringify({
            id: p.id,
            key: group.key,
            type: group.type,
            product_key: p.product_key,
            config_detail: detail,
        }).replace(/"/g, '&quot;');

        return `<tr>
            <td>${escapeHtml(p.product_key)}</td>
            <td>${group.type === 'count_limit' ? limitText : '<span class="perm-text-muted">不适用</span>'}</td>
            <td class="perm-actions-cell">
                <button type="button" class="perm-edit-btn perm-action-btn" data-item="${itemData}" title="编辑">✏️</button>
                <button type="button" class="perm-delete-btn perm-action-btn perm-action-danger" data-id="${p.id}" title="删除">🗑️</button>
            </td>
        </tr>`;
    }).join('');

    return `<div class="perm-group-card">
        <div class="perm-group-header">
            <div class="perm-group-title">
                <span class="perm-key-label">${escapeHtml(group.key)}</span>
                ${typeBadge}
            </div>
            <span class="perm-type-desc">${typeLabel}</span>
        </div>
        <table class="perm-table">
            <thead><tr><th>产品 Key</th><th>配置详情</th><th>操作</th></tr></thead>
            <tbody>${productRows || '<tr><td colspan="3" class="perm-text-muted">暂无产品配置</td></tr>'}</tbody>
        </table>
    </div>`;
}

// ===== 资源管理（admin） =====

let currentEditResourceId = null;

function initResources() {
    const createBtn = document.getElementById('open-create-resource-btn');
    if (createBtn && createBtn.dataset.bound !== 'true') {
        createBtn.addEventListener('click', () => showResourceModal(null));
        createBtn.dataset.bound = 'true';
    }
    const closeBtn = document.getElementById('close-resource-modal');
    if (closeBtn && closeBtn.dataset.bound !== 'true') {
        closeBtn.addEventListener('click', hideResourceModal);
        closeBtn.dataset.bound = 'true';
    }
    const cancelBtn = document.getElementById('cancel-resource-btn');
    if (cancelBtn && cancelBtn.dataset.bound !== 'true') {
        cancelBtn.addEventListener('click', hideResourceModal);
        cancelBtn.dataset.bound = 'true';
    }
    const form = document.getElementById('resource-form');
    if (form && form.dataset.bound !== 'true') {
        form.addEventListener('submit', handleResourceFormSubmit);
        form.dataset.bound = 'true';
    }
    // 监听类型和来源选择变化，动态显示补充信息
    const typeSelect = document.getElementById('resource-type');
    const sourceSelect = document.getElementById('resource-source');
    if (typeSelect && typeSelect.dataset.bound !== 'true') {
        typeSelect.addEventListener('change', toggleResourceExtraFields);
        typeSelect.dataset.bound = 'true';
    }
    if (sourceSelect && sourceSelect.dataset.bound !== 'true') {
        sourceSelect.addEventListener('change', toggleResourceExtraFields);
        sourceSelect.dataset.bound = 'true';
    }
    const toggleSecretBtn = document.getElementById('toggle-ak-secret');
    if (toggleSecretBtn && toggleSecretBtn.dataset.bound !== 'true') {
        toggleSecretBtn.addEventListener('click', () => {
            const input = document.getElementById('resource-extra-ak-secret');
            const isPassword = input.type === 'password';
            input.type = isPassword ? 'text' : 'password';
            toggleSecretBtn.textContent = isPassword ? '🙈' : '👁️';
        });
        toggleSecretBtn.dataset.bound = 'true';
    }
    const toggleLoginPwdBtn = document.getElementById('toggle-login-password');
    if (toggleLoginPwdBtn && toggleLoginPwdBtn.dataset.bound !== 'true') {
        toggleLoginPwdBtn.addEventListener('click', () => {
            const input = document.getElementById('resource-extra-login-password');
            const isPassword = input.type === 'password';
            input.type = isPassword ? 'text' : 'password';
            toggleLoginPwdBtn.textContent = isPassword ? '🙈' : '👁️';
        });
        toggleLoginPwdBtn.dataset.bound = 'true';
    }
}

function toggleResourceExtraFields() {
    const type = document.getElementById('resource-type').value;
    const source = document.getElementById('resource-source').value;
    const mysqlFields = document.getElementById('resource-extra-fields');
    const githubFields = document.getElementById('resource-extra-github-fields');
    const cloudServerFields = document.getElementById('resource-extra-cloud-server-fields');
    const sourceSelect = document.getElementById('resource-source');

    // 根据资源类型动态过滤来源选项
    const sourceOptions = sourceSelect.querySelectorAll('option');
    sourceOptions.forEach(opt => {
        if (!opt.value) return; // 跳过占位选项
        if (type === 'mysql') {
            opt.style.display = opt.value === 'aliyun' ? '' : 'none';
        } else if (type === 'code_repo') {
            opt.style.display = opt.value === 'github' ? '' : 'none';
        } else if (type === 'cloud_server') {
            opt.style.display = opt.value === 'tencent_cloud' ? '' : 'none';
        } else {
            opt.style.display = '';
        }
    });

    mysqlFields.style.display = (type === 'mysql' && source === 'aliyun') ? '' : 'none';
    githubFields.style.display = (type === 'code_repo' && source === 'github') ? '' : 'none';
    cloudServerFields.style.display = (type === 'cloud_server' && source === 'tencent_cloud') ? '' : 'none';
}

function showResourceModal(item) {
    const modal = document.getElementById('resource-modal');
    const title = document.getElementById('resource-modal-title');
    const form = document.getElementById('resource-form');

    form.reset();
    document.getElementById('resource-extra-fields').style.display = 'none';
    document.getElementById('resource-extra-ak-secret').type = 'password';
    const toggleBtn = document.getElementById('toggle-ak-secret');
    if (toggleBtn) toggleBtn.textContent = '👁️';
    document.getElementById('resource-extra-github-fields').style.display = 'none';
    document.getElementById('resource-extra-cloud-server-fields').style.display = 'none';
    document.getElementById('resource-extra-login-password').type = 'password';
    const toggleLoginBtn = document.getElementById('toggle-login-password');
    if (toggleLoginBtn) toggleLoginBtn.textContent = '👁️';

    if (item) {
        currentEditResourceId = item.id;
        title.textContent = '编辑资源';
        document.getElementById('resource-name').value = item.name || '';
        document.getElementById('resource-type').value = item.type || '';
        document.getElementById('resource-source').value = item.source || '';
        // 设置环境复选框
        const envCheckboxes = document.querySelectorAll('input[name="resource-envs"]');
        const envs = item.envs || [];
        envCheckboxes.forEach(cb => {
            cb.checked = envs.includes(cb.value);
        });
        // 设置补充信息
        const extra = item.extra || {};
        document.getElementById('resource-extra-url').value = extra.url || '';
        document.getElementById('resource-extra-ak-id').value = extra.access_key_id || '';
        document.getElementById('resource-extra-ak-secret').value = extra.access_key_secret || '';
        document.getElementById('resource-extra-organization').value = extra.organization || '';
        document.getElementById('resource-extra-app-id').value = extra.app_id || '';
        document.getElementById('resource-extra-private-key').value = extra.private_key || '';
        document.getElementById('resource-extra-login-user').value = extra.login_user || '';
        document.getElementById('resource-extra-login-password').value = extra.login_password || '';
        document.getElementById('resource-extra-server-ip').value = extra.server_ip || '';
        toggleResourceExtraFields();
    } else {
        currentEditResourceId = null;
        title.textContent = '新增资源';
    }
    modal.style.display = 'flex';
}

function hideResourceModal() {
    document.getElementById('resource-modal').style.display = 'none';
    currentEditResourceId = null;
}

async function handleResourceFormSubmit(e) {
    e.preventDefault();
    const saveBtn = document.getElementById('save-resource-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';

    try {
        const name = document.getElementById('resource-name').value.trim();
        const type = document.getElementById('resource-type').value;
        const source = document.getElementById('resource-source').value;

        if (!name) { alert('请填写资源名称'); return; }
        if (!type || !source) { alert('请选择资源类型和来源'); return; }

        const envCheckboxes = document.querySelectorAll('input[name="resource-envs"]:checked');
        const envs = Array.from(envCheckboxes).map(cb => cb.value);
        if (envs.length === 0) { alert('请至少选择一个可用环境'); return; }

        const extra = {};
        if (type === 'mysql' && source === 'aliyun') {
            const url = document.getElementById('resource-extra-url').value.trim();
            const akId = document.getElementById('resource-extra-ak-id').value.trim();
            const akSecret = document.getElementById('resource-extra-ak-secret').value.trim();
            if (!url || !akId) { alert('请填写数据库实例地址和 AccessKey ID'); return; }
            extra.url = url;
            extra.access_key_id = akId;
            if (akSecret) {
                extra.access_key_secret = akSecret;
            } else if (!currentEditResourceId) {
                alert('请填写 AccessKey Secret');
                return;
            }
        } else if (type === 'code_repo' && source === 'github') {
            const organization = document.getElementById('resource-extra-organization').value.trim();
            const appId = document.getElementById('resource-extra-app-id').value.trim();
            const privateKey = document.getElementById('resource-extra-private-key').value.trim();
            if (!organization) { alert('请填写 GitHub Organization'); return; }
            if (!appId) { alert('请填写 GitHub App ID'); return; }
            extra.organization = organization;
            extra.app_id = appId;
            if (privateKey) {
                extra.private_key = privateKey;
            } else if (!currentEditResourceId) {
                alert('请填写 GitHub App Private Key（PEM 格式，非 Client Secret）');
                return;
            }
        } else if (type === 'cloud_server' && source === 'tencent_cloud') {
            const loginUser = document.getElementById('resource-extra-login-user').value.trim();
            const loginPassword = document.getElementById('resource-extra-login-password').value.trim();
            const serverIp = document.getElementById('resource-extra-server-ip').value.trim();
            if (!loginUser) { alert('请填写登录账号'); return; }
            if (!serverIp) { alert('请填写服务器IP'); return; }
            extra.login_user = loginUser;
            extra.server_ip = serverIp;
            if (loginPassword) {
                extra.login_password = loginPassword;
            } else if (!currentEditResourceId) {
                alert('请填写登录密码');
                return;
            }
        }

        const payload = { name, type, source, envs, extra };

        let res;
        if (currentEditResourceId) {
            // 编辑时如果没填敏感字段，不传该字段（保留原值）
            if (type === 'mysql' && source === 'aliyun' && !extra.access_key_secret) {
                delete payload.extra.access_key_secret;
            }
            if (type === 'code_repo' && source === 'github' && !extra.private_key) {
                delete payload.extra.private_key;
            }
            if (type === 'cloud_server' && source === 'tencent_cloud' && !extra.login_password) {
                delete payload.extra.login_password;
            }
            res = await adminResourceAPI.update(currentEditResourceId, payload);
        } else {
            res = await adminResourceAPI.create(payload);
        }

        if (res.code === 200 || res.code === 201) {
            hideResourceModal();
            loadAdminResources();
        } else {
            alert(res.message || '保存失败');
        }
    } catch (err) {
        alert(err.message || '请求失败');
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
    }
}

async function loadAdminResources() {
    initResources();
    const container = document.getElementById('resources-list');
    if (!container) return;
    container.innerHTML = '<p class="perm-loading">加载中…</p>';

    try {
        const res = await adminResourceAPI.list();
        if (res.code !== 200) {
            container.innerHTML = `<p class="perm-empty">加载失败: ${res.message || '未知错误'}</p>`;
            return;
        }
        const resources = res.data || [];
        if (resources.length === 0) {
            container.innerHTML = '<p class="perm-empty">暂无资源，点击右上角「新增资源」添加</p>';
            return;
        }
        container.innerHTML = `
            <table class="data-table resource-table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>名称</th>
                        <th>类型</th>
                        <th>来源</th>
                        <th>可用环境</th>
                        <th>实例信息</th>
                        <th>状态</th>
                        <th>创建时间</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>
                    ${resources.map(renderResourceRow).join('')}
                </tbody>
            </table>`;

        // 绑定操作按钮
        container.querySelectorAll('.resource-edit-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const item = JSON.parse(btn.dataset.item);
                showResourceModal(item);
            });
        });
        container.querySelectorAll('.resource-toggle-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = parseInt(btn.dataset.id, 10);
                const action = btn.dataset.action;
                try {
                    const res = action === 'offline'
                        ? await adminResourceAPI.offline(id)
                        : await adminResourceAPI.online(id);
                    if (res.code === 200) {
                        loadAdminResources();
                    } else {
                        alert(res.message || '操作失败');
                    }
                } catch (err) {
                    alert(err.message || '操作失败');
                }
            });
        });
    } catch (err) {
        container.innerHTML = `<p class="perm-empty">加载失败: ${err.message || '网络错误'}</p>`;
    }
}

function renderResourceRow(resource) {
    const typeLabels = { mysql: 'MySQL', code_repo: '代码仓库', cloud_server: '云服务器' };
    const sourceLabels = { aliyun: '阿里云', github: 'GitHub', tencent_cloud: '腾讯云' };
    const envLabels = { test: '测试', prod: '生产' };

    const envsHtml = (resource.envs || []).map(e =>
        `<span class="resource-env-tag">${envLabels[e] || e}</span>`
    ).join(' ');

    const extra = resource.extra || {};
    let instanceInfo = '-';
    if (resource.type === 'code_repo') {
        instanceInfo = extra.organization || '-';
    } else if (resource.type === 'cloud_server') {
        instanceInfo = extra.server_ip || '-';
    } else {
        instanceInfo = extra.url || '-';
    }

    const statusClass = resource.is_online ? 'resource-status-online' : 'resource-status-offline';
    const statusText = resource.is_online ? '上架' : '下架';
    const toggleAction = resource.is_online ? 'offline' : 'online';
    const toggleText = resource.is_online ? '下架' : '上架';

    const itemData = JSON.stringify({
        id: resource.id,
        name: resource.name,
        type: resource.type,
        source: resource.source,
        envs: resource.envs,
        extra: resource.extra,
    }).replace(/"/g, '&quot;');

    const createdAt = resource.created_at
        ? new Date(resource.created_at).toLocaleDateString('zh-CN')
        : '-';

    return `<tr>
        <td>${resource.id}</td>
        <td class="resource-url-cell" title="${escapeHtml(resource.name || '')}">${escapeHtml(resource.name || '-')}</td>
        <td>${typeLabels[resource.type] || resource.type}</td>
        <td>${sourceLabels[resource.source] || resource.source}</td>
        <td>${envsHtml}</td>
        <td class="resource-url-cell" title="${escapeHtml(instanceInfo)}">${escapeHtml(instanceInfo)}</td>
        <td><span class="resource-status-badge ${statusClass}">${statusText}</span></td>
        <td>${createdAt}</td>
        <td class="perm-actions-cell">
            <button type="button" class="resource-edit-btn perm-action-btn" data-item="${itemData}" title="编辑">✏️</button>
            <button type="button" class="resource-toggle-btn perm-action-btn" data-id="${resource.id}" data-action="${toggleAction}" title="${toggleText}">${resource.is_online ? '⏸️' : '▶️'}</button>
        </td>
    </tr>`;
}
