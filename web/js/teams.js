// teams.js - 团队 Tab 前端逻辑
//
// 位于"应用"视图下的子 Tab：团队。
// 负责：团队搜索、选中团队、成员列表渲染、管理员添加/删除成员。

// 模块级状态
const teamState = {
    initialized: false,
    bound: false,
    currentTeam: null,       // { id, name, role, role_text, ... }
    currentMembers: [],      // [{ user_id, name, role, role_text }]
    myRole: null,            // 'admin' / 'member'
    lookupUser: null,        // 最近一次 uid 查询命中的用户 { user_id, name }
};

// ===== 子 Tab 切换 =====

function initAppSubTabs() {
    const tabs = document.querySelectorAll('.app-subtab');
    if (!tabs.length) return;

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.subtab;
            switchAppSubTab(target);
        });
    });
}

function switchAppSubTab(target) {
    document.querySelectorAll('.app-subtab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.subtab === target);
    });
    document.querySelectorAll('.app-subtab-panel').forEach(panel => {
        panel.classList.remove('active');
    });
    const panelId = target === 'teams' ? 'teams-subtab-panel' : 'clients-subtab-panel';
    document.getElementById(panelId)?.classList.add('active');

    if (target === 'teams') {
        initTeamsTab();
    }
}

// ===== 团队 Tab 初始化 =====

function initTeamsTab() {
    if (teamState.bound) return;
    teamState.bound = true;

    const searchBtn = document.getElementById('team-search-btn');
    const searchInput = document.getElementById('team-search-input');
    const clearBtn = document.getElementById('team-search-clear-btn');
    const addTeamBtn = document.getElementById('add-team-btn');
    const addLookupBtn = document.getElementById('team-add-lookup-btn');
    const addConfirmBtn = document.getElementById('team-add-confirm-btn');
    const addUidInput = document.getElementById('team-add-uid-input');

    if (searchBtn) {
        searchBtn.addEventListener('click', () => {
            searchTeams(searchInput?.value || '');
        });
    }
    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                searchBtn?.click();
            }
        });
    }
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            if (searchInput) searchInput.value = '';
            renderTeamList([]);
            const empty = document.getElementById('team-results-empty');
            if (empty) {
                empty.style.display = '';
                empty.textContent = '点击搜索按钮开始查找你的团队';
            }
        });
    }

    if (addTeamBtn) {
        addTeamBtn.addEventListener('click', () => {
            showCreateTeamModal();
        });
    }

    if (addLookupBtn) {
        addLookupBtn.addEventListener('click', () => {
            lookupMemberCandidate();
        });
    }
    if (addUidInput) {
        addUidInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                addLookupBtn?.click();
            }
        });
        addUidInput.addEventListener('input', () => {
            teamState.lookupUser = null;
            const hint = document.getElementById('team-add-hint');
            if (hint) hint.textContent = '';
        });
    }
    if (addConfirmBtn) {
        addConfirmBtn.addEventListener('click', () => {
            confirmAddMember();
        });
    }

    // 初始加载我的团队（不带 keyword，返回最近 10 条）
    searchTeams('');
    teamState.initialized = true;
}

// ===== 搜索团队 =====

async function searchTeams(keyword) {
    try {
        const resp = await teamAPI.search((keyword || '').trim());
        const list = (resp && resp.data) || [];
        renderTeamList(list);
        const empty = document.getElementById('team-results-empty');
        if (empty) {
            if (list.length === 0) {
                empty.style.display = '';
                empty.textContent = keyword ? '未找到匹配的团队' : '你还没有加入任何团队，点击右上角"新建团队"开始创建';
            } else {
                empty.style.display = 'none';
            }
        }
    } catch (error) {
        showToast(error.message || '搜索团队失败', 'error');
    }
}

function renderTeamList(teams) {
    const list = document.getElementById('team-results-list');
    if (!list) return;
    list.innerHTML = teams.map(team => renderTeamListItem(team)).join('');

    list.querySelectorAll('.team-result-item').forEach(item => {
        item.addEventListener('click', () => {
            const id = Number(item.dataset.teamId);
            const team = teams.find(t => t.id === id);
            if (team) selectTeam(team);
        });
    });

    if (teamState.currentTeam) {
        const active = list.querySelector(`.team-result-item[data-team-id="${teamState.currentTeam.id}"]`);
        active?.classList.add('active');
    }
}

