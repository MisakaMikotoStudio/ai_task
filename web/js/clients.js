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

// 添加应用：直接打开配置向导
function showAddClientModal() {
    openClientConfig(null, 'add');
}

// 兼容旧代码的 showAddTaskModal 别名
function showAddTaskModal() {
    showTaskEditModal(null, true);
}
