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
let isStandaloneMode = false; // task_id=0 模式
let standaloneClientId = null; // 独立 chat 的 client_id

// ===== Image attachment state =====
// 已上传图片列表（active view），每项 { oss_path, filename }
let pendingImages = [];
// welcome view 的已上传图片列表
let welcomePendingImages = [];

// ===== Init =====
document.addEventListener('DOMContentLoaded', async () => {
    await initAPIConfig();

    if (!isLoggedIn()) {
        window.location.href = 'index.html';
        return;
    }

    const params = new URLSearchParams(window.location.search);
    taskId = parseInt(params.get('task_id'));
    if (isNaN(taskId) && !params.has('task_id')) {
        showToast('缺少 task_id 参数', 'error');
        return;
    }

    isStandaloneMode = (taskId === 0);

    // Embed mode: hide sidebar when loaded inside an iframe
    const isEmbed = params.get('embed') === '1';
    if (isEmbed) {
        document.querySelector('.chat-page')?.classList.add('embed-mode');
        // 有 chat_id 时直接显示 active-view，避免 welcome 页面闪烁
        if (params.get('chat_id')) {
            document.getElementById('welcome-view').style.display = 'none';
            document.getElementById('active-view').style.display = 'flex';
        }
    }

    if (isStandaloneMode) {
        // 独立 Chat 模式
        standaloneClientId = parseInt(params.get('client_id')) || null;
        const initialChatId = parseInt(params.get('chat_id')) || null;
        await loadStandaloneInfo();
        await loadClientConfig();
        await loadChats();
        if (initialChatId) {
            await selectChat(initialChatId);
        }
    } else {
        if (!taskId) {
            showToast('缺少 task_id 参数', 'error');
            return;
        }
        await loadTaskInfo();
        await loadClientConfig();
        await loadChats();
    }
});

// ===== Helpers（共享函数来自 chat-helpers.js）=====
function formatTime(dateStr) { return chatFormatTime(dateStr); }