function renderTeamListItem(team) {
    const roleText = team.role_text || (team.role === 'admin' ? '管理员' : '普通成员');
    const roleClass = team.role === 'admin' ? 'role-admin' : 'role-member';
    return `
        <li class="team-result-item" data-team-id="${team.id}">
            <div class="team-result-main">
                <span class="team-result-name">${escapeHtml(team.name)}</span>
                <span class="team-result-id">#${team.id}</span>
            </div>
            <span class="team-role-badge ${roleClass}">${escapeHtml(roleText)}</span>
        </li>
    `;
}

// ===== 选中团队 =====

async function selectTeam(team) {
    teamState.currentTeam = team;

    document.querySelectorAll('.team-result-item').forEach(item => {
        item.classList.toggle('active', Number(item.dataset.teamId) === team.id);
    });

    const empty = document.getElementById('team-detail-empty');
    const body = document.getElementById('team-detail-body');
    if (empty) empty.style.display = 'none';
    if (body) body.style.display = '';

    const nameEl = document.getElementById('team-detail-name');
    const idEl = document.getElementById('team-detail-id');
    const roleEl = document.getElementById('team-detail-role');
    if (nameEl) nameEl.textContent = team.name;
    if (idEl) idEl.textContent = `#${team.id}`;
    if (roleEl) {
        const roleText = team.role_text || (team.role === 'admin' ? '管理员' : '普通成员');
        roleEl.textContent = roleText;
        roleEl.className = 'team-detail-role ' + (team.role === 'admin' ? 'role-admin' : 'role-member');
    }

    await loadTeamMembers();
}

async function loadTeamMembers() {
    if (!teamState.currentTeam) return;
    try {
        const resp = await teamAPI.listMembers(teamState.currentTeam.id);
        const data = (resp && resp.data) || {};
        teamState.currentMembers = data.members || [];
        teamState.myRole = data.my_role || 'member';
        toggleAdminPanel(teamState.myRole === 'admin');
        renderMembers();
    } catch (error) {
        showToast(error.message || '加载成员失败', 'error');
    }
}

function toggleAdminPanel(isAdmin) {
    const addPanel = document.getElementById('team-add-member');
    if (addPanel) addPanel.style.display = isAdmin ? '' : 'none';
}

function renderMembers() {
    const tbody = document.getElementById('team-members-table-body');
    const emptyState = document.getElementById('team-members-empty');
    if (!tbody) return;

    const members = teamState.currentMembers || [];
    if (members.length === 0) {
        tbody.innerHTML = '';
        emptyState?.classList.add('show');
        return;
    }
    emptyState?.classList.remove('show');

    tbody.innerHTML = members.map(m => renderMemberRow(m)).join('');
}

function renderMemberRow(member) {
    const isAdminRole = member.role === 'admin';
    const roleClass = isAdminRole ? 'role-admin' : 'role-member';
    const roleText = member.role_text || (isAdminRole ? '管理员' : '普通成员');

    let actionHtml = '<span class="text-muted">-</span>';
    if (teamState.myRole === 'admin') {
        actionHtml = `<button class="btn-action btn-delete" onclick="confirmDeleteMember(${member.user_id})">删除</button>`;
    }

    return `
        <tr>
            <td data-label="用户ID">${member.user_id}</td>
            <td data-label="名称">${escapeHtml(member.name || '-')}</td>
            <td data-label="角色"><span class="team-role-badge ${roleClass}">${escapeHtml(roleText)}</span></td>
            <td data-label="操作">${actionHtml}</td>
        </tr>
    `;
}

// ===== 新建团队 =====

