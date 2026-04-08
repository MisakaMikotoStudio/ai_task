/**
 * 管理后台（商品/订单）页面逻辑
 * 认证：复用统一登录 Token，后端按 admin 身份校验。
 */
(function () {
    'use strict';

    let currentOrderPage = 1;
    let quillEditor = null;

    async function init() {
        await initAPIConfig();
        await ensureAdmin();
        bindTabEvents();
        bindModalEvents();
        bindBusinessEvents();
        await loadProducts();
        await loadOrders(1);
    }

    async function ensureAdmin() {
        if (!isLoggedIn()) {
            window.location.href = '/index.html';
            return;
        }
        try {
            const resp = await userAPI.me();
            const user = resp && resp.data;
            if (!user || user.name !== 'admin') {
                window.location.href = '/index.html';
            }
        } catch (e) {
            clearAuth();
            window.location.href = '/index.html';
        }
    }

    function bindTabEvents() {
        const tabButtons = document.querySelectorAll('.tab-btn');
        tabButtons.forEach((btn) => {
            btn.addEventListener('click', () => {
                tabButtons.forEach((b) => b.classList.remove('active'));
                btn.classList.add('active');
                const tab = btn.dataset.tab;
                document.getElementById('tab-products').classList.toggle('hide', tab !== 'products');
                document.getElementById('tab-orders').classList.toggle('hide', tab !== 'orders');
            });
        });
    }

    // ========== Modal 弹窗逻辑 ==========

    function bindModalEvents() {
        const modal = document.getElementById('create-product-modal');
        const openBtn = document.getElementById('open-create-product-btn');
        const closeBtn = document.getElementById('close-create-modal');
        const cancelBtn = document.getElementById('cancel-create-modal');

        if (openBtn) {
            openBtn.addEventListener('click', () => openCreateModal());
        }
        if (closeBtn) {
            closeBtn.addEventListener('click', () => closeCreateModal());
        }
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => closeCreateModal());
        }
        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closeCreateModal();
            });
        }
    }

    function openCreateModal() {
        const modal = document.getElementById('create-product-modal');
        if (!modal) return;
        modal.classList.add('show');
        initQuillEditor();
    }

    function closeCreateModal() {
        const modal = document.getElementById('create-product-modal');
        if (!modal) return;
        modal.classList.remove('show');
        resetCreateForm();
    }

    function resetCreateForm() {
        const form = document.getElementById('create-product-form');
        if (form) form.reset();
        if (quillEditor) {
            quillEditor.setContents([]);
        }
    }

    function initQuillEditor() {
        if (quillEditor) return;
        const container = document.getElementById('product-desc-editor');
        if (!container) return;
        quillEditor = new Quill(container, {
            theme: 'snow',
            placeholder: '输入商品描述...',
            modules: {
                toolbar: [
                    [{ 'header': [1, 2, 3, false] }],
                    ['bold', 'italic', 'underline', 'strike'],
                    [{ 'color': [] }, { 'background': [] }],
                    [{ 'list': 'ordered' }, { 'list': 'bullet' }],
                    ['blockquote', 'link'],
                    ['clean']
                ]
            }
        });
    }

    function getQuillHtml() {
        if (!quillEditor) return '';
        const html = quillEditor.root.innerHTML;
        if (html === '<p><br></p>' || html === '<p></p>') return '';
        return html;
    }

    // ========== 商品逻辑 ==========

    function bindBusinessEvents() {
        const createForm = document.getElementById('create-product-form');
        if (createForm) createForm.addEventListener('submit', handleCreateProduct);

        const loadOrdersBtn = document.getElementById('load-orders-btn');
        if (loadOrdersBtn) loadOrdersBtn.addEventListener('click', () => loadOrders(1));
    }

    async function loadProducts() {
        const grid = document.getElementById('products-grid');
        if (!grid) return;
        try {
            const resp = await adminCommerceAPI.getAdminProducts();
            const items = resp.data || [];
            if (!items.length) {
                grid.innerHTML = `
                    <div class="empty-state" style="grid-column:1/-1;">
                        <div class="empty-state-icon">&#128230;</div>
                        <p class="empty-state-text">暂无商品，点击上方按钮添加</p>
                    </div>`;
                return;
            }
            grid.innerHTML = items.map((p) => renderProductCard(p)).join('');

            grid.querySelectorAll('.btn-offline-product').forEach((btn) => {
                btn.addEventListener('click', async () => {
                    const productId = Number(btn.dataset.id);
                    if (!productId) return;
                    if (!confirm('确认下架该商品？前台将不再展示。')) return;
                    try {
                        await adminCommerceAPI.offlineProduct(productId);
                        await loadProducts();
                    } catch (err) {
                        alert('下架失败：' + err.message);
                    }
                });
            });
        } catch (err) {
            grid.innerHTML = `
                <div class="empty-state" style="grid-column:1/-1;">
                    <div class="empty-state-icon">&#9888;</div>
                    <p class="empty-state-text">加载失败：${escapeHtml(err.message)}</p>
                </div>`;
        }
    }

    function renderProductCard(product) {
        const isOffline = !!product.offline;
        const offlineClass = isOffline ? ' offline' : '';

        const iconHtml = product.icon
            ? `<img class="product-card-icon" src="${escapeHtml(product.icon)}" alt="${escapeHtml(product.title)}">`
            : `<div class="product-card-icon-placeholder">&#128230;</div>`;

        const statusBadge = isOffline
            ? '<span class="product-card-badge badge-danger">已下架</span>'
            : '<span class="product-card-badge badge-success">上架中</span>';

        const expireBadge = product.expire_time
            ? `<span class="product-card-badge badge-info">${Math.round(product.expire_time / 86400)} 天</span>`
            : '<span class="product-card-badge badge-muted">永久</span>';

        const renewBadge = product.support_continue
            ? '<span class="product-card-badge badge-info">可续费</span>'
            : '';

        const actionBtn = isOffline
            ? ''
            : `<button class="btn btn-danger btn-sm btn-offline-product" data-id="${product.id}">下架</button>`;

        const descText = stripHtml(product.desc || '');
        const descDisplay = descText
            ? `<p class="product-card-desc">${escapeHtml(descText)}</p>`
            : '';

        return `
        <div class="product-card${offlineClass}">
            ${iconHtml}
            <div class="product-card-body">
                <div class="product-card-title">${escapeHtml(product.title)}</div>
                <span class="product-card-key">${escapeHtml(product.key)}</span>
                ${descDisplay}
                <div class="product-card-meta">
                    <span class="product-card-price">${Number(product.price || 0).toFixed(2)}</span>
                    ${statusBadge}
                    ${expireBadge}
                    ${renewBadge}
                </div>
            </div>
            <div class="product-card-footer">
                <span class="product-card-date">ID: ${product.id} | ${formatDate(product.created_at)}</span>
                ${actionBtn}
            </div>
        </div>`;
    }

    function stripHtml(html) {
        const tmp = document.createElement('div');
        tmp.innerHTML = html;
        return tmp.textContent || tmp.innerText || '';
    }

    async function handleCreateProduct(e) {
        e.preventDefault();
        const form = e.target;
        const productData = {
            key: form.querySelector('[name=key]').value.trim(),
            title: form.querySelector('[name=title]').value.trim(),
            desc: getQuillHtml(),
            price: parseFloat(form.querySelector('[name=price]').value),
            expire_time: form.querySelector('[name=expire_time]').value
                ? parseInt(form.querySelector('[name=expire_time]').value, 10)
                : null,
            support_continue: form.querySelector('[name=support_continue]').checked,
            icon: form.querySelector('[name=icon]').value.trim() || null
        };
        try {
            await adminCommerceAPI.createProduct(productData);
            closeCreateModal();
            await loadProducts();
            alert('商品创建成功');
        } catch (err) {
            alert('创建失败：' + err.message);
        }
    }

    // ========== 订单逻辑 ==========

    async function loadOrders(page) {
        currentOrderPage = page;
        const userIdFilter = (document.getElementById('order-user-filter') || {}).value || null;
        const statusFilter = (document.getElementById('order-status-filter') || {}).value || null;
        try {
            const resp = await adminCommerceAPI.getOrders({
                page,
                page_size: 20,
                user_id: userIdFilter || undefined,
                status: statusFilter || undefined
            });
            renderOrdersTable(resp.data || {});
        } catch (err) {
            alert('加载订单失败：' + err.message);
        }
    }

    function renderOrdersTable(data) {
        const tbody = document.querySelector('#orders-table tbody');
        if (!tbody) return;
        const items = data.items || [];
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-secondary);padding:40px;">暂无订单</td></tr>';
        } else {
            tbody.innerHTML = items.map((o) => {
                const canRefund = o.status === 'paid';
                return `<tr>
  <td>${o.id}</td>
  <td>${o.user_id}</td>
  <td><code>${escapeHtml(o.product_key)}</code></td>
  <td style="font-weight:600;">¥${Number(o.amount || 0).toFixed(2)}</td>
  <td>${escapeHtml(o.status)}</td>
  <td>${escapeHtml(o.trade_no || '-')}</td>
  <td>${formatDate(o.created_at)}</td>
  <td>${canRefund ? `<button class="btn btn-danger btn-sm refund-btn" data-id="${o.id}">退款</button>` : '-'}</td>
</tr>`;
            }).join('');
        }

        const pageInfo = document.getElementById('orders-page-info');
        if (pageInfo) pageInfo.textContent = `共 ${data.total || 0} 条，第 ${data.page || 1} 页`;

        const prevBtn = document.getElementById('orders-prev-btn');
        const nextBtn = document.getElementById('orders-next-btn');
        if (prevBtn) {
            prevBtn.disabled = (data.page || 1) <= 1;
            prevBtn.onclick = () => loadOrders((data.page || 1) - 1);
        }
        if (nextBtn) {
            nextBtn.disabled = (data.page || 1) * (data.page_size || 20) >= (data.total || 0);
            nextBtn.onclick = () => loadOrders((data.page || 1) + 1);
        }

        tbody.querySelectorAll('.refund-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const orderId = Number(btn.dataset.id);
                if (!orderId) return;
                if (!confirm(`确认退款订单 #${orderId}？`)) return;
                try {
                    await adminCommerceAPI.refundOrder(orderId);
                    await loadOrders(currentOrderPage);
                    alert('退款成功');
                } catch (err) {
                    alert('退款失败：' + err.message);
                }
            });
        });
    }

    // ========== 工具函数 ==========

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

    document.addEventListener('DOMContentLoaded', init);
})();
