// tasks.js - 任务管理、待办事项、秘钥管理
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

// 简单的 Markdown 解析
function parseSimpleMarkdown(text) {
    if (!text) return '';
    
    let html = escapeHtml(text);
    
    // 链接: [text](url) - 仅允许 http/https/mailto 协议，防止 javascript: XSS
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(match, text, url) {
        const decoded = url.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
        if (/^(https?:|mailto:)/i.test(decoded.trim())) {
            return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + text + '</a>';
        }
        return text;
    });
    
    // 加粗: **text**
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    
    // 行内代码: `code`
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    
    // 换行
    html = html.replace(/\n/g, '<br>');
    
    return html;
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