function showCreateTeamModal() {
    const content = `
        <div class="form-group">
            <label class="form-label">团队名称</label>
            <input type="text" id="create-team-name-input" class="form-input" placeholder="请输入团队名称（最多32个字符）" maxlength="32">
        </div>
        <div class="modal-actions" style="margin-top: 20px; display: flex; justify-content: flex-end; gap: 8px;">
            <button class="btn-secondary" id="create-team-cancel">取消</button>
            <button class="btn-primary" id="create-team-confirm">创建</button>
        </div>
    `;
    openModal('新建团队', content);

    const input = document.getElementById('create-team-name-input');
    setTimeout(() => input?.focus(), 0);

    const confirmBtn = document.getElementById('create-team-confirm');
    const cancelBtn = document.getElementById('create-team-cancel');
    cancelBtn?.addEventListener('click', () => closeModal());
    input?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            confirmBtn?.click();
        }
    });

    confirmBtn?.addEventListener('click', async () => {
        const name = (input?.value || '').trim();
        if (!name) {
            showToast('团队名称不能为空', 'error');
            return;
        }
        if (name.length > 32) {
            showToast('团队名称最多 32 个字符', 'error');
            return;
        }
        confirmBtn.disabled = true;
        confirmBtn.textContent = '创建中...';
        try {
            const resp = await teamAPI.create(name);
            closeModal();
            showToast('团队创建成功', 'success');
            // 创建后自动选中
            const team = resp && resp.data;
            if (team) {
                const searchInput = document.getElementById('team-search-input');
                if (searchInput) searchInput.value = '';
                await searchTeams('');
                await selectTeam(team);
            } else {
                await searchTeams('');
            }
        } catch (error) {
            showToast(error.message || '创建团队失败', 'error');
            confirmBtn.disabled = false;
            confirmBtn.textContent = '创建';
        }
    });
}

// ===== 添加成员 =====

async function lookupMemberCandidate() {
    const input = document.getElementById('team-add-uid-input');
    const hint = document.getElementById('team-add-hint');
    const raw = (input?.value || '').trim();
    if (!raw) {
        showToast('请输入用户 uid', 'error');
        return;
    }
    const uid = parseInt(raw, 10);
    if (!Number.isInteger(uid) || uid <= 0 || String(uid) !== raw) {
        showToast('请输入有效的用户 uid', 'error');
        return;
    }
    try {
        const resp = await teamAPI.searchUser(uid);
        const user = resp && resp.data;
        teamState.lookupUser = user || null;
        if (user && hint) {
            hint.textContent = `已找到用户：${user.name}（uid: ${user.user_id}）`;
            hint.className = 'team-add-hint success';
        }
    } catch (error) {
        teamState.lookupUser = null;
        if (hint) {
            hint.textContent = error.message || '用户不存在';
            hint.className = 'team-add-hint error';
        }
    }
}

async function confirmAddMember() {
    if (!teamState.currentTeam) return;
    const input = document.getElementById('team-add-uid-input');
    const raw = (input?.value || '').trim();
    if (!raw) {
        showToast('请输入用户 uid', 'error');
        return;
    }
    const uid = parseInt(raw, 10);
    if (!Number.isInteger(uid) || uid <= 0 || String(uid) !== raw) {
        showToast('请输入有效的用户 uid', 'error');
        return;
    }
    try {
        await teamAPI.addMember(teamState.currentTeam.id, uid);
        showToast('成员添加成功', 'success');
        if (input) input.value = '';
        teamState.lookupUser = null;
        const hint = document.getElementById('team-add-hint');
        if (hint) hint.textContent = '';
        await loadTeamMembers();
    } catch (error) {
        showToast(error.message || '添加成员失败', 'error');
    }
}

// ===== 删除成员 =====

async function confirmDeleteMember(userId) {
    if (!teamState.currentTeam) return;
    const member = (teamState.currentMembers || []).find(m => m.user_id === userId);
    const displayName = member ? (member.name || userId) : userId;
    if (!confirm(`确定要将 ${displayName} 移出团队吗？`)) return;

    try {
        await teamAPI.deleteMember(teamState.currentTeam.id, userId);
        showToast('成员已移除', 'success');
        await loadTeamMembers();
    } catch (error) {
        showToast(error.message || '移除成员失败', 'error');
    }
}
