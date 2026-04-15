// clients.js - 客户端列表管理
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
            <button class="btn-action btn-deploy" onclick="openDeployDetails(${client.id})">发布详情</button>
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
    if (!confirm('确定要删除这个应用吗？')) return;

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
            if (appTypes.length === 0) { showToast('请选择至少一种应用形态', 'error'); return; }
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

// ===== 发布详情 =====
function openDeployDetails(clientId) {
    window.open(`deploy-details.html?client_id=${clientId}`, '_blank');
}

// 兼容旧代码的 showAddTaskModal 别名
function showAddTaskModal() {
    showTaskEditModal(null, true);
}
