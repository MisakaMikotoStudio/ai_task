// chat-helpers.js - 聊天相关的共享工具函数（chat.js 和 standalone-chat.js 共用）

/**
 * 格式化时间：当天只显示时分，跨天显示月日+时分
 */
function chatFormatTime(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ' ' +
        d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

/**
 * 解析消息 extra 字段（JSON 字符串或对象）
 */
function chatParseMsgExtra(extra) {
    if (!extra) return {};
    if (typeof extra === 'string') {
        try { return JSON.parse(extra); } catch { return {}; }
    }
    return extra;
}

/**
 * 渲染 markdown 输出（marked + DOMPurify）
 */
function chatRenderOutput(output) {
    if (!output) return '';
    if (typeof marked !== 'undefined') {
        try { return marked.parse(output); } catch (_) {}
    }
    return `<p>${escapeHtml(output).replace(/\n/g, '<br>')}</p>`;
}

/**
 * 生成消息输出的缓存 key
 */
function chatGetOutputCacheKey(msg) {
    const updated = msg.updated_at || msg.created_at || '';
    const outputLen = msg.output ? String(msg.output).length : 0;
    return `${msg.id}|${msg.status}|${updated}|${outputLen}`;
}

/**
 * 带缓存的 markdown 输出渲染
 * @param {Object} msg - 消息对象
 * @param {Map} cache - 缓存 Map 实例
 */
function chatRenderOutputCached(msg, cache) {
    if (!msg || !msg.output) return '';
    const key = chatGetOutputCacheKey(msg);
    const cached = cache.get(key);
    if (cached) return cached;

    const html = chatRenderOutput(msg.output);
    cache.set(key, html);

    if (cache.size > 1000) cache.clear();
    return html;
}

/**
 * 消息列表指纹（用于轮询检测变化）
 */
function chatGetMessagesFingerprint(list) {
    return (list || []).map(m => {
        const updated = m.updated_at || m.created_at || '';
        return `${m.id}:${m.status}:${updated}`;
    }).join('|');
}

/**
 * 渲染消息附带的图片链接
 * @param {Array} images - 图片列表
 * @param {string} viewFnName - 点击查看时调用的全局函数名
 */
function chatRenderMsgImages(images, viewFnName) {
    if (!images || !Array.isArray(images) || images.length === 0) return '';
    const items = images.map(img => {
        const ossPath = escapeHtml(img.oss_path || '');
        const filename = escapeHtml(img.filename || 'image');
        return `<span class="msg-image-link" onclick="${viewFnName}('${ossPath}', '${filename}')" title="点击查看">📎 ${filename}</span>`;
    }).join('');
    return `<div class="msg-image-list">${items}</div>`;
}

/**
 * 查看聊天图片（通用，可配置 DOM ID）
 * @param {string} ossPath - OSS 路径
 * @param {string} filename - 文件名
 * @param {string} modalId - 模态框 DOM ID
 * @param {string} imgId - 图片元素 DOM ID
 * @param {string} titleId - 标题元素 DOM ID
 */
function chatViewImage(ossPath, filename, modalId, imgId, titleId) {
    const modal = document.getElementById(modalId);
    const img = document.getElementById(imgId);
    const title = document.getElementById(titleId);

    title.textContent = filename;
    img.src = '';
    img.alt = filename;

    chatAPI.getPresignedImageUrl(ossPath)
    .then(res => {
        if (res.code !== 200 || !res.data || !res.data.url) {
            throw new Error(res.message || '获取预签名 URL 失败');
        }
        img.src = res.data.url;
    })
    .catch(e => {
        img.alt = '图片加载失败';
        showToast('图片加载失败: ' + e.message, 'error');
    });

    modal.classList.add('show');
}

/**
 * 关闭图片预览模态框（通用，可配置 DOM ID）
 * @param {string} modalId - 模态框 DOM ID
 * @param {string} imgId - 图片元素 DOM ID
 */
function chatCloseImagePreview(modalId, imgId) {
    const modal = document.getElementById(modalId);
    const img = document.getElementById(imgId);
    modal.classList.remove('show');
    if (img.src && img.src.startsWith('blob:')) {
        URL.revokeObjectURL(img.src);
    }
    img.src = '';
}

/**
 * 渲染待上传图片列表
 * @param {Array} list - 待上传图片数组
 * @param {string} containerId - 容器 DOM ID
 * @param {string} removeFnName - 移除按钮调用的全局函数名
 * @param {string} target - 传给 removeFn 的 target 参数
 */
function chatRenderPendingImages(list, containerId, removeFnName, target) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (list.length === 0) {
        container.innerHTML = '';
        container.style.display = 'none';
        return;
    }

    container.style.display = 'flex';
    container.innerHTML = list.map((img, i) => `
        <div class="pending-image-item">
            <span class="pending-image-name" title="${escapeHtml(img.filename)}">
                📎 ${escapeHtml(img.filename)}
            </span>
            <button class="pending-image-remove" onclick="${removeFnName}(${i}, '${target}')" title="移除">×</button>
        </div>
    `).join('');
}

/**
 * 收集并清空待上传图片
 * @param {Array} list - 待上传图片数组
 * @param {string} containerId - 容器 DOM ID
 * @param {string} removeFnName - 重渲染用的函数名
 * @param {string} target - target 参数
 */
function chatCollectAndClearImages(list, containerId, removeFnName, target) {
    const images = list.slice();
    list.length = 0;
    chatRenderPendingImages(list, containerId, removeFnName, target);
    return images;
}