// ===== Standalone info =====
async function loadStandaloneInfo() {
    document.getElementById('sidebar-task-name').textContent = '独立 Chat';
    document.getElementById('sidebar-task-id').textContent = '#-';
    document.title = 'Chat · 独立模式';

    if (standaloneClientId) {
        try {
            const res = await clientAPI.getConfig(standaloneClientId);
            const clientName = res.data?.name || '';
            if (clientName) {
                document.getElementById('sidebar-client-name').textContent = clientName;
                document.getElementById('sidebar-client-link').style.display = 'inline-flex';
            }
        } catch (e) {
            console.warn('loadStandaloneInfo client failed:', e);
        }
    }

    // 隐藏文档和变更详情区域（独立 Chat 无 task extra）
    const docsEl = document.getElementById('sidebar-docs');
    docsEl.innerHTML = '<div class="sidebar-doc-empty">暂无文档</div>';
    const gpEl = document.getElementById('sidebar-gitpush');
    gpEl.innerHTML = '<div class="gitpush-empty">暂无推送记录</div>';
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
        let configClientId = null;
        if (isStandaloneMode) {
            configClientId = standaloneClientId;
        } else {
            configClientId = taskInfo?.client_id;
        }
        if (!configClientId) return;
        const res = await clientAPI.getConfig(configClientId);
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
        let res;
        if (isStandaloneMode) {
            res = await chatAPI.listStandaloneChats();
        } else {
            res = await chatAPI.listChats(taskId);
        }
        const raw = res.data || [];
        chatsCache = Array.isArray(raw) ? raw : (raw.items || []);
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
        const clientInfo = (isStandaloneMode && chat.client_name)
            ? `<span class="chat-status-label" style="margin-left:auto;">${escapeHtml(chat.client_name)}</span>`
            : '';

        return `
        <div class="chat-item ${active}" onclick="selectChat(${chat.id})">
            <div class="chat-item-row1">
                <span class="chat-item-id">#${chat.id}</span>
                <span class="chat-status-dot ${st}"></span>
                <span class="chat-status-label ${st}">${statusLabel[st] || st}</span>
                ${clientInfo}
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

        // User row (with optional image list)
        const msgImages = extra.images || [];
        const imageListHtml = renderMsgImages(msgImages);
        const userRow = `
        <div class="msg-user-row">
            <div class="msg-avatar user-avatar">你</div>
            <div class="msg-body">
                <div class="msg-header">
                    <span class="msg-role">You</span>
                    <span class="msg-time">${formatTime(msg.created_at)}</span>
                </div>
                <div class="msg-text">${escapeHtml(msg.input)}</div>
                ${imageListHtml}
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

function renderOutput(output) { return chatRenderOutput(output); }
function getOutputCacheKey(msg) { return chatGetOutputCacheKey(msg); }
function renderOutputCached(msg) { return chatRenderOutputCached(msg, outputHtmlCache); }

function scrollToBottom() {
    const feed = document.getElementById('chat-feed');
    setTimeout(() => { feed.scrollTop = feed.scrollHeight; }, 50);
}

function parseMsgExtra(extra) { return chatParseMsgExtra(extra); }

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

    const mergeTaskBtn = document.getElementById('merge-to-task-btn');
    const mergeDefaultBtn = document.getElementById('merge-to-default-btn');
    const showMerge = !!clientConfigCache;

    if (runningMessageId) {
        box.classList.add('locked');
        input.disabled = true;
        sendBtn.style.display = 'none';
        if (mergeTaskBtn) mergeTaskBtn.style.display = 'none';
        if (mergeDefaultBtn) mergeDefaultBtn.style.display = 'none';
        stopBtn.style.display = 'flex';
        hintEl.className = 'composer-hint warn';
        hintText.textContent = '当前有 Chat 消息正在执行，无法输入新消息';
        startPolling();
    } else {
        box.classList.remove('locked');
        input.disabled = false;
        sendBtn.style.display = 'flex';
        // 独立 Chat 模式：隐藏"合并到 Task"按钮
        if (mergeTaskBtn) mergeTaskBtn.style.display = (showMerge && !isStandaloneMode) ? 'flex' : 'none';
        if (mergeDefaultBtn) mergeDefaultBtn.style.display = showMerge ? 'flex' : 'none';
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
function getMessagesFingerprint(list) { return chatGetMessagesFingerprint(list); }

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
        const images = collectAndClearImages('active');
        const extra = images.length > 0 ? { images } : {};
        await chatAPI.createMessage(taskId, currentChatId, text, extra);
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

function getExistingImageNames() {
    const names = new Set();
    for (const msg of messagesCache) {
        const extra = parseMsgExtra(msg.extra);
        for (const img of (extra.images || [])) {
            if (img.filename) names.add(img.filename);
        }
    }
    for (const img of pendingImages) {
        if (img.filename) names.add(img.filename);
    }
    for (const img of welcomePendingImages) {
        if (img.filename) names.add(img.filename);
    }
    return names;
}

function pasteImageExt(mimeType) {
    const map = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp' };
    return map[mimeType] || '.png';
}

function generateUniqueImageName(ext, existingNames) {
    let n = 1;
    while (existingNames.has(`pasted_image_${n}${ext}`)) n++;
    const name = `pasted_image_${n}${ext}`;
    existingNames.add(name);
    return name;
}

async function handlePasteImages(e, target) {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;

    const imageFiles = [];
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            const file = item.getAsFile();
            if (file) imageFiles.push(file);
        }
    }
    if (imageFiles.length === 0) return;

    e.preventDefault();

    const isWelcome = (target === 'welcome');
    const list = isWelcome ? welcomePendingImages : pendingImages;
    const existingNames = getExistingImageNames();

    for (const file of imageFiles) {
        if (file.size > 10 * 1024 * 1024) {
            showToast(`${file.name || '粘贴图片'} 超过 10MB 限制`, 'error');
            continue;
        }
        const ext = pasteImageExt(file.type);
        const uniqueName = generateUniqueImageName(ext, existingNames);
        const renamedFile = new File([file], uniqueName, { type: file.type });
        try {
            const res = await chatAPI.uploadImage(renamedFile);
            list.push(res.data);
        } catch (err) {
            showToast(err.message, 'error');
        }
    }
    renderPendingImages(target);
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 180) + 'px';
}

