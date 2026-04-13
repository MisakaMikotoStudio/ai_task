// standalone-chat.js - 独立聊天面板（split-panel）
// ===== Chat 跳转 =====

function openTaskChat(taskId) {
    window.open(`chat.html?task_id=${taskId}`, '_blank');
}


// ===== 独立 Chat（split-panel, inline rendering） =====

let scStatusFilter = ['pending', 'running', 'completed'];
let scChatList = [];
let scCurrentPage = 1;
let scPageSize = 20;
let scTotal = 0;
let scSelectedChatId = null;
let scSelectedClientId = null;
let scClientsCache = [];
let scInitialized = false;

// Chat detail state
let scMessagesCache = [];
let scMessagesFingerprint = '';
const scOutputHtmlCache = new Map();
let scRunningMessageId = null;
let scPollTimer = null;
let scMergeRequestStore = {};
let scClientConfigCache = null;

// Image attachment state
let scWelcomePendingImages = [];
let scDetailPendingImages = [];

const SC_CLIENT_CACHE_KEY = 'sc_last_client_id';

// ── Init marked.js with highlight.js ──
(function scInitMarked() {
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

// ── Helpers（共享函数来自 chat-helpers.js）──
function scFormatTime(dateStr) { return chatFormatTime(dateStr); }
function scParseMsgExtra(extra) { return chatParseMsgExtra(extra); }
function scRenderOutput(output) { return chatRenderOutput(output); }
function scGetOutputCacheKey(msg) { return chatGetOutputCacheKey(msg); }
function scRenderOutputCached(msg) { return chatRenderOutputCached(msg, scOutputHtmlCache); }
function scGetMessagesFingerprint(list) { return chatGetMessagesFingerprint(list); }

function scAutoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

// ── Init panel ──
function initStandaloneChatPanel() {
    if (scInitialized) return;
    scInitialized = true;

    // 状态筛选
    const filterEl = document.getElementById('chat-status-filter');
    if (filterEl) {
        filterEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', () => {
                const label = cb.parentElement;
                if (cb.checked) { label.classList.add('checked'); }
                else { label.classList.remove('checked'); }
                scStatusFilter = Array.from(filterEl.querySelectorAll('input:checked')).map(c => c.value);
                scCurrentPage = 1;
                scChatList = [];
                loadStandaloneChatList();
            });
        });
    }

    // 新建按钮
    const newBtn = document.getElementById('sc-new-chat-btn');
    if (newBtn) {
        newBtn.addEventListener('click', () => scShowWelcome());
    }

    // 加载更多
    const loadMoreBtn = document.getElementById('sc-load-more-btn');
    if (loadMoreBtn) {
        loadMoreBtn.addEventListener('click', () => {
            scCurrentPage++;
            loadStandaloneChatList(true);
        });
    }

    // Welcome composer: 发送按钮
    const sendBtn = document.getElementById('sc-welcome-send-btn');
    if (sendBtn) {
        sendBtn.addEventListener('click', () => scSendNewChat());
    }

    // Welcome composer: Ctrl+Enter
    const textarea = document.getElementById('sc-welcome-input');
    if (textarea) {
        textarea.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault();
                scSendNewChat();
            }
        });
        textarea.addEventListener('paste', (e) => scHandlePasteImages(e, 'welcome'));
    }

    // 客户端选择变化时缓存
    const clientSel = document.getElementById('sc-client-select');
    if (clientSel) {
        clientSel.addEventListener('change', () => {
            if (clientSel.value) {
                localStorage.setItem(SC_CLIENT_CACHE_KEY, clientSel.value);
            }
        });
    }

    // Detail composer: 发送按钮
    const detailSendBtn = document.getElementById('sc-detail-send-btn');
    if (detailSendBtn) {
        detailSendBtn.addEventListener('click', () => scSendMessage());
    }

    // Detail composer: 终止按钮
    const detailStopBtn = document.getElementById('sc-detail-stop-btn');
    if (detailStopBtn) {
        detailStopBtn.addEventListener('click', () => scTerminateMessage());
    }

    // Detail composer: Ctrl+Enter
    const detailInput = document.getElementById('sc-detail-input');
    if (detailInput) {
        detailInput.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault();
                scSendMessage();
            }
        });
        detailInput.addEventListener('input', () => scAutoResize(detailInput));
        detailInput.addEventListener('paste', (e) => scHandlePasteImages(e, 'detail'));
    }

    // Merge default branch
    const mergeDefaultBtn = document.getElementById('sc-merge-default-btn');
    if (mergeDefaultBtn) {
        mergeDefaultBtn.addEventListener('click', () => scMergeToDefaultBranch());
    }

    // Merge request modal: click overlay to close
    const mergeModal = document.getElementById('sc-merge-modal');
    if (mergeModal) {
        mergeModal.addEventListener('click', (e) => {
            if (e.target === mergeModal) scCloseMergeRequestModal();
        });
    }

    // 加载客户端列表
    scLoadClients();
}

