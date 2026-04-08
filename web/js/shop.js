/**
 * 商城页面逻辑
 * 依赖：api.js（commercialAPI）、utils.js（getToken）
 */

(function () {
    'use strict';

    // ─── 初始化 ──────────────────────────────────────────────────────
    async function initShop() {
        await initAPIConfig();

        document.getElementById('shop-content').style.display = 'block';

        bindAuthUI();
        await loadProducts();
    }

    // ─── 登录态 UI ───────────────────────────────────────────────────
    function bindAuthUI() {
        const token = getToken();
        const navUser = document.getElementById('shop-nav-user');
        const navLogin = document.getElementById('shop-nav-login');
        if (token) {
            if (navUser) navUser.style.display = 'inline-block';
            if (navLogin) navLogin.style.display = 'none';
        } else {
            if (navUser) navUser.style.display = 'none';
            if (navLogin) navLogin.style.display = 'inline-block';
        }
        const logoutBtn = document.getElementById('shop-logout-btn');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', function () {
                clearAuth();
                window.location.reload();
            });
        }
    }

    // ─── 商品列表 ─────────────────────────────────────────────────────
    async function loadProducts() {
        const container = document.getElementById('products-grid');
        if (!container) return;

        container.innerHTML = '<p class="loading-tip">加载中…</p>';
        try {
            const resp = await commercialAPI.getProducts();
            const products = resp.data || [];
            if (products.length === 0) {
                container.innerHTML = '<p class="empty-tip">暂无商品</p>';
                return;
            }
            container.innerHTML = products.map(renderProductCard).join('');
            container.querySelectorAll('.buy-btn').forEach(btn => {
                btn.addEventListener('click', handleBuy);
            });
            container.querySelectorAll('.renew-btn').forEach(btn => {
                btn.addEventListener('click', handleBuy);
            });
        } catch (e) {
            container.innerHTML = `<p class="error-tip">加载商品失败：${e.message}</p>`;
        }
    }

    function renderProductCard(product) {
        const iconHtml = product.icon
            ? `<img src="${escapeHtml(product.icon)}" alt="${escapeHtml(product.title)}" class="product-icon">`
            : '<div class="product-icon-placeholder"></div>';
        const expireText = product.expire_time
            ? `有效期 ${Math.round(product.expire_time / 86400)} 天`
            : '永久有效';
        const renewBtn = product.support_continue
            ? `<button class="renew-btn" data-id="${product.id}" data-type="renew">续费</button>`
            : '';
        return `
<div class="product-card">
  ${iconHtml}
  <h3 class="product-title">${escapeHtml(product.title)}</h3>
  <div class="product-desc">${formatShopDesc(product.desc)}</div>
  <div class="product-meta">
    <span class="product-price">¥${product.price.toFixed(2)}</span>
    <span class="product-expire">${expireText}</span>
  </div>
  <div class="product-actions">
    <button class="buy-btn btn-primary" data-id="${product.id}" data-type="purchase">购买</button>
    ${renewBtn}
  </div>
</div>`;
    }

    async function handleBuy(e) {
        const btn = e.currentTarget;
        const productId = parseInt(btn.dataset.id, 10);
        const orderType = btn.dataset.type || 'purchase';

        if (!getToken()) {
            showToast('请先登录后再购买', 'warn');
            return;
        }

        btn.disabled = true;
        btn.textContent = '跳转中…';
        try {
            const isMobile = /mobile|android|iphone|ipad/i.test(navigator.userAgent);
            const resp = await commercialAPI.buy(productId, orderType, isMobile ? 'mobile' : 'pc');
            const payUrl = resp.data && resp.data.pay_url;
            if (payUrl) {
                window.location.href = payUrl;
            } else {
                throw new Error('未获取到支付链接');
            }
        } catch (err) {
            showToast(`购买失败：${err.message}`, 'error');
            btn.disabled = false;
            btn.textContent = orderType === 'renew' ? '续费' : '购买';
        }
    }

    // ─── 工具函数 ─────────────────────────────────────────────────────
    function formatShopDesc(text) {
        if (!text) return '';
        return escapeHtml(text).replace(/\n/g, '<br>');
    }

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function formatDate(iso) {
        if (!iso) return '-';
        return iso.replace('T', ' ').replace('Z', '');
    }

    function showToast(msg, type) {
        const toast = document.createElement('div');
        toast.className = `toast toast-${type || 'info'}`;
        toast.textContent = msg;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    // ─── 启动 ────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', initShop);
})();
