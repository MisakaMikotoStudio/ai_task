/**
 * Task Chat 页面逻辑
 */

// ===== State =====
let taskId = null;
let taskInfo = null;
let currentChatId = null;
let chatsCache = [];
let messagesCache = [];
let messagesFingerprint = '';
const outputHtmlCache = new Map();
let runningMessageId = null;
let pollTimer = null;
let mergeRequestStore = {};
let clientConfigCache = null;

// ===== Init =====
document.addEventListener('DOMContentLoaded', async () => {
    await initAPIConfig();

    if (!isLoggedIn()) {
        window.location.href = 'index.html';
        return;
    }

    const params = new URLSearchParams(window.location.search);
    taskId = parseInt(params.get('task_id'));
    if (!taskId) {
        showToast('缺少 task_id 参数', 'error');
        return;
    }

    await loadTaskInfo();
    await loadClientConfig();
    await loadChats();
});

// ===== Helpers =====
function formatTime(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ' ' +
        d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

// ===== Task info =====
async function loadTaskInfo() {
    try {
        const res = await taskAPI.get(taskId);
        taskInfo = res.data;
        document.getElementById('sidebar-task-name').textContent = taskInfo.title || `Task ${taskId}`;
        document.getElementById('sidebar-task-id').textContent = `#${taskId}`;
        document.title = `Chat · ${taskInfo.title || '#' + taskId}`;

        // 客户端名称（后端 get_task 已通过 JOIN 返回）
        const clientName = taskInfo.client_name || null;
        if (clientName) {
            document.getElementById('sidebar-client-name').textContent = clientName;
            document.getElementById('sidebar-client-link').style.display = 'inline-flex';
        }

        renderSidebarExtra(taskInfo);
    } catch (e) {
        console.warn('loadTaskInfo failed:', e);
        document.getElementById('sidebar-task-name').textContent = `Task ${taskId}`;
        document.getElementById('sidebar-task-id').textContent = `#${taskId}`;
    }
}

// ===== Load client config (repos info) =====
async function loadClientConfig() {
    try {
        if (!taskInfo || !taskInfo.client_id) return;
        const res = await clientAPI.getConfig(taskInfo.client_id);
        clientConfigCache = res.data;
    } catch (e) {
        console.warn('loadClientConfig failed:', e);
        clientConfigCache = null;
    }
}

// ===== Sidebar sections (from task extra) =====

function renderSidebarExtra(info) {
    const developDoc = info.develop_doc || '';
    const mergeRequest = info.merge_request || [];

    // ── 开发文档 ──
    const docsEl = document.getElementById('sidebar-docs');
    if (!developDoc) {
        docsEl.innerHTML = '<div class="sidebar-doc-empty">暂无文档</div>';
    } else {
        docsEl.innerHTML = `
            <a class="sidebar-doc-link" href="${escapeHtml(developDoc)}" target="_blank" rel="noopener noreferrer">
                📄 开发文档
            </a>`;
    }

    // ── 变更详情 ──
    const gpEl = document.getElementById('sidebar-gitpush');
    const storeKey = 'task_sidebar';
    mergeRequestStore[storeKey] = mergeRequest;
    const summary = mergeRequest.length > 0
        ? `共 ${mergeRequest.length} 条变更记录，点击查看详情`
        : '暂无推送记录，点击查看详情';

    gpEl.innerHTML = `
        <button type="button" class="sidebar-action-btn" onclick="showMergeRequestModal('${storeKey}')">
            <span class="sidebar-action-icon">🔀</span>
            <span class="sidebar-action-text">
                <span class="sidebar-action-title">查看变更详情</span>
                <span class="sidebar-action-subtitle">${escapeHtml(summary)}</span>
            </span>
        </button>`;
}

// ===== Chat list =====
async function loadChats() {
    try {
        const res = await chatAPI.listChats(taskId);
        chatsCache = res.data || [];
        renderChatList();

        if (chatsCache.length === 0) {
            document.getElementById('welcome-view').style.display = 'flex';
            document.getElementById('active-view').style.display = 'none';
            return;
        }

        const hasCurrentChat = currentChatId && chatsCache.some(chat => chat.id === currentChatId);
        const targetChatId = hasCurrentChat ? currentChatId : chatsCache[0].id;
        await selectChat(targetChatId);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function renderChatList() {
    const container = document.getElementById('chat-list');

    if (chatsCache.length === 0) {
        container.innerHTML = '<div class="chat-sidebar-empty">暂无 Chat<br>点击「新建 Chat」开始</div>';
        return;
    }

    const statusLabel = { running: '执行中', completed: '已完成', terminated: '已终止' };

    container.innerHTML = chatsCache.map(chat => {
        const active = chat.id === currentChatId ? 'active' : '';
        const st = chat.status || 'completed';
        const preview = chat.title || `Chat #${chat.id}`;

        return `
        <div class="chat-item ${active}" onclick="selectChat(${chat.id})">
            <div class="chat-item-row1">
                <span class="chat-item-id">#${chat.id}</span>
                <span class="chat-status-dot ${st}"></span>
                <span class="chat-status-label ${st}">${statusLabel[st] || st}</span>
            </div>
            <div class="chat-item-row2">${escapeHtml(preview)}</div>
        </div>`;
    }).join('');
}

// ===== Select chat =====
async function selectChat(chatId) {
    currentChatId = chatId;
    stopPolling();

    document.getElementById('welcome-view').style.display = 'none';
    document.getElementById('active-view').style.display = 'flex';

    const chat = chatsCache.find(c => c.id === chatId);
    if (chat) updateTopbar(chat);

    renderChatList();
    await loadMessages(chatId);
}

function updateTopbar(chat) {
    document.getElementById('topbar-title').textContent = chat.title;
    const badge = document.getElementById('topbar-badge');
    const labels = { running: '执行中', completed: '执行完成', terminated: '已终止' };
    badge.textContent = labels[chat.status] || '';
    badge.className = `chat-topbar-badge ${chat.status || ''}`;
}

// ===== Messages =====
async function loadMessages(chatId) {
    try {
        const res = await chatAPI.listMessages(taskId, chatId);
        messagesCache = res.data || [];
        // 让轮询指纹与当前缓存对齐，避免首次轮询做重复渲染/请求
        messagesFingerprint = getMessagesFingerprint(messagesCache);
        renderFeed();
        updateComposerState();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function renderFeed() {
    const feed = document.getElementById('chat-feed');

    if (messagesCache.length === 0) {
        feed.innerHTML = `
            <div class="feed-welcome" style="height:100%">
                <div class="feed-welcome-icon">✨</div>
                <div class="feed-welcome-text">开始对话</div>
                <div class="feed-welcome-sub">在下方输入框输入您的问题</div>
            </div>`;
        return;
    }

    const statusChipMap = {
        pending: ['pending', '等待执行'],
        running: ['running', '执行中…'],
        completed: ['completed', '执行完成'],
        terminated: ['terminated', '已终止']
    };

    feed.innerHTML = messagesCache.map(msg => {
        const [chipClass, chipLabel] = statusChipMap[msg.status] || ['', msg.status];
        const extra = parseMsgExtra(msg.extra);

        // User row
        const userRow = `
        <div class="msg-user-row">
            <div class="msg-avatar user-avatar">你</div>
            <div class="msg-body">
                <div class="msg-header">
                    <span class="msg-role">You</span>
                    <span class="msg-time">${formatTime(msg.created_at)}</span>
                </div>
                <div class="msg-text">${escapeHtml(msg.input)}</div>
            </div>
        </div>`;

        // Agent row
        let outputHtml;
        if (msg.output) {
            outputHtml = `<div class="msg-output">${renderOutputCached(msg)}</div>`;
        } else if (msg.status === 'pending' || msg.status === 'running') {
            outputHtml = `
                <div class="typing-indicator">
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                </div>`;
        } else {
            outputHtml = `<div class="msg-output" style="color:var(--text-muted);font-style:italic">无输出</div>`;
        }

        // Extra buttons: develop_doc + merge_request
        let extraBtns = '';
        if (extra.develop_doc) {
            extraBtns += `<a class="msg-extra-btn doc-btn" href="${escapeHtml(extra.develop_doc)}" target="_blank" rel="noopener noreferrer">📄 开发文档</a>`;
        }
        const showMrBtn = extra && extra.merge_request !== undefined;
        if (showMrBtn) {
            const storeKey = `msg_${msg.id}`;
            const mrData = Array.isArray(extra.merge_request) ? extra.merge_request : [];
            mergeRequestStore[storeKey] = mrData;
            extraBtns += `<button class="msg-extra-btn mr-btn" onclick="showMergeRequestModal('${storeKey}')">🔀 变更详情</button>`;
        }

        const agentRow = `
        <div class="msg-agent-row">
            <div class="msg-avatar agent-avatar">⚡</div>
            <div class="msg-body">
                <div class="msg-header">
                    <span class="msg-role">Agent</span>
                    <span class="msg-time">${formatTime(msg.updated_at)}</span>
                </div>
                ${outputHtml}
                <div class="msg-status-row">
                    <span class="msg-status-chip ${chipClass}">${chipLabel}</span>
                    ${extraBtns}
                </div>
            </div>
        </div>`;

        return `<div class="msg-turn">${userRow}${agentRow}</div>`;
    }).join('');

    scrollToBottom();
}

// Configure marked with highlight.js
(function initMarked() {
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

function renderOutput(output) {
    if (!output) return '';
    if (typeof marked !== 'undefined') {
        try { return marked.parse(output); } catch (_) {}
    }
    return `<p>${escapeHtml(output).replace(/\n/g, '<br>')}</p>`;
}

function getOutputCacheKey(msg) {
    // 用 updated_at + output 长度做区分，避免相同内容在轮询渲染时重复 marked.parse
    const updated = msg.updated_at || msg.created_at || '';
    const outputLen = msg.output ? String(msg.output).length : 0;
    return `${msg.id}|${msg.status}|${updated}|${outputLen}`;
}

function renderOutputCached(msg) {
    if (!msg || !msg.output) return '';
    const key = getOutputCacheKey(msg);
    const cached = outputHtmlCache.get(key);
    if (cached) return cached;

    const html = renderOutput(msg.output);
    outputHtmlCache.set(key, html);

    // 防止缓存无限增长
    if (outputHtmlCache.size > 1000) outputHtmlCache.clear();
    return html;
}

function scrollToBottom() {
    const feed = document.getElementById('chat-feed');
    setTimeout(() => { feed.scrollTop = feed.scrollHeight; }, 50);
}

function parseMsgExtra(extra) {
    if (!extra) return {};
    if (typeof extra === 'string') {
        try { return JSON.parse(extra); } catch { return {}; }
    }
    return extra;
}

function showMergeRequestModal(storeKey) {
    const body = document.getElementById('merge-request-body');
    const data = mergeRequestStore[storeKey];
    const list = Array.isArray(data) ? data : [];

    if (list.length === 0) {
        body.innerHTML = '<div class="mr-empty-text">暂无变更记录</div>';
        document.getElementById('merge-request-modal').classList.add('show');
        return;
    }

    body.innerHTML = `
        <table class="mr-table">
            <thead>
                <tr>
                    <th>项目</th>
                    <th>分支</th>
                    <th>提交</th>
                    <th>PR</th>
                </tr>
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
        </table>
    `;

    document.getElementById('merge-request-modal').classList.add('show');
}

function closeMergeRequestModal() {
    document.getElementById('merge-request-modal').classList.remove('show');
}

document.getElementById('merge-request-modal').addEventListener('click', function (e) {
    if (e.target === this) closeMergeRequestModal();
});

// ===== Composer state =====
function updateComposerState() {
    const running = messagesCache.find(m => m.status === 'pending' || m.status === 'running');
    runningMessageId = running ? running.id : null;

    const box = document.getElementById('composer-box');
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const stopBtn = document.getElementById('stop-btn');
    const hintEl = document.getElementById('composer-hint');
    const hintText = document.getElementById('composer-hint-text');

    const mergeWrap = document.getElementById('merge-dropdown-wrap');

    if (runningMessageId) {
        box.classList.add('locked');
        input.disabled = true;
        sendBtn.style.display = 'none';
        if (mergeWrap) mergeWrap.style.display = 'none';
        stopBtn.style.display = 'flex';
        hintEl.className = 'composer-hint warn';
        hintText.textContent = '当前有 Chat 消息正在执行，无法输入新消息';
        startPolling();
    } else {
        box.classList.remove('locked');
        input.disabled = false;
        sendBtn.style.display = 'flex';
        if (mergeWrap) mergeWrap.style.display = clientConfigCache ? 'block' : 'none';
        stopBtn.style.display = 'none';
        hintEl.className = 'composer-hint';
        hintText.textContent = '尽管问，带图也行';
        stopPolling();
    }

    // Update topbar
    const chat = chatsCache.find(c => c.id === currentChatId);
    if (chat) updateTopbar(chat);
}

// ===== Polling =====
function getMessagesFingerprint(list) {
    // 用 id/status/更新时间做轻量指纹，避免 JSON.stringify 带来的大开销
    return (list || []).map(m => {
        const updated = m.updated_at || m.created_at || '';
        return `${m.id}:${m.status}:${updated}`;
    }).join('|');
}

function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
        if (!currentChatId) return;
        try {
            const res = await chatAPI.listMessages(taskId, currentChatId);
            const fresh = res.data || [];
            const nextFingerprint = getMessagesFingerprint(fresh);
            if (nextFingerprint !== messagesFingerprint) {
                const prevRunningId = runningMessageId;
                messagesFingerprint = nextFingerprint;
                messagesCache = fresh;
                renderFeed();
                updateComposerState();
                // 仅当运行状态开关变化时，才刷新 Chat 列表（减少无意义请求）
                if (prevRunningId !== runningMessageId) {
                    await loadChats();
                }
            }
        } catch { /* silent */ }
    }, 3000);
}

function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ===== Send =====
async function sendMessage() {
    if (!currentChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;

    const btn = document.getElementById('send-btn');
    btn.disabled = true;

    try {
        await chatAPI.createMessage(taskId, currentChatId, text);
        input.value = '';
        autoResize(input);
        await loadMessages(currentChatId);
        await loadChats();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

function handleInputKeydown(e) {
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); sendMessage(); }
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}

// ===== Terminate =====
async function terminateMessage() {
    if (!runningMessageId || !currentChatId) return;
    const btn = document.getElementById('stop-btn');
    btn.disabled = true;
    btn.innerHTML = '终止中…';
    try {
        const res = await chatAPI.deleteMessage(taskId, currentChatId, runningMessageId);
        const inputText = res?.data?.input || '';
        showToast('已撤销，内容已回填', 'success');
        await loadMessages(currentChatId);
        await loadChats();
        // 回填 input 到输入框并聚焦
        if (inputText) {
            const inputEl = document.getElementById('chat-input');
            inputEl.value = inputText;
            autoResize(inputEl);
            inputEl.focus();
            // 光标移到末尾
            inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
        }
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '⬛ 终止';
    }
}

// ===== New Chat (show welcome view) =====
function showNewChatModal() {
    stopPolling();
    currentChatId = null;
    document.getElementById('welcome-view').style.display = 'flex';
    document.getElementById('active-view').style.display = 'none';
    renderChatList();
    setTimeout(() => {
        const input = document.getElementById('welcome-input');
        if (input) input.focus();
    }, 80);
}

// ===== Send from welcome view (auto-create chat + message) =====
async function sendNewChatMessage() {
    const input = document.getElementById('welcome-input');
    const text = input.value.trim();
    if (!text) return;

    const btn = document.getElementById('welcome-send-btn');
    btn.disabled = true;

    try {
        const res = await chatAPI.createChatWithMessage(taskId, text);
        input.value = '';
        autoResize(input);
        const chatId = res.data.chat.id;
        await loadChats();
        await selectChat(chatId);
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

function handleWelcomeInputKeydown(e) {
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); sendNewChatMessage(); }
}

// ===== Merge dropdown =====
function toggleMergeDropdown(e) {
    e.stopPropagation();
    const menu = document.getElementById('merge-dropdown-menu');
    menu.classList.toggle('show');
}

function closeMergeDropdown() {
    const menu = document.getElementById('merge-dropdown-menu');
    if (menu) menu.classList.remove('show');
}

document.addEventListener('click', () => { closeMergeDropdown(); });

function _getRepoName(url) {
    if (!url) return '';
    const m = url.match(/[/:]([\w.-]+?)(?:\.git)?$/);
    return m ? m[1] : url;
}

function _buildRepoTable(repos) {
    const lines = ['| 仓库 | 分支前缀 | 默认分支 | task 分支 | chat 分支 |', '|------|---------|---------|----------|----------|'];
    for (const repo of repos) {
        const name = _getRepoName(repo.url);
        const prefix = repo.branch_prefix || 'ai_';
        const defaultBr = repo.default_branch || 'main';
        const taskBr = `${prefix}${taskId}`;
        const chatBr = `${prefix}${taskId}_${currentChatId}`;
        lines.push(`| ${name} | ${prefix} | ${defaultBr} | ${taskBr} | ${chatBr} |`);
    }
    return lines.join('\n');
}

function _buildMergeToTaskPrompt() {
    if (!clientConfigCache || !clientConfigCache.repos) return null;
    const repos = clientConfigCache.repos;
    const repoTable = _buildRepoTable(repos);

    return `# 合并 Chat 分支到 Task 分支

## 背景信息

- task_id: ${taskId}
- chat_id: ${currentChatId}
- 当前工作目录下有多个独立 git 仓库

${repoTable}

## 操作要求

对当前工作目录下的 **每一个 git 仓库** 执行以下操作：

1. **整理差异**：对比 chat 分支与 task 分支的差异
2. **Squash 合并**：将 chat 分支相对于 task 分支的所有差异合并成 **一个 commit**，设置有意义的 commit message（概括本次 chat 的改动内容）
3. **合并方式**：要求在 task 分支的 commit 历史上新增一个 commit，而不是产生 merge commit 记录。推荐使用 \`git checkout <task分支> && git merge --squash <chat分支> && git commit\` 的方式
4. **推送到远端**：合并完成后推送 task 分支到远端。如果远端有新的提交，先执行 \`git pull --rebase origin <task分支>\` 再 push
5. **关闭 PR**：如果 chat 分支在远端有对应的 PR（目标分支为 task 分支），使用 git 命令或 API 关闭该 PR，避免云端残留大量 PR。可以通过删除远端 chat 分支来自动关闭 PR：\`git push origin --delete <chat分支>\`
6. **操作完成后**：切回 chat 分支继续工作

## 注意事项

- 如果 chat 分支与 task 分支没有差异，跳过该仓库
- 每个仓库独立操作，一个失败不影响其他仓库
- 操作过程中如遇到冲突，尝试解决；无法解决时报告错误
`;
}

function _buildMergeToDefaultBranchPrompt() {
    if (!clientConfigCache || !clientConfigCache.repos) return null;
    const repos = clientConfigCache.repos;
    const repoTable = _buildRepoTable(repos);

    return `# 合并 Chat 分支到 Task 分支，再合并 Task 分支到默认分支

## 背景信息

- task_id: ${taskId}
- chat_id: ${currentChatId}
- 当前工作目录下有多个独立 git 仓库

${repoTable}

## 第一步：合并 Chat 分支到 Task 分支

对当前工作目录下的 **每一个 git 仓库** 执行以下操作：

1. **整理差异**：对比 chat 分支与 task 分支的差异
2. **Squash 合并**：将 chat 分支相对于 task 分支的所有差异合并成 **一个 commit**，设置有意义的 commit message
3. **合并方式**：在 task 分支的 commit 历史上新增一个 commit，而不是产生 merge commit。推荐使用 \`git checkout <task分支> && git merge --squash <chat分支> && git commit\` 的方式
4. **推送到远端**：合并完成后推送 task 分支到远端。如果远端有新的提交，先执行 \`git pull --rebase origin <task分支>\` 再 push
5. **关闭 PR**：如果 chat 分支在远端有对应的 PR，通过删除远端 chat 分支来关闭：\`git push origin --delete <chat分支>\`

## 第二步：合并 Task 分支到默认分支

在第一步全部完成后，对 **每一个 git 仓库** 继续执行：

1. **切换到默认分支**：\`git checkout <默认分支>\`
2. **拉取最新**：\`git pull origin <默认分支>\`
3. **Rebase 合并**：将 task 分支 rebase 到默认分支上，确保 commit 历史是线性的，不产生 merge commit。推荐方式：
   - \`git checkout <默认分支>\`
   - \`git merge --ff-only <task分支>\`（如果 task 分支已经 rebase 过默认分支）
   - 或者 \`git rebase <默认分支> <task分支> && git checkout <默认分支> && git merge --ff-only <task分支>\`
4. **推送默认分支**：\`git push origin <默认分支>\`
5. **关闭 PR**：如果 task 分支在远端有对应的 PR（目标分支为默认分支），通过删除远端 task 分支来关闭：\`git push origin --delete <task分支>\`
6. **操作完成后**：切回 chat 分支继续工作

## 注意事项

- 如果分支之间没有差异，跳过对应步骤
- 每个仓库独立操作，一个失败不影响其他仓库
- 操作过程中如遇到冲突，尝试解决；无法解决时报告错误
- 确保默认分支的 commit 历史是清爽的线性记录
`;
}

async function mergeToTask() {
    closeMergeDropdown();
    if (!currentChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const prompt = _buildMergeToTaskPrompt();
    if (!prompt) { showToast('未获取到仓库配置信息', 'error'); return; }

    const btn = document.getElementById('merge-trigger-btn');
    btn.disabled = true;
    try {
        await chatAPI.createMessage(taskId, currentChatId, prompt);
        await loadMessages(currentChatId);
        await loadChats();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

async function mergeToDefaultBranch() {
    closeMergeDropdown();
    if (!currentChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const prompt = _buildMergeToDefaultBranchPrompt();
    if (!prompt) { showToast('未获取到仓库配置信息', 'error'); return; }

    const btn = document.getElementById('merge-trigger-btn');
    btn.disabled = true;
    try {
        await chatAPI.createMessage(taskId, currentChatId, prompt);
        await loadMessages(currentChatId);
        await loadChats();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}
