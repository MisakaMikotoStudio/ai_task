/**
 * 工具函数
 */

// 生成UUID（用于traceId）
function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// 简单的SHA256哈希函数（用于密码哈希）
// 支持安全上下文（HTTPS/localhost）和非安全上下文（HTTP）
async function sha256(message) {
    // 优先使用 Web Crypto API（安全上下文下可用）
    if (window.crypto && window.crypto.subtle) {
        const msgBuffer = new TextEncoder().encode(message);
        const hashBuffer = await crypto.subtle.digest('SHA-256', msgBuffer);
        const hashArray = Array.from(new Uint8Array(hashBuffer));
        return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
    }
    
    // 后备：纯 JavaScript SHA256 实现（用于非安全上下文）
    return sha256Fallback(message);
}

// 纯 JavaScript SHA256 实现
function sha256Fallback(message) {
    function rightRotate(value, amount) {
        return (value >>> amount) | (value << (32 - amount));
    }

    const mathPow = Math.pow;
    const maxWord = mathPow(2, 32);
    let result = '';

    const words = [];
    const asciiBitLength = message.length * 8;

    let hash = [];
    const k = [];
    let primeCounter = 0;

    const isComposite = {};
    for (let candidate = 2; primeCounter < 64; candidate++) {
        if (!isComposite[candidate]) {
            for (let i = 0; i < 313; i += candidate) {
                isComposite[i] = candidate;
            }
            hash[primeCounter] = (mathPow(candidate, 0.5) * maxWord) | 0;
            k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
        }
    }

    message += '\x80';
    while ((message.length % 64) - 56) message += '\x00';

    for (let i = 0; i < message.length; i++) {
        const j = message.charCodeAt(i);
        if (j >> 8) return; // ASCII check
        words[i >> 2] |= j << (((3 - i) % 4) * 8);
    }
    words[words.length] = (asciiBitLength / maxWord) | 0;
    words[words.length] = asciiBitLength;

    for (let j = 0; j < words.length; ) {
        const w = words.slice(j, (j += 16));
        const oldHash = hash.slice(0);

        for (let i = 0; i < 64; i++) {
            const w15 = w[i - 15], w2 = w[i - 2];

            const a = oldHash[0], e = oldHash[4];
            const temp1 =
                oldHash[7] +
                (rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25)) +
                ((e & oldHash[5]) ^ (~e & oldHash[6])) +
                k[i] +
                (w[i] =
                    i < 16
                        ? w[i]
                        : (w[i - 16] +
                            (rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3)) +
                            w[i - 7] +
                            (rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10))) |
                          0);

            const temp2 =
                (rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22)) +
                ((a & oldHash[1]) ^ (a & oldHash[2]) ^ (oldHash[1] & oldHash[2]));

            oldHash.pop();
            oldHash.unshift((temp1 + temp2) | 0);
            oldHash[4] = (oldHash[4] + temp1) | 0;
        }

        for (let i = 0; i < 8; i++) {
            hash[i] = (hash[i] + oldHash[i]) | 0;
        }
    }

    for (let i = 0; i < 8; i++) {
        for (let j = 3; j + 1; j--) {
            const b = (hash[i] >> (j * 8)) & 255;
            result += (b < 16 ? '0' : '') + b.toString(16);
        }
    }

    return result;
}

// 格式化日期时间
function formatDateTime(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// 格式化相对时间
function formatRelativeTime(dateStr) {
    if (!dateStr) return '从未';
    
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now - date;
    
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    
    if (minutes < 1) return '刚刚';
    if (minutes < 60) return `${minutes}分钟前`;
    if (hours < 24) return `${hours}小时前`;
    if (days < 30) return `${days}天前`;
    
    return formatDateTime(dateStr);
}

// 显示Toast通知
function showToast(message, type = 'success') {
    // 兼容首页（toast-container）与聊天页（chat-toast）
    const container = document.getElementById('toast-container');
    const chatToast = document.getElementById('chat-toast');

    if (container) {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'toastIn 0.3s ease reverse';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
        return;
    }

    if (chatToast) {
        chatToast.textContent = message;
        chatToast.className = `chat-toast show ${type}`;
        setTimeout(() => chatToast.classList.remove('show'), 3000);
        return;
    }

    // 最后兜底：容器不存在时退化到控制台
    console.log(message);
}

// 本地存储操作
const storage = {
    get(key) {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : null;
        } catch {
            return null;
        }
    },
    
    set(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            console.error('Storage error:', e);
        }
    },
    
    remove(key) {
        localStorage.removeItem(key);
    }
};

// 获取认证Token
function getToken() {
    return storage.get('token');
}

// 设置认证Token
function setToken(token) {
    storage.set('token', token);
}

// 清除认证信息
function clearAuth() {
    storage.remove('token');
    storage.remove('user');
}

// 获取当前用户
function getCurrentUser() {
    return storage.get('user');
}

// 设置当前用户
function setCurrentUser(user) {
    storage.set('user', user);
}

// 检查是否登录
function isLoggedIn() {
    return !!getToken();
}

// HTML 转义，避免把不可信内容直接渲染进 innerHTML
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