// ── Clients ──
async function scLoadClients() {
    try {
        const result = await activeClientAPI.list();
        scClientsCache = result.data || [];
    } catch (e) {
        scClientsCache = [];
    }
    scRenderClientSelect();
}

function scRenderClientSelect() {
    const sel = document.getElementById('sc-client-select');
    if (!sel) return;
    const lastClientId = localStorage.getItem(SC_CLIENT_CACHE_KEY) || '';
    let html = '<option value="">-- 选择应用 --</option>';
    for (const c of scClientsCache) {
        const selected = String(c.id) === lastClientId ? ' selected' : '';
        html += `<option value="${c.id}"${selected}>${escapeHtml(c.name)}</option>`;
    }
    sel.innerHTML = html;
}

// ── Chat list ──
async function loadStandaloneChatList(append = false) {
    try {
        const res = await chatAPI.listStandaloneChats({
            status: scStatusFilter,
            page: scCurrentPage,
            pageNum: scPageSize,
        });
        const pageData = res.data || {};
        const items = pageData.items || [];
        scTotal = pageData.total || 0;

        if (append) {
            scChatList = scChatList.concat(items);
        } else {
            scChatList = items;
        }

        scRenderChatList();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function scRenderChatList() {
    const listEl = document.getElementById('sc-chat-list');
    const loadMoreEl = document.getElementById('sc-load-more');

    if (scChatList.length === 0) {
        listEl.innerHTML = '<div class="sc-chat-empty">暂无 Chat</div>';
        if (loadMoreEl) loadMoreEl.style.display = 'none';
        return;
    }

    const statusLabels = {
        pending: '等待',
        running: '执行中',
        completed: '完成',
        terminated: '终止'
    };

    listEl.innerHTML = scChatList.map(chat => {
        const isActive = chat.id === scSelectedChatId ? ' active' : '';
        const safeTitle = escapeHtml(chat.title || '无标题');
        const statusText = statusLabels[chat.status] || chat.status;
        const clientName = escapeHtml(chat.client_name || '');
        const timeStr = formatDateTime(chat.updated_at || chat.created_at);

        return `
        <div class="sc-chat-item${isActive}" onclick="scSelectChat(${chat.id}, ${chat.client_id || 0})" data-chat-id="${chat.id}">
            <div class="sc-chat-item-row1">
                <span class="sc-chat-item-id">#${chat.id}</span>
                <span class="sc-chat-status-dot ${chat.status}"></span>
                <span class="sc-chat-status-label ${chat.status}">${statusText}</span>
            </div>
            <div class="sc-chat-item-title">${safeTitle}</div>
            <div class="sc-chat-item-meta">
                ${clientName ? `<span>${clientName}</span>` : ''}
                <span>${timeStr}</span>
            </div>
        </div>`;
    }).join('');

    if (loadMoreEl) {
        loadMoreEl.style.display = scChatList.length < scTotal ? '' : 'none';
    }
}

// ── Welcome / Detail switching ──
function scShowWelcome() {
    scStopPolling();
    scSelectedChatId = null;
    scSelectedClientId = null;
    scMessagesCache = [];
    scRunningMessageId = null;

    document.querySelectorAll('.sc-chat-item.active').forEach(el => el.classList.remove('active'));
    document.getElementById('sc-welcome').style.display = '';
    document.getElementById('sc-detail').style.display = 'none';

    const textarea = document.getElementById('sc-welcome-input');
    if (textarea) textarea.value = '';
    scRenderClientSelect();
}

async function scSelectChat(chatId, clientId) {
    scStopPolling();
    scSelectedChatId = chatId;
    scSelectedClientId = clientId;

    // 高亮左侧
    document.querySelectorAll('.sc-chat-item').forEach(el => {
        el.classList.toggle('active', Number(el.dataset.chatId) === chatId);
    });

    // 切换视图
    document.getElementById('sc-welcome').style.display = 'none';
    document.getElementById('sc-detail').style.display = '';

    // Update topbar
    const chat = scChatList.find(c => c.id === chatId);
    if (chat) scUpdateTopbar(chat);

    // 加载客户端配置（合并按钮需要）
    await scLoadClientConfig(clientId);

    // 加载消息
    await scLoadMessages(chatId);
}

function scUpdateTopbar(chat) {
    document.getElementById('sc-topbar-title').textContent = chat.title || `Chat #${chat.id}`;
    const badge = document.getElementById('sc-topbar-badge');
    const labels = { running: '执行中', completed: '执行完成', terminated: '已终止' };
    badge.textContent = labels[chat.status] || '';
    badge.className = `sc-detail-topbar-badge ${chat.status || ''}`;
}

// ── Client config ──
async function scLoadClientConfig(clientId) {
    if (!clientId) { scClientConfigCache = null; return; }
    try {
        const res = await clientAPI.getConfig(clientId);
        scClientConfigCache = res.data;
    } catch (e) {
        scClientConfigCache = null;
    }
}

// ── Messages ──
async function scLoadMessages(chatId) {
    try {
        const res = await chatAPI.listMessages(0, chatId);
        scMessagesCache = res.data || [];
        scMessagesFingerprint = scGetMessagesFingerprint(scMessagesCache);
        scRenderFeed();
        scUpdateComposerState();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function scRenderFeed() {
    const feed = document.getElementById('sc-feed');

    if (scMessagesCache.length === 0) {
        feed.innerHTML = `
            <div class="sc-feed-empty">
                <div class="sc-feed-empty-icon">✨</div>
                <div class="sc-feed-empty-text">开始对话</div>
                <div class="sc-feed-empty-sub">在下方输入框输入您的问题</div>
            </div>`;
        return;
    }

    const statusChipMap = {
        pending: ['pending', '等待执行'],
        running: ['running', '执行中…'],
        completed: ['completed', '执行完成'],
        terminated: ['terminated', '已终止']
    };

    feed.innerHTML = scMessagesCache.map(msg => {
        const [chipClass, chipLabel] = statusChipMap[msg.status] || ['', msg.status];
        const extra = scParseMsgExtra(msg.extra);

        // User row
        const msgImages = extra.images || [];
        const userRow = `
        <div class="sc-msg-user-row">
            <div class="sc-msg-avatar user">你</div>
            <div class="sc-msg-body">
                <div class="sc-msg-header">
                    <span class="sc-msg-role">You</span>
                    <span class="sc-msg-time">${scFormatTime(msg.created_at)}</span>
                </div>
                <div class="sc-msg-text">${escapeHtml(msg.input)}</div>
                ${scRenderMsgImages(msgImages)}
            </div>
        </div>`;

        // Agent output
        let outputHtml;
        if (msg.output) {
            outputHtml = `<div class="sc-msg-output">${scRenderOutputCached(msg)}</div>`;
        } else if (msg.status === 'pending' || msg.status === 'running') {
            outputHtml = `
                <div class="sc-typing-indicator">
                    <div class="sc-typing-dot"></div>
                    <div class="sc-typing-dot"></div>
                    <div class="sc-typing-dot"></div>
                </div>`;
        } else {
            outputHtml = `<div class="sc-msg-output" style="color:var(--text-muted);font-style:italic">无输出</div>`;
        }

        // Extra buttons
        let extraBtns = '';
        if (extra.develop_doc) {
            extraBtns += `<a class="sc-msg-extra-btn doc-btn" href="${escapeHtml(extra.develop_doc)}" target="_blank" rel="noopener noreferrer">📄 开发文档</a>`;
        }
        if (extra && extra.merge_request !== undefined) {
            const storeKey = `sc_msg_${msg.id}`;
            const mrData = Array.isArray(extra.merge_request) ? extra.merge_request : [];
            scMergeRequestStore[storeKey] = mrData;
            extraBtns += `<button class="sc-msg-extra-btn mr-btn" onclick="scShowMergeRequestModal('${storeKey}')">🔀 变更详情</button>`;
        }

        const agentRow = `
        <div class="sc-msg-agent-row">
            <div class="sc-msg-avatar agent">⚡</div>
            <div class="sc-msg-body">
                <div class="sc-msg-header">
                    <span class="sc-msg-role">Agent</span>
                    <span class="sc-msg-time">${scFormatTime(msg.updated_at)}</span>
                </div>
                ${outputHtml}
                <div class="sc-msg-status-row">
                    <span class="sc-msg-status-chip ${chipClass}">${chipLabel}</span>
                    ${extraBtns}
                </div>
            </div>
        </div>`;

        return `<div class="sc-msg-turn">${userRow}${agentRow}</div>`;
    }).join('');

    // Scroll to bottom
    setTimeout(() => { feed.scrollTop = feed.scrollHeight; }, 50);
}

// ── Composer state ──
function scUpdateComposerState() {
    const running = scMessagesCache.find(m => m.status === 'pending' || m.status === 'running');
    scRunningMessageId = running ? running.id : null;

    const box = document.getElementById('sc-detail-composer-box');
    const input = document.getElementById('sc-detail-input');
    const sendBtn = document.getElementById('sc-detail-send-btn');
    const stopBtn = document.getElementById('sc-detail-stop-btn');
    const hintEl = document.getElementById('sc-detail-hint');
    const hintText = document.getElementById('sc-detail-hint-text');
    const mergeDefaultBtn = document.getElementById('sc-merge-default-btn');
    const showMerge = !!scClientConfigCache;

    if (scRunningMessageId) {
        box.classList.add('locked');
        input.disabled = true;
        sendBtn.style.display = 'none';
        if (mergeDefaultBtn) mergeDefaultBtn.style.display = 'none';
        stopBtn.style.display = 'flex';
        hintEl.className = 'sc-detail-hint warn';
        hintText.textContent = '当前有消息正在执行，无法输入新消息';
        scStartPolling();
    } else {
        box.classList.remove('locked');
        input.disabled = false;
        sendBtn.style.display = 'flex';
        if (mergeDefaultBtn) mergeDefaultBtn.style.display = showMerge ? 'flex' : 'none';
        stopBtn.style.display = 'none';
        hintEl.className = 'sc-detail-hint';
        hintText.textContent = '尽管问，带图也行';
        scStopPolling();
    }

    // Update topbar
    const chat = scChatList.find(c => c.id === scSelectedChatId);
    if (chat) scUpdateTopbar(chat);
}

// ── Polling ──
function scStartPolling() {
    if (scPollTimer) return;
    scPollTimer = setInterval(async () => {
        if (!scSelectedChatId) return;
        try {
            const res = await chatAPI.listMessages(0, scSelectedChatId);
            const fresh = res.data || [];
            const nextFingerprint = scGetMessagesFingerprint(fresh);
            if (nextFingerprint !== scMessagesFingerprint) {
                const prevRunningId = scRunningMessageId;
                scMessagesFingerprint = nextFingerprint;
                scMessagesCache = fresh;
                scRenderFeed();
                scUpdateComposerState();
                if (prevRunningId !== scRunningMessageId) {
                    scCurrentPage = 1;
                    scChatList = [];
                    await loadStandaloneChatList();
                }
            }
        } catch { /* silent */ }
    }, 3000);
}

function scStopPolling() {
    if (scPollTimer) { clearInterval(scPollTimer); scPollTimer = null; }
}

// ── Image attachment helpers ──
function scTriggerImageSelect(inputId) {
    document.getElementById(inputId).click();
}

async function scHandleImageSelect(event, target) {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const isWelcome = (target === 'welcome');
    const list = isWelcome ? scWelcomePendingImages : scDetailPendingImages;

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
    event.target.value = '';
    scRenderPendingImages(target);
}

function scRemovePendingImage(index, target) {
    const list = (target === 'welcome') ? scWelcomePendingImages : scDetailPendingImages;
    list.splice(index, 1);
    scRenderPendingImages(target);
}

function scRenderPendingImages(target) {
    const isWelcome = (target === 'welcome');
    const list = isWelcome ? scWelcomePendingImages : scDetailPendingImages;
    const containerId = isWelcome ? 'sc-welcome-pending-images' : 'sc-detail-pending-images';
    chatRenderPendingImages(list, containerId, 'scRemovePendingImage', target);
}

function scCollectAndClearImages(target) {
    const isWelcome = (target === 'welcome');
    const list = isWelcome ? scWelcomePendingImages : scDetailPendingImages;
    const containerId = isWelcome ? 'sc-welcome-pending-images' : 'sc-detail-pending-images';
    return chatCollectAndClearImages(list, containerId, 'scRemovePendingImage', target);
}

function scGetExistingImageNames() {
    const names = new Set();
    for (const msg of scMessagesCache) {
        const extra = scParseMsgExtra(msg.extra);
        for (const img of (extra.images || [])) {
            if (img.filename) names.add(img.filename);
        }
    }
    for (const img of scDetailPendingImages) {
        if (img.filename) names.add(img.filename);
    }
    for (const img of scWelcomePendingImages) {
        if (img.filename) names.add(img.filename);
    }
    return names;
}

function scPasteImageExt(mimeType) {
    const map = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp' };
    return map[mimeType] || '.png';
}

function scGenerateUniqueImageName(ext, existingNames) {
    let n = 1;
    while (existingNames.has(`pasted_image_${n}${ext}`)) n++;
    const name = `pasted_image_${n}${ext}`;
    existingNames.add(name);
    return name;
}

async function scHandlePasteImages(e, target) {
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
    const list = isWelcome ? scWelcomePendingImages : scDetailPendingImages;
    const existingNames = scGetExistingImageNames();

    for (const file of imageFiles) {
        if (file.size > 10 * 1024 * 1024) {
            showToast(`${file.name || '粘贴图片'} 超过 10MB 限制`, 'error');
            continue;
        }
        const ext = scPasteImageExt(file.type);
        const uniqueName = scGenerateUniqueImageName(ext, existingNames);
        const renamedFile = new File([file], uniqueName, { type: file.type });
        try {
            const res = await chatAPI.uploadImage(renamedFile);
            list.push(res.data);
        } catch (err) {
            showToast(err.message, 'error');
        }
    }
    scRenderPendingImages(target);
}

function scRenderMsgImages(images) {
    if (!images || !Array.isArray(images) || images.length === 0) return '';
    const items = images.map(img => {
        const ossPath = escapeHtml(img.oss_path || '');
        const filename = escapeHtml(img.filename || 'image');
        return `<span class="sc-msg-image-link" onclick="scViewChatImage('${ossPath}', '${filename}')" title="点击查看">📎 ${filename}</span>`;
    }).join('');
    return `<div class="sc-msg-image-list">${items}</div>`;
}

function scViewChatImage(ossPath, filename) {
    chatViewImage(ossPath, filename, 'sc-image-preview-modal', 'sc-image-preview-img', 'sc-image-preview-title');
}

function scCloseImagePreviewModal() {
    chatCloseImagePreview('sc-image-preview-modal', 'sc-image-preview-img');
}

// ── Send message (active chat) ──
async function scSendMessage() {
    if (!scSelectedChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const input = document.getElementById('sc-detail-input');
    const text = input.value.trim();
    if (!text) return;

    const images = scCollectAndClearImages('detail');
    const extra = images.length > 0 ? { images } : {};

    const btn = document.getElementById('sc-detail-send-btn');
    btn.disabled = true;
    try {
        await chatAPI.createMessage(0, scSelectedChatId, text, extra);
        input.value = '';
        scAutoResize(input);
        await scLoadMessages(scSelectedChatId);
        scCurrentPage = 1;
        scChatList = [];
        await loadStandaloneChatList();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ── Terminate message ──
async function scTerminateMessage() {
    if (!scRunningMessageId || !scSelectedChatId) return;
    const btn = document.getElementById('sc-detail-stop-btn');
    btn.disabled = true;
    btn.textContent = '终止中…';
    try {
        const res = await chatAPI.deleteMessage(0, scSelectedChatId, scRunningMessageId);
        const inputText = res?.data?.input || '';
        showToast('已撤销，内容已回填', 'success');
        await scLoadMessages(scSelectedChatId);
        scCurrentPage = 1;
        scChatList = [];
        await loadStandaloneChatList();
        if (inputText) {
            const inputEl = document.getElementById('sc-detail-input');
            inputEl.value = inputText;
            scAutoResize(inputEl);
            inputEl.focus();
            inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
        }
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '⬛ 终止';
    }
}

// ── Create new chat ──
async function scSendNewChat() {
    const sel = document.getElementById('sc-client-select');
    const textarea = document.getElementById('sc-welcome-input');
    const sendBtn = document.getElementById('sc-welcome-send-btn');

    const clientId = sel ? parseInt(sel.value) : 0;
    const inputText = textarea ? textarea.value.trim() : '';

    if (!clientId) {
        showToast('请选择一个应用', 'error');
        return;
    }
    if (!inputText) {
        showToast('请输入内容', 'error');
        return;
    }

    const images = scCollectAndClearImages('welcome');
    const extra = images.length > 0 ? { images } : {};

    localStorage.setItem(SC_CLIENT_CACHE_KEY, String(clientId));
    sendBtn.disabled = true;
    try {
        const res = await chatAPI.createStandaloneChatWithMessage(inputText, clientId, extra);
        const newChatId = res.data.chat.id;
        scCurrentPage = 1;
        scChatList = [];
        await loadStandaloneChatList();
        await scSelectChat(newChatId, clientId);
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        sendBtn.disabled = false;
    }
}

// ── Merge actions ──
function _scGetRepoName(url) {
    if (!url) return '';
    const m = url.match(/[/:]([\w.-]+?)(?:\.git)?$/);
    return m ? m[1] : url;
}

function _scBuildRepoTable(repos) {
    const lines = ['| 仓库 | 分支前缀 | 默认分支 | chat 分支 |', '|------|---------|---------|----------|'];
    for (const repo of repos) {
        const name = _scGetRepoName(repo.url);
        const prefix = repo.branch_prefix || 'ai_';
        const defaultBr = repo.default_branch || 'main';
        const chatBr = `${prefix}0_${scSelectedChatId}`;
        lines.push(`| ${name} | ${prefix} | ${defaultBr} | ${chatBr} |`);
    }
    return lines.join('\n');
}

function _scBuildMergeToDefaultBranchPrompt() {
    if (!scClientConfigCache || !scClientConfigCache.repos) return null;
    const repos = scClientConfigCache.repos.filter(r => !r.docs_repo);
    const repoTable = _scBuildRepoTable(repos);

    return `# 合并 Chat 分支到默认分支

## 背景信息

- chat_id: ${scSelectedChatId}（独立 Chat，直接合并到默认分支）
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

async function scMergeToDefaultBranch() {
    if (!scSelectedChatId) { showToast('请先选择或新建一个 Chat', 'error'); return; }

    const prompt = _scBuildMergeToDefaultBranchPrompt();
    if (!prompt) { showToast('未获取到仓库配置信息', 'error'); return; }

    const btn = document.getElementById('sc-merge-default-btn');
    btn.disabled = true;
    try {
        await chatAPI.createMessage(0, scSelectedChatId, prompt);
        await scLoadMessages(scSelectedChatId);
        scCurrentPage = 1;
        scChatList = [];
        await loadStandaloneChatList();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ── Merge request modal ──
function scShowMergeRequestModal(storeKey) {
    const body = document.getElementById('sc-merge-body');
    const data = scMergeRequestStore[storeKey];
    const list = Array.isArray(data) ? data : [];

    if (list.length === 0) {
        body.innerHTML = '<div class="sc-mr-empty">暂无变更记录</div>';
        document.getElementById('sc-merge-modal').classList.add('active');
        return;
    }

    body.innerHTML = `
        <table class="sc-mr-table">
            <thead>
                <tr><th>项目</th><th>分支</th><th>提交</th><th>PR</th></tr>
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
        </table>`;

    document.getElementById('sc-merge-modal').classList.add('active');
}

function scCloseMergeRequestModal() {
    document.getElementById('sc-merge-modal').classList.remove('active');
}
