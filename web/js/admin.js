/**
 * 管理后台（商品/订单）页面逻辑
 * 认证：复用统一登录 Token，后端按 admin 身份校验。
 */
(function () {
    'use strict';

    let currentOrderPage = 1;

    async function init() {
        await initAPIConfig();
        await ensureAdmin();
        bindTabEvents();
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

    function bindBusinessEvents() {
        const createForm = document.getElementById('create-product-form');
        if (createForm) createForm.addEventListener('submit', handleCreateProduct);

        const loadOrdersBtn = document.getElementById('load-orders-btn');
        if (loadOrdersBtn) loadOrdersBtn.addEventListener('click', () => loadOrders(1));
    }

    async function loadProducts() {
        const tbody = document.querySelector('#products-table tbody');
        if (!tbody) return;
        try {
            const resp = await adminCommerceAPI.getAdminProducts();
            const items = resp.data || [];
            if (!items.length) {
                tbody.innerHTML = '<tr><td colspan="9" class="empty-cell">暂无商品</td></tr>';
                return;
            }
            tbody.innerHTML = items.map((p) => {
                const offline = p.offline;
                const statusCell = offline
                    ? '<span class="status-badge status-offline">已下架</span>'
                    : '<span class="status-badge status-online">上架中</span>';
                const actionCell = offline
                    ? '<span style="color:var(--text-muted)">—</span>'
                    : `<button type="button" class="btn-danger btn-sm btn-offline-product" data-id="${p.id}">下架</button>`;
                return `<tr>
  <td>${p.id}</td>
  <td><code style="font-size:12px;background:var(--bg-tertiary,#f3f4f6);padding:2px 6px;border-radius:4px;">${escapeHtml(p.key)}</code></td>
  <td>${escapeHtml(p.title)}</td>
  <td class="amount">¥${Number(p.price || 0).toFixed(2)}</td>
  <td>${p.expire_time ? `${Math.round(p.expire_time / 86400)} 天` : '永久'}</td>
  <td>${p.support_continue ? '是' : '否'}</td>
  <td style="color:var(--text-secondary,#6b7280)">${formatDate(p.created_at)}</td>
  <td>${statusCell}</td>
  <td>${actionCell}</td>
</tr>`;
            }).join('');

            tbody.querySelectorAll('.btn-offline-product').forEach((btn) => {
                btn.addEventListener('click', async () => {
                    const productId = Number(btn.dataset.id);
                    if (!productId) return;
                    if (!confirm('确认下架该商品？前台将不再展示。')) return;
                    try {
                        await adminCommerceAPI.offlineProduct(productId);
                        await loadProducts();
                        alert('已下架');
                    } catch (err) {
                        alert(`下架失败：${err.message}`);
                    }
                });
            });
        } catch (err) {
            alert(`加载商品失败：${err.message}`);
        }
    }

    async function handleCreateProduct(e) {
        e.preventDefault();
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
            icon: form.querySelector('[name=icon]').value.trim() || null
        };
        try {
            await adminCommerceAPI.createProduct(productData);
            form.reset();
            await loadProducts();
            alert('商品创建成功');
        } catch (err) {
            alert(`创建失败：${err.message}`);
        }
    }

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
            alert(`加载订单失败：${err.message}`);
        }
    }

    const STATUS_MAP = {
        pending: '待支付',
        paid: '已支付',
        failed: '失败',
        refunded: '已退款'
    };

    function renderOrdersTable(data) {
        const tbody = document.querySelector('#orders-table tbody');
        if (!tbody) return;
        const items = data.items || [];
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="empty-cell">暂无订单</td></tr>';
        } else {
            tbody.innerHTML = items.map((o) => {
                const canRefund = o.status === 'paid';
                const statusLabel = STATUS_MAP[o.status] || escapeHtml(o.status);
                const statusClass = `status-${escapeHtml(o.status)}`;
                return `<tr>
  <td>${o.id}</td>
  <td>${o.user_id}</td>
  <td><code style="font-size:12px;background:var(--bg-tertiary,#f3f4f6);padding:2px 6px;border-radius:4px;">${escapeHtml(o.product_key)}</code></td>
  <td class="amount">¥${Number(o.amount || 0).toFixed(2)}</td>
  <td><span class="status-badge ${statusClass}">${statusLabel}</span></td>
  <td style="color:var(--text-secondary,#6b7280);font-size:12px;">${escapeHtml(o.trade_no || '-')}</td>
  <td style="color:var(--text-secondary,#6b7280)">${formatDate(o.created_at)}</td>
  <td>${canRefund ? `<button class="btn-danger btn-sm refund-btn" data-id="${o.id}">退款</button>` : '<span style="color:var(--text-muted,#6e7681)">—</span>'}</td>
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
                    alert(`退款失败：${err.message}`);
                }
            });
        });
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

    document.addEventListener('DOMContentLoaded', init);
})();
