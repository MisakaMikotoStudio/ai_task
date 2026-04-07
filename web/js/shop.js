/**
 * 商城页面逻辑
 * 依赖：api.js（commercialAPI, shopAdminAPI）、utils.js（getToken, generateUUID）
 */

(function () {
    'use strict';

    // ─── 状态 ────────────────────────────────────────────────────────
    let _adminToken = '';

    // ─── 初始化 ──────────────────────────────────────────────────────
    async function initShop() {
        await initAPIConfig();

        document.getElementById('shop-content').style.display = 'block';

        bindAuthUI();
        await loadProducts();
        initAdminPanel();
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
  <div class="product-desc">${product.desc || ''}</div>
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

    // ─── 管理面板 ─────────────────────────────────────────────────────
    function initAdminPanel() {
        const panel = document.getElementById('admin-panel');
        if (!panel) return;

        const toggleBtn = document.getElementById('admin-toggle-btn');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', function () {
                panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
            });
        }

        // 设置 admin token
        const tokenInput = document.getElementById('admin-token-input');
        const tokenSaveBtn = document.getElementById('admin-token-save-btn');
        if (tokenSaveBtn) {
            tokenSaveBtn.addEventListener('click', function () {
                _adminToken = (tokenInput.value || '').trim();
                showToast('Admin Token 已保存', 'success');
            });
        }

        // 新增商品表单
        const createForm = document.getElementById('create-product-form');
        if (createForm) {
            createForm.addEventListener('submit', handleCreateProduct);
        }

        // 图片上传
        const iconUploadBtn = document.getElementById('icon-upload-btn');
        if (iconUploadBtn) {
            iconUploadBtn.addEventListener('click', handleIconUpload);
        }

        // 加载订单
        const loadOrdersBtn = document.getElementById('load-orders-btn');
        if (loadOrdersBtn) {
            loadOrdersBtn.addEventListener('click', () => loadOrders(1));
        }
    }

    async function handleCreateProduct(e) {
        e.preventDefault();
        if (!_adminToken) { showToast('请先填写 Admin Token', 'warn'); return; }

        const form = e.target;
        const productData = {
            key: form.querySelector('[name=key]').value.trim(),
            title: form.querySelector('[name=title]').value.trim(),
            desc: form.querySelector('[name=desc]').value,
            price: parseFloat(form.querySelector('[name=price]').value),
            expire_time: form.querySelector('[name=expire_time]').value
                ? parseInt(form.querySelector('[name=expire_time]').value, 10)
                : null,
            support_continue: form.querySelector('[name=support_continue]').checked,
            icon: form.querySelector('[name=icon]').value.trim() || null,
        };

        try {
            await shopAdminAPI.createProduct(_adminToken, productData);
            showToast('商品创建成功', 'success');
            form.reset();
            await loadProducts();
        } catch (err) {
            showToast(`创建失败：${err.message}`, 'error');
        }
    }

    async function handleIconUpload() {
        if (!_adminToken) { showToast('请先填写 Admin Token', 'warn'); return; }
        const fileInput = document.getElementById('icon-file-input');
        if (!fileInput || !fileInput.files[0]) {
            showToast('请先选择图片文件', 'warn');
            return;
        }
        try {
            const resp = await shopAdminAPI.uploadIcon(_adminToken, fileInput.files[0]);
            const iconUrlInput = document.querySelector('#create-product-form [name=icon]');
            if (iconUrlInput) iconUrlInput.value = resp.data.url;
            showToast('上传成功', 'success');
        } catch (err) {
            showToast(`上传失败：${err.message}`, 'error');
        }
    }

    async function loadOrders(page) {
        if (!_adminToken) { showToast('请先填写 Admin Token', 'warn'); return; }

        const userIdFilter = (document.getElementById('order-user-filter') || {}).value || null;
        const statusFilter = (document.getElementById('order-status-filter') || {}).value || null;

        try {
            const resp = await shopAdminAPI.getOrders(_adminToken, {
                page,
                page_size: 20,
                user_id: userIdFilter || undefined,
                status: statusFilter || undefined,
            });
            renderOrdersTable(resp.data);
        } catch (err) {
            showToast(`加载订单失败：${err.message}`, 'error');
        }
    }

    function renderOrdersTable(data) {
        const tbody = document.querySelector('#orders-table tbody');
        if (!tbody) return;

        const items = data.items || [];
        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center">暂无记录</td></tr>';
        } else {
            tbody.innerHTML = items.map(o => `
<tr>
  <td>${o.id}</td>
  <td>${o.user_id}</td>
  <td>${escapeHtml(o.product_key)}</td>
  <td>¥${o.amount.toFixed(2)}</td>
  <td>${escapeHtml(o.order_type)}</td>
  <td><span class="status-badge status-${o.status}">${o.status}</span></td>
  <td>${o.trade_no || '-'}</td>
  <td>${formatDate(o.created_at)}</td>
</tr>`).join('');
        }

        // 分页信息
        const pageInfo = document.getElementById('orders-page-info');
        if (pageInfo) {
            pageInfo.textContent = `共 ${data.total} 条，第 ${data.page} 页`;
        }

        // 翻页按钮
        const prevBtn = document.getElementById('orders-prev-btn');
        const nextBtn = document.getElementById('orders-next-btn');
        if (prevBtn) {
            prevBtn.disabled = data.page <= 1;
            prevBtn.onclick = () => loadOrders(data.page - 1);
        }
        if (nextBtn) {
            nextBtn.disabled = data.page * data.page_size >= data.total;
            nextBtn.onclick = () => loadOrders(data.page + 1);
        }
    }

    // ─── 工具函数 ─────────────────────────────────────────────────────
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