// ===== Image attachment helpers =====

function triggerImageSelect(inputId) {
    document.getElementById(inputId).click();
}

async function handleImageSelect(event, target) {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const isWelcome = (target === 'welcome');
    const list = isWelcome ? welcomePendingImages : pendingImages;

    for (const file of files) {
        if (!file.type.startsWith('image/')) {
            showToast('仅支持图片文件', 'error');
            continue;
        }
        if (file.size > 10 * 1024 * 1024) {
            showToast(`${file.name} 超过 10MB 限制`, 'error');
            continue;
        }
        try {
            const res = await chatAPI.uploadImage(file);
            list.push(res.data);
        } catch (e) {
            showToast(e.message, 'error');
        }
    }
    // 清空 input 以允许重复选择同一文件
    event.target.value = '';
    renderPendingImages(target);
}

function removePendingImage(index, target) {
    const list = (target === 'welcome') ? welcomePendingImages : pendingImages;
    list.splice(index, 1);
    renderPendingImages(target);
}

function renderPendingImages(target) {
    const isWelcome = (target === 'welcome');
    const list = isWelcome ? welcomePendingImages : pendingImages;
    const containerId = isWelcome ? 'welcome-pending-images' : 'pending-images';
    chatRenderPendingImages(list, containerId, 'removePendingImage', target);
}

function collectAndClearImages(target) {
    const isWelcome = (target === 'welcome');
    const list = isWelcome ? welcomePendingImages : pendingImages;
    const containerId = isWelcome ? 'welcome-pending-images' : 'pending-images';
    return chatCollectAndClearImages(list, containerId, 'removePendingImage', target);
}

// ===== Message image display helpers =====

function renderMsgImages(images) {
    return chatRenderMsgImages(images, 'viewChatImage');
}

function viewChatImage(ossPath, filename) {
    chatViewImage(ossPath, filename, 'image-preview-modal', 'image-preview-img', 'image-preview-title');
}

