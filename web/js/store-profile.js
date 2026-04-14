// store-profile.js - 商店与个人中心
// ===== 商店 =====

async function loadStoreProducts() {
    const grid = document.getElementById('store-products-grid');
    if (!grid) return;

    grid.innerHTML = '<p class="store-loading-tip">加载中…</p>';
    try {
        const resp = await commercialAPI.getProducts();
        const products = resp.data || [];
        if (products.length === 0) {
            grid.innerHTML = '<p class="store-empty-tip">暂无商品</p>';
            return;
        }
        grid.innerHTML = products.map(renderStoreProductCard).join('');
        grid.querySelectorAll('.store-buy-btn').forEach((btn) => {
            btn.addEventListener('click', handleStoreBuy);
        });
    } catch (err) {
        grid.innerHTML = `<p class="store-error-tip">加载失败：${escapeHtml(err.message)}</p>`;
    }
}

function renderStoreProductCard(product) {
    const iconHtml = product.icon
        ? `<img src="${escapeHtml(product.icon)}" alt="${escapeHtml(product.title)}" class="store-product-icon">`
        : `<div class="store-product-icon-placeholder">🛍️</div>`;
    const expireText = product.expire_time ? `有效期 ${Math.round(product.expire_time / 86400)} 天` : '永久有效';
    const renewBtn = product.support_continue
        ? `<button class="store-buy-btn btn-primary" data-id="${product.id}" data-type="renew">续费</button>`
        : '';
    return `
<div class="store-product-card">
  <div class="store-product-header">${iconHtml}</div>
  <div class="store-product-body">
    <div class="store-product-title">${escapeHtml(product.title)}</div>
    <div class="store-product-desc">${escapeHtml(product.desc || '')}</div>
    <div class="store-product-meta">
      <span class="store-product-price">¥${product.price.toFixed(2)}</span>
      <span class="store-product-expire">${escapeHtml(expireText)}</span>
    </div>
  </div>
  <div class="store-product-actions">
    <button class="store-buy-btn btn-primary" data-id="${product.id}" data-type="purchase">购买</button>
    ${renewBtn}
  </div>
</div>`;
}

async function handleStoreBuy(e) {
    const btn = e.currentTarget;
    const productId = parseInt(btn.dataset.id, 10);
    const orderType = btn.dataset.type || 'purchase';

    btn.disabled = true;
    const originalText = btn.textContent;
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
        btn.textContent = originalText;
    }
}

// ===== 我的（个人中心）=====

let profileOrderPage = 1;

function loadProfileUserInfo() {
    const user = getCurrentUser();
    const nameEl = document.getElementById('profile-username');
    const idEl = document.getElementById('profile-user-id');
    if (nameEl) nameEl.textContent = (user && user.name) ? user.name : '用户';
    if (idEl) idEl.textContent = (user && user.user_id != null) ? String(user.user_id) : '—';
}

function initProfile() {
    // 初始化分页按钮
    const prevBtn = document.getElementById('profile-orders-prev-btn');
    const nextBtn = document.getElementById('profile-orders-next-btn');
    if (prevBtn && prevBtn.dataset.bound !== 'true') {
        prevBtn.addEventListener('click', () => loadMyOrders(profileOrderPage - 1));
        prevBtn.dataset.bound = 'true';
    }
    if (nextBtn && nextBtn.dataset.bound !== 'true') {
        nextBtn.addEventListener('click', () => loadMyOrders(profileOrderPage + 1));
        nextBtn.dataset.bound = 'true';
    }
}

async function loadMyServices() {
    const container = document.getElementById('profile-services-list');
    const emptyEl = document.getElementById('profile-services-empty');
    if (!container) return;

    container.innerHTML = '<p class="store-loading-tip">加载中…</p>';
    if (emptyEl) emptyEl.style.display = 'none';

    try {
        const resp = await commercialAPI.getMyServices();
        const services = resp.data || [];
        if (services.length === 0) {
            container.innerHTML = '';
            if (emptyEl) emptyEl.style.display = '';
            return;
        }
        container.innerHTML = services.map(renderServiceCard).join('');
    } catch (err) {
        container.innerHTML = `<p class="store-error-tip">加载失败：${escapeHtml(err.message)}</p>`;
    }
}