function closeImagePreviewModal() {
    chatCloseImagePreview('image-preview-modal', 'image-preview-img');
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

    if (isStandaloneMode && !standaloneClientId) {
        showToast('未指定应用，无法发送', 'error');
        return;
    }

    const btn = document.getElementById('welcome-send-btn');
    btn.disabled = true;

    try {
        const images = collectAndClearImages('welcome');
        const extra = images.length > 0 ? { images } : {};
        let res;
        if (isStandaloneMode) {
            res = await chatAPI.createStandaloneChatWithMessage(text, standaloneClientId, extra);
        } else {
            res = await chatAPI.createChatWithMessage(taskId, text, extra);
        }
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

// ===== Merge actions =====
function _getRepoName(url) {
    if (!url) return '';
    const m = url.match(/[/:]([\w.-]+?)(?:\.git)?$/);
    return m ? m[1] : url;
}

function _buildRepoTable(repos) {
    if (isStandaloneMode) {
        const lines = ['| 仓库 | 分支前缀 | 默认分支 | chat 分支 |', '|------|---------|---------|----------|'];
        for (const repo of repos) {
            const name = _getRepoName(repo.url);
            const prefix = repo.branch_prefix || 'ai_';
            const defaultBr = repo.default_branch || 'main';
            const chatBr = `${prefix}0_${currentChatId}`;
            lines.push(`| ${name} | ${prefix} | ${defaultBr} | ${chatBr} |`);
        }
        return lines.join('\n');
    }
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
    const repos = clientConfigCache.repos.filter(r => !r.docs_repo);
    const repoTable = _buildRepoTable(repos);

    return `# 合并 Chat 分支到 Task 分支

## 背景信息

- task_id: ${taskId}，chat_id: ${currentChatId}
- 以下仓库需要操作（已排除文档仓库）：

${repoTable}

## 操作步骤

对上述 **每个仓库** 执行：

1. **Squash 合并**：将 chat 分支相对于 task 分支的差异合并为一个 commit（有意义的 commit message）
   \`git checkout <task分支> && git merge --squash <chat分支> && git commit\`
2. **推送**：\`git push origin <task分支>\`（远端有新提交则先 \`git pull --rebase\`）
3. **清理 PR**：删除远端 chat 分支关闭关联 PR：\`git push origin --delete <chat分支>\`

## 注意事项

- 无差异的仓库跳过
- 各仓库独立操作，遇冲突尝试解决，无法解决时报告
`;
}

function _buildMergeToDefaultBranchPrompt() {
    if (!clientConfigCache || !clientConfigCache.repos) return null;
    const repos = clientConfigCache.repos.filter(r => !r.docs_repo);
    const repoTable = _buildRepoTable(repos);

    // 独立 Chat 模式：直接从 chat 分支合并到默认分支
    if (isStandaloneMode) {
        return `# 合并 Chat 分支到默认分支

## 背景信息

- chat_id: ${currentChatId}（独立 Chat，直接合并到默认分支）
- 以下仓库需要操作（已排除文档仓库）：

${repoTable}

## 操作步骤

对上述 **每个仓库** 执行：

1. **Rebase 到默认分支**：\`git rebase origin/<默认分支> <chat分支>\`
2. **Fast-forward 合并**：\`git checkout <默认分支> && git merge --ff-only <chat分支>\`
3. **推送**：\`git push origin <默认分支>\`
4. **清理 PR**：删除远端 chat 分支：\`git push origin --delete <chat分支>\`

## 注意事项

- 无差异的仓库跳过
- 各仓库独立操作，遇冲突尝试解决，无法解决时报告
- 保持默认分支线性 commit 历史
`;
    }

    return `# 合并 Chat→Task→默认分支

## 背景信息

- task_id: ${taskId}，chat_id: ${currentChatId}
- 以下仓库需要操作（已排除文档仓库）：

${repoTable}

## 第一步：Chat 分支 → Task 分支

对上述 **每个仓库** 执行：

1. **Squash 合并**：将 chat 分支相对于 task 分支的差异合并为一个 commit（有意义的 commit message）
   \`git checkout <task分支> && git merge --squash <chat分支> && git commit\`
2. **推送**：\`git push origin <task分支>\`（远端有新提交则先 \`git pull --rebase\`）
3. **清理 PR**：删除远端 chat 分支：\`git push origin --delete <chat分支>\`

## 第二步：Task 分支 → 默认分支

第一步完成后，对 **每个仓库** 继续执行：

1. **拉取最新默认分支**：\`git checkout <默认分支> && git pull origin <默认分支>\`
2. **Rebase + Fast-forward**：\`git rebase <默认分支> <task分支> && git checkout <默认分支> && git merge --ff-only <task分支>\`
3. **推送**：\`git push origin <默认分支>\`
4. **清理 PR**：删除远端 task 分支：\`git push origin --delete <task分支>\`

## 注意事项

- 无差异的仓库/步骤跳过
- 各仓库独立操作，遇冲突尝试解决，无法解决时报告
- 保持默认分支线性 commit 历史
`;
}

async function mergeToTask() {
    if (!currentChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const prompt = _buildMergeToTaskPrompt();
    if (!prompt) { showToast('未获取到仓库配置信息', 'error'); return; }

    const btn = document.getElementById('merge-to-task-btn');
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
    if (!currentChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const prompt = _buildMergeToDefaultBranchPrompt();
    if (!prompt) { showToast('未获取到仓库配置信息', 'error'); return; }

    const btn = document.getElementById('merge-to-default-btn');
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