function renderServiceCard(service) {
    const iconHtml = service.product_icon
        ? `<img src="${escapeHtml(service.product_icon)}" alt="" class="service-card-icon">`
        : `<div class="service-card-icon-placeholder">✨</div>`;

    let expireHtml;
    if (service.is_permanent) {
        expireHtml = `<span class="service-expire-badge service-expire-permanent">永久有效</span>`;
    } else {
        const expireDate = new Date(service.expire_at);
        const now = new Date();
        const diffDays = Math.ceil((expireDate - now) / 86400000);
        const expireDateStr = expireDate.toLocaleDateString('zh-CN');
        const urgentClass = diffDays <= 7 ? 'service-expire-urgent' : 'service-expire-normal';
        expireHtml = `<span class="service-expire-badge ${urgentClass}">到期：${expireDateStr}（剩 ${diffDays} 天）</span>`;
    }

    return `
<div class="service-card">
  <div class="service-card-left">${iconHtml}</div>
  <div class="service-card-body">
    <div class="service-card-title">${escapeHtml(service.product_title)}</div>
    <div class="service-card-expire">${expireHtml}</div>
  </div>
</div>`;
}

async function loadMyOrders(page) {
    profileOrderPage = Math.max(1, page || 1);
    const tbody = document.getElementById('profile-orders-tbody');
    const emptyEl = document.getElementById('profile-orders-empty');
    const footer = document.getElementById('profile-orders-footer');
    if (!tbody) return;

    try {
        const resp = await commercialAPI.getMyOrders(profileOrderPage, 20);
        const data = resp.data || {};
        const orders = data.orders || [];
        const total = data.total || 0;
        const totalPages = Math.ceil(total / (data.page_size || 20));

        if (orders.length === 0 && profileOrderPage === 1) {
            tbody.innerHTML = '';
            if (emptyEl) emptyEl.style.display = '';
            if (footer) footer.style.display = 'none';
            return;
        }

        if (emptyEl) emptyEl.style.display = 'none';
        if (footer) footer.style.display = '';

        tbody.innerHTML = orders.map((order) => {
            const statusMap = { pending: '待支付', paid: '已支付', failed: '失败', refunded: '已退款' };
            const typeMap = { purchase: '购买', renew: '续费' };
            const expireText = order.expire_at ? new Date(order.expire_at).toLocaleDateString('zh-CN') : (order.status === 'paid' ? '永久' : '-');
            const statusClass = order.status === 'paid' ? 'status-paid'
                : order.status === 'refunded' ? 'status-refunded'
                : order.status === 'failed' ? 'status-failed'
                : 'status-pending';
            return `<tr>
  <td class="text-muted" style="font-size:12px;">${escapeHtml(order.out_trade_no || String(order.id))}</td>
  <td>${escapeHtml(order.product_title || order.product_key)}</td>
  <td>¥${Number(order.amount).toFixed(2)}</td>
  <td>${typeMap[order.order_type] || order.order_type}</td>
  <td><span class="order-status-badge ${statusClass}">${statusMap[order.status] || order.status}</span></td>
  <td>${expireText}</td>
  <td>${order.created_at ? new Date(order.created_at).toLocaleDateString('zh-CN') : '-'}</td>
</tr>`;
        }).join('');

        // 更新分页信息
        const totalInfoEl = document.getElementById('profile-orders-total-info');
        const pageInfoEl = document.getElementById('profile-orders-page-info');
        const prevBtn = document.getElementById('profile-orders-prev-btn');
        const nextBtn = document.getElementById('profile-orders-next-btn');

        if (totalInfoEl) totalInfoEl.textContent = `共 ${total} 条`;
        if (pageInfoEl) pageInfoEl.textContent = `第 ${profileOrderPage} / ${totalPages} 页`;
        if (prevBtn) prevBtn.disabled = profileOrderPage <= 1;
        if (nextBtn) nextBtn.disabled = profileOrderPage >= totalPages;
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--accent-danger);">加载失败：${escapeHtml(err.message)}</td></tr>`;
    }
}

// ===== 工具函数 =====

