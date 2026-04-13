// admin.js - Admin 商品/订单、权限配置、资源管理
// ===== Admin 商品/订单（/admin 下嵌入 index.html，样式见 style.css .commerce-*）=====
let adminOrderPage = 1;

const ORDER_STATUS_LABELS = {
    pending: '待支付',
    paid: '已支付',
    failed: '失败',
    refunded: '已退款'
};

function commerceOrderStatusKey(status) {
    const allowed = ['pending', 'paid', 'failed', 'refunded'];
    return allowed.includes(status) ? status : 'unknown';
}

function initAdminCommerce() {
    const openBtn = document.getElementById('open-create-product-btn');
    if (openBtn && openBtn.dataset.bound !== 'true') {
        openBtn.addEventListener('click', showAdminCreateProductModal);
        openBtn.dataset.bound = 'true';
    }

    const loadOrdersBtn = document.getElementById('load-orders-btn');
    if (loadOrdersBtn && loadOrdersBtn.dataset.bound !== 'true') {
        loadOrdersBtn.addEventListener('click', () => loadAdminOrders(1));
        loadOrdersBtn.dataset.bound = 'true';
    }
}

const COMMERCE_MAX_EXPIRE_SECONDS = 1e8;
const COMMERCE_MAX_DESC_LEN = 10000;

function plainPreviewFromDesc(text, maxLen) {
    const flat = String(text || '').replace(/\r\n/g, '\n').replace(/\n/g, ' ').replace(/\s+/g, ' ').trim();
    if (!flat) return '';
    if (flat.length > maxLen) return `${flat.slice(0, maxLen)}…`;
    return flat;
}

function parseAdminProductPriceInput(raw) {
    const s = String(raw ?? '').trim();
    if (!s) return { ok: false, message: '请填写价格' };
    if (!/^\d+(\.\d{1,2})?$/.test(s)) {
        return { ok: false, message: '价格须为数字，最多两位小数' };
    }
    const n = parseFloat(s);
    if (!(n > 0)) return { ok: false, message: '价格必须大于 0' };
    return { ok: true, value: Math.round(n * 100) / 100 };
}

function renderAdminProductCard(p) {
    const offline = p.offline;
    const statusHtml = offline
        ? '<span class="commerce-badge commerce-product-offline">已下架</span>'
        : '<span class="commerce-badge commerce-product-online">上架中</span>';
    const actionHtml = offline
        ? `<button type="button" class="commerce-online-btn btn-online-product" data-id="${p.id}">上架</button>`
        : `<button type="button" class="commerce-offline-btn btn-offline-product" data-id="${p.id}">下架</button>`;
    const validity = p.expire_time ? `${Math.round(p.expire_time / 86400)} 天` : '永久';
    const renew = p.support_continue ? '支持续费' : '不支持续费';
    const preview = plainPreviewFromDesc(p.desc, 140);
    const previewBlock = preview
        ? `<p class="commerce-product-card-desc">${escapeHtml(preview)}</p>`
        : '';
    const media = p.icon
        ? `<div class="commerce-product-card-media"><img src="${escapeHtml(p.icon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'"></div>`
        : '<div class="commerce-product-card-media commerce-product-card-media-placeholder" aria-hidden="true">📦</div>';
    return `<article class="commerce-product-card${offline ? ' is-offline' : ''}">
  ${media}
  <div class="commerce-product-card-body">
    <div class="commerce-product-card-top">
      <h4 class="commerce-product-card-name">${escapeHtml(p.title)}</h4>
      ${statusHtml}
    </div>
    <div class="commerce-product-card-key"><code class="commerce-code">${escapeHtml(p.key)}</code><span class="commerce-product-card-id">#${p.id}</span></div>
    <div class="commerce-product-card-price commerce-amount">¥${Number(p.price || 0).toFixed(2)}</div>
    ${previewBlock}
    <ul class="commerce-product-card-meta">
      <li><span>有效期</span><strong>${validity}</strong></li>
      <li><span>续费</span><strong>${renew}</strong></li>
      <li><span>创建</span><strong class="commerce-meta-time">${formatDateTime(p.created_at)}</strong></li>
    </ul>
    <div class="commerce-product-card-actions">${actionHtml}</div>
  </div>
</article>`;
}

/* ===== 图片裁剪工具 ===== */
const CROP_ASPECT = 3 / 2;
const CROP_OUTPUT_WIDTH = 600;
const CROP_OUTPUT_HEIGHT = Math.round(CROP_OUTPUT_WIDTH / CROP_ASPECT);
const CROP_JPEG_QUALITY = 0.85;

function openImageCropDialog(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error('读取文件失败'));
        reader.onload = () => {
            const img = new Image();
            img.onerror = () => reject(new Error('无法加载图片'));
            img.onload = () => _showCropUI(img, resolve, reject);
            img.src = reader.result;
        };
        reader.readAsDataURL(file);
    });
}

function _showCropUI(sourceImg, resolve, reject) {
    const overlay = document.createElement('div');
    overlay.className = 'image-crop-overlay';
    overlay.innerHTML = `
        <div class="image-crop-dialog">
            <div class="image-crop-header">
                <h4>裁剪封面图</h4>
                <button type="button" class="image-crop-close">&times;</button>
            </div>
            <div class="image-crop-body">
                <p class="image-crop-hint">拖拽图片调整位置，滑块控制缩放</p>
                <div class="image-crop-canvas-wrap" id="crop-viewport">
                    <img id="crop-source-img" alt="">
                </div>
                <div class="image-crop-zoom-row">
                    <span class="image-crop-zoom-label">缩放</span>
                    <input type="range" class="image-crop-zoom-slider" id="crop-zoom-slider"
                           min="100" max="300" value="100" step="1">
                </div>
            </div>
            <div class="image-crop-footer">
                <button type="button" class="btn-secondary" id="crop-cancel-btn">取消</button>
                <button type="button" class="btn-primary" id="crop-confirm-btn">确认裁剪</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const viewport = overlay.querySelector('#crop-viewport');
    const imgEl = overlay.querySelector('#crop-source-img');
    const zoomSlider = overlay.querySelector('#crop-zoom-slider');
    const cancelBtn = overlay.querySelector('#crop-cancel-btn');
    const closeBtn = overlay.querySelector('.image-crop-close');
    const confirmBtn = overlay.querySelector('#crop-confirm-btn');

    imgEl.src = sourceImg.src;

    let scale = 1;
    let imgX = 0;
    let imgY = 0;
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let dragStartImgX = 0;
    let dragStartImgY = 0;

    function getViewportRect() {
        return viewport.getBoundingClientRect();
    }

    function fitInitialScale() {
        const vr = getViewportRect();
        const vw = vr.width;
        const vh = vr.height;
        const iw = sourceImg.naturalWidth;
        const ih = sourceImg.naturalHeight;
        const scaleToFitW = vw / iw;
        const scaleToFitH = vh / ih;
        const minFit = Math.max(scaleToFitW, scaleToFitH);
        scale = minFit;
        zoomSlider.min = Math.round(minFit * 100);
        zoomSlider.max = Math.round(Math.max(minFit * 3, 3) * 100);
        zoomSlider.value = Math.round(scale * 100);
        imgX = (vw - iw * scale) / 2;
        imgY = (vh - ih * scale) / 2;
    }

    function clampPosition() {
        const vr = getViewportRect();
        const vw = vr.width;
        const vh = vr.height;
        const iw = sourceImg.naturalWidth * scale;
        const ih = sourceImg.naturalHeight * scale;
        if (iw <= vw) {
            imgX = (vw - iw) / 2;
        } else {
            imgX = Math.min(0, Math.max(vw - iw, imgX));
        }
        if (ih <= vh) {
            imgY = (vh - ih) / 2;
        } else {
            imgY = Math.min(0, Math.max(vh - ih, imgY));
        }
    }

    function render() {
        imgEl.style.width = (sourceImg.naturalWidth * scale) + 'px';
        imgEl.style.height = (sourceImg.naturalHeight * scale) + 'px';
        imgEl.style.left = imgX + 'px';
        imgEl.style.top = imgY + 'px';
    }

    function update() {
        clampPosition();
        render();
    }

    function onPointerDown(e) {
        if (e.button && e.button !== 0) return;
        isDragging = true;
        dragStartX = e.clientX ?? e.touches[0].clientX;
        dragStartY = e.clientY ?? e.touches[0].clientY;
        dragStartImgX = imgX;
        dragStartImgY = imgY;
        e.preventDefault();
    }

    function onPointerMove(e) {
        if (!isDragging) return;
        const cx = e.clientX ?? (e.touches && e.touches[0] ? e.touches[0].clientX : dragStartX);
        const cy = e.clientY ?? (e.touches && e.touches[0] ? e.touches[0].clientY : dragStartY);
        imgX = dragStartImgX + (cx - dragStartX);
        imgY = dragStartImgY + (cy - dragStartY);
        update();
    }

    function onPointerUp() {
        isDragging = false;
    }

    viewport.addEventListener('mousedown', onPointerDown);
    viewport.addEventListener('touchstart', onPointerDown, { passive: false });
    document.addEventListener('mousemove', onPointerMove);
    document.addEventListener('touchmove', onPointerMove, { passive: false });
    document.addEventListener('mouseup', onPointerUp);
    document.addEventListener('touchend', onPointerUp);

    viewport.addEventListener('wheel', (e) => {
        e.preventDefault();
        const vr = getViewportRect();
        const oldScale = scale;
        const delta = e.deltaY > 0 ? -0.03 : 0.03;
        const minScale = parseInt(zoomSlider.min) / 100;
        const maxScale = parseInt(zoomSlider.max) / 100;
        scale = Math.min(maxScale, Math.max(minScale, scale + delta));
        const pointerX = e.clientX - vr.left;
        const pointerY = e.clientY - vr.top;
        imgX = pointerX - (pointerX - imgX) * (scale / oldScale);
        imgY = pointerY - (pointerY - imgY) * (scale / oldScale);
        zoomSlider.value = Math.round(scale * 100);
        update();
    }, { passive: false });

    zoomSlider.addEventListener('input', () => {
        const vr = getViewportRect();
        const oldScale = scale;
        scale = parseInt(zoomSlider.value) / 100;
        const cx = vr.width / 2;
        const cy = vr.height / 2;
        imgX = cx - (cx - imgX) * (scale / oldScale);
        imgY = cy - (cy - imgY) * (scale / oldScale);
        update();
    });

    function cleanup() {
        document.removeEventListener('mousemove', onPointerMove);
        document.removeEventListener('touchmove', onPointerMove);
        document.removeEventListener('mouseup', onPointerUp);
        document.removeEventListener('touchend', onPointerUp);
        overlay.remove();
    }

    function cancel() {
        cleanup();
        reject(new Error('用户取消裁剪'));
    }

    cancelBtn.addEventListener('click', cancel);
    closeBtn.addEventListener('click', cancel);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) cancel();
    });

    confirmBtn.addEventListener('click', () => {
        const vr = getViewportRect();
        const vw = vr.width;
        const vh = vr.height;
        const srcX = -imgX / scale;
        const srcY = -imgY / scale;
        const srcW = vw / scale;
        const srcH = vh / scale;
        const canvas = document.createElement('canvas');
        canvas.width = CROP_OUTPUT_WIDTH;
        canvas.height = CROP_OUTPUT_HEIGHT;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(sourceImg, srcX, srcY, srcW, srcH, 0, 0, CROP_OUTPUT_WIDTH, CROP_OUTPUT_HEIGHT);
        canvas.toBlob((blob) => {
            cleanup();
            if (!blob) {
                reject(new Error('裁剪失败'));
                return;
            }
            resolve(blob);
        }, 'image/jpeg', CROP_JPEG_QUALITY);
    });

    requestAnimationFrame(() => {
        fitInitialScale();
        update();
    });
}

function showAdminCreateProductModal() {
    const content = `
        <form id="admin-create-product-form" class="commerce-modal-form commerce-modal-form-refined">
            <div class="commerce-modal-grid">
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label" for="admin-product-key">商品 Key <span class="commerce-req">*</span></label>
                    <input id="admin-product-key" type="text" name="key" required placeholder="如 pro_monthly" autocomplete="off" maxlength="64">
                </div>
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label" for="admin-product-title">商品名称 <span class="commerce-req">*</span></label>
                    <input id="admin-product-title" type="text" name="title" required placeholder="展示名称" autocomplete="off" maxlength="128">
                </div>
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label" for="admin-product-price">价格（元）<span class="commerce-req">*</span></label>
                    <input id="admin-product-price" type="text" name="price" required placeholder="如 9.99" inputmode="decimal" autocomplete="off">
                </div>
                <div class="commerce-modal-field commerce-expire-row">
                    <label class="commerce-modal-label" for="admin-product-expire-val">有效期</label>
                    <div class="commerce-expire-inputs">
                        <input id="admin-product-expire-val" type="text" name="expire_val" placeholder="留空表示永久" inputmode="numeric" autocomplete="off">
                        <select id="admin-product-expire-unit" name="expire_unit" aria-label="有效期单位">
                            <option value="day" selected>天</option>
                            <option value="hour">小时</option>
                        </select>
                    </div>
                </div>
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label">封面图</label>
                    <input type="hidden" name="icon" id="admin-product-icon-url" value="">
                    <input type="file" id="admin-product-icon-file" class="commerce-file-input-hidden" accept="image/jpeg,image/png,image/gif,image/webp">
                    <div class="commerce-icon-upload-row">
                        <button type="button" class="btn-secondary commerce-btn-upload" id="admin-product-icon-trigger">上传图片</button>
                        <span id="admin-product-icon-status" class="commerce-icon-status"></span>
                    </div>
                    <div id="admin-product-icon-preview" class="commerce-icon-preview" hidden>
                        <img id="admin-product-icon-preview-img" alt="封面预览">
                    </div>
                </div>
                <div class="commerce-modal-field">
                    <label class="commerce-modal-label" for="admin-product-renew">支持续费</label>
                    <select id="admin-product-renew" name="support_continue" class="commerce-modal-select">
                        <option value="0" selected>否</option>
                        <option value="1">是</option>
                    </select>
                </div>
            </div>
            <div class="commerce-modal-field commerce-modal-field-span commerce-modal-desc-block">
                <label class="commerce-modal-label" for="admin-product-desc">商品描述</label>
                <textarea id="admin-product-desc" name="desc" class="commerce-desc-textarea" rows="4" maxlength="${COMMERCE_MAX_DESC_LEN}" placeholder="支持换行，前台按原格式展示"></textarea>
            </div>
            <div class="modal-actions commerce-modal-actions commerce-modal-actions-compact">
                <button type="button" class="btn-secondary commerce-btn-modal-cancel" onclick="closeModal()">取消</button>
                <button type="submit" class="btn-primary commerce-btn-submit-create">创建</button>
            </div>
        </form>
    `;
    openModal('新增商品', content, 'modal-commerce-product');

    const fileInput = document.getElementById('admin-product-icon-file');
    const trigger = document.getElementById('admin-product-icon-trigger');
    const statusEl = document.getElementById('admin-product-icon-status');
    const hiddenIcon = document.getElementById('admin-product-icon-url');
    const previewWrap = document.getElementById('admin-product-icon-preview');
    const previewImg = document.getElementById('admin-product-icon-preview-img');

    if (trigger && fileInput) {
        trigger.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', async () => {
            const f = fileInput.files && fileInput.files[0];
            if (!f) return;
            fileInput.value = '';
            let croppedBlob;
            try {
                croppedBlob = await openImageCropDialog(f);
            } catch (cropErr) {
                return;
            }
            statusEl.textContent = '上传中…';
            try {
                const croppedFile = new File([croppedBlob], f.name.replace(/\.[^.]+$/, '.jpg'), { type: 'image/jpeg' });
                const res = await adminCommerceAPI.uploadProductIcon(croppedFile);
                const url = res.data && res.data.url;
                if (!url) throw new Error('未返回链接');
                hiddenIcon.value = url;
                previewImg.src = url;
                previewWrap.hidden = false;
                statusEl.textContent = '已上传';
            } catch (err) {
                hiddenIcon.value = '';
                previewWrap.hidden = true;
                statusEl.textContent = err.message || '上传失败';
                showToast(statusEl.textContent, 'error');
            }
        });
    }

    const form = document.getElementById('admin-create-product-form');
    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const key = form.querySelector('[name=key]').value.trim();
            if (!/^[a-zA-Z0-9_-]+$/.test(key) || key.length > 64) {
                showToast('Key 仅允许字母、数字、下划线与短横线，且不超过 64 字符', 'error');
                return;
            }
            const title = form.querySelector('[name=title]').value.trim();
            if (!title || title.length > 128) {
                showToast('请填写商品名称（不超过 128 字）', 'error');
                return;
            }
            const pr = parseAdminProductPriceInput(form.querySelector('[name=price]').value);
            if (!pr.ok) {
                showToast(pr.message, 'error');
                return;
            }
            const desc = (form.querySelector('[name=desc]').value || '');
            if (desc.length > COMMERCE_MAX_DESC_LEN) {
                showToast(`描述过长（最多 ${COMMERCE_MAX_DESC_LEN} 字符）`, 'error');
                return;
            }
            const ev = (form.querySelector('[name=expire_val]').value || '').trim();
            let expireSeconds = null;
            if (ev !== '') {
                if (!/^\d+$/.test(ev)) {
                    showToast('有效期须为正整数', 'error');
                    return;
                }
                const n = parseInt(ev, 10);
                const unit = form.querySelector('[name=expire_unit]').value;
                const mult = unit === 'hour' ? 3600 : 86400;
                expireSeconds = n * mult;
                if (expireSeconds < 1 || expireSeconds > COMMERCE_MAX_EXPIRE_SECONDS) {
                    showToast(`换算后有效期须对应 1～${COMMERCE_MAX_EXPIRE_SECONDS} 秒`, 'error');
                    return;
                }
            }
            const icon = (hiddenIcon && hiddenIcon.value || '').trim() || null;

            const productData = {
                key,
                title,
                desc,
                price: pr.value,
                expire_time: expireSeconds,
                support_continue: form.querySelector('[name=support_continue]').value === '1',
                icon,
            };
            try {
                await adminCommerceAPI.createProduct(productData);
                closeModal();
                await loadAdminProducts();
                showToast('商品创建成功', 'success');
            } catch (err) {
                showToast(`创建失败：${err.message}`, 'error');
            }
        });
    }
}

async function loadAdminProducts() {
    const root = document.getElementById('products-list');
    if (!root) return;
    try {
        const resp = await adminCommerceAPI.getAdminProducts();
        const items = resp.data || [];
        if (!items.length) {
            root.innerHTML = '<div class="commerce-products-empty">暂无商品，点击右上角「新增商品」开始添加。</div>';
            return;
        }
        root.innerHTML = items.map((p) => renderAdminProductCard(p)).join('');

        root.querySelectorAll('.btn-offline-product').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const productId = Number(btn.dataset.id);
                if (!productId) return;
                if (!confirm('确认下架该商品？前台将不再展示。')) return;
                try {
                    await adminCommerceAPI.offlineProduct(productId);
                    await loadAdminProducts();
                    showToast('已下架', 'success');
                } catch (err) {
                    showToast(`下架失败：${err.message}`, 'error');
                }
            });
        });

        root.querySelectorAll('.btn-online-product').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const productId = Number(btn.dataset.id);
                if (!productId) return;
                if (!confirm('确认上架该商品？前台将重新展示。')) return;
                try {
                    await adminCommerceAPI.onlineProduct(productId);
                    await loadAdminProducts();
                    showToast('已上架', 'success');
                } catch (err) {
                    showToast(`上架失败：${err.message}`, 'error');
                }
            });
        });
    } catch (err) {
        showToast(`加载商品失败：${err.message}`, 'error');
    }
}

async function loadAdminOrders(page) {
    adminOrderPage = page;
    const raw = (document.getElementById('order-user-filter') || {}).value;
    const statusFilter = (document.getElementById('order-status-filter') || {}).value || null;
    const s = (raw || '').trim();
    let userIdFilter;
    if (s) {
        if (!/^[1-9]\d{5}$/.test(s)) {
            showToast('用户编号须为 6 位数字且首位不能为 0', 'error');
            return;
        }
        userIdFilter = s;
    }
    try {
        const resp = await adminCommerceAPI.getOrders({
            page,
            page_size: 20,
            user_id: userIdFilter,
            status: statusFilter || undefined
        });
        renderAdminOrdersTable(resp.data || {});
    } catch (err) {
        showToast(`加载订单失败：${err.message}`, 'error');
    }
}

function renderAdminOrdersTable(data) {
    const tbody = document.querySelector('#orders-table tbody');
    if (!tbody) return;
    const items = data.items || [];
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="commerce-empty-cell">暂无订单</td></tr>';
    } else {
        tbody.innerHTML = items.map((o) => {
            const canRefund = o.status === 'paid';
            const sk = commerceOrderStatusKey(o.status);
            const statusLabel = ORDER_STATUS_LABELS[o.status] || escapeHtml(o.status);
            return `<tr>
  <td>${o.id}</td>
  <td>${o.user_id}</td>
  <td><code class="commerce-code">${escapeHtml(o.product_key)}</code></td>
  <td class="commerce-amount">¥${Number(o.amount || 0).toFixed(2)}</td>
  <td><span class="commerce-badge commerce-order-${sk}">${statusLabel}</span></td>
  <td class="commerce-muted">${o.trade_no ? escapeHtml(o.trade_no) : '—'}</td>
  <td class="commerce-muted">${formatDateTime(o.created_at)}</td>
  <td>${canRefund ? `<button type="button" class="commerce-refund-btn refund-btn" data-id="${o.id}">退款</button>` : '<span class="commerce-muted">—</span>'}</td>
</tr>`;
        }).join('');
    }

    const pageInfo = document.getElementById('orders-page-info');
    if (pageInfo) pageInfo.textContent = `共 ${data.total || 0} 条，第 ${data.page || 1} 页`;

    const prevBtn = document.getElementById('orders-prev-btn');
    const nextBtn = document.getElementById('orders-next-btn');
    if (prevBtn) {
        prevBtn.disabled = (data.page || 1) <= 1;
        prevBtn.onclick = () => loadAdminOrders((data.page || 1) - 1);
    }
    if (nextBtn) {
        nextBtn.disabled = (data.page || 1) * (data.page_size || 20) >= (data.total || 0);
        nextBtn.onclick = () => loadAdminOrders((data.page || 1) + 1);
    }

    tbody.querySelectorAll('.refund-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
            const orderId = Number(btn.dataset.id);
            if (!orderId) return;
            if (!confirm(`确认退款订单 #${orderId}？`)) return;
            try {
                await adminCommerceAPI.refundOrder(orderId);
                await loadAdminOrders(adminOrderPage);
                showToast('退款成功', 'success');
            } catch (err) {
                showToast(`退款失败：${err.message}`, 'error');
            }
        });
    });
}

// ===== 权限配置管理（admin） =====

let _permEditId = null; // null=新增, number=编辑

function initAdminPermissions() {
    const openBtn = document.getElementById('open-create-permission-btn');
    if (openBtn && openBtn.dataset.bound !== 'true') {
        openBtn.addEventListener('click', () => showPermissionModal(null));
        openBtn.dataset.bound = 'true';
    }

    const closeBtn = document.getElementById('close-permission-modal');
    if (closeBtn && closeBtn.dataset.bound !== 'true') {
        closeBtn.addEventListener('click', hidePermissionModal);
        closeBtn.dataset.bound = 'true';
    }

    const cancelBtn = document.getElementById('cancel-permission-btn');
    if (cancelBtn && cancelBtn.dataset.bound !== 'true') {
        cancelBtn.addEventListener('click', hidePermissionModal);
        cancelBtn.dataset.bound = 'true';
    }

    const typeSelect = document.getElementById('perm-type');
    if (typeSelect && typeSelect.dataset.bound !== 'true') {
        typeSelect.addEventListener('change', () => {
            const limitGroup = document.getElementById('perm-limit-group');
            limitGroup.style.display = typeSelect.value === 'count_limit' ? '' : 'none';
        });
        typeSelect.dataset.bound = 'true';
    }

    const form = document.getElementById('permission-form');
    if (form && form.dataset.bound !== 'true') {
        form.addEventListener('submit', handlePermissionSubmit);
        form.dataset.bound = 'true';
    }
}

function showPermissionModal(editItem) {
    _permEditId = editItem ? editItem.id : null;
    const modal = document.getElementById('permission-modal');
    const title = document.getElementById('permission-modal-title');
    const keyInput = document.getElementById('perm-key');
    const typeSelect = document.getElementById('perm-type');
    const productKeyInput = document.getElementById('perm-product-key');
    const limitInput = document.getElementById('perm-limit');
    const limitGroup = document.getElementById('perm-limit-group');

    title.textContent = editItem ? '编辑权限配置' : '新增权限配置';

    if (editItem) {
        keyInput.value = editItem.key || '';
        typeSelect.value = editItem.type || 'subscribed';
        productKeyInput.value = editItem.product_key || '';
        const detail = editItem.config_detail || {};
        limitInput.value = detail.limit != null ? detail.limit : '';
    } else {
        keyInput.value = '';
        typeSelect.value = 'subscribed';
        productKeyInput.value = '';
        limitInput.value = '';
    }

    limitGroup.style.display = typeSelect.value === 'count_limit' ? '' : 'none';
    modal.style.display = '';
}

function hidePermissionModal() {
    document.getElementById('permission-modal').style.display = 'none';
    _permEditId = null;
}

async function handlePermissionSubmit(e) {
    e.preventDefault();
    const key = document.getElementById('perm-key').value.trim();
    const type = document.getElementById('perm-type').value;
    const productKey = document.getElementById('perm-product-key').value.trim();
    const limitVal = document.getElementById('perm-limit').value.trim();

    if (!key || !type || !productKey) {
        alert('请填写所有必填项');
        return;
    }

    if (!/^[a-zA-Z0-9_-]+$/.test(key)) {
        alert('权限 Key 仅允许字母、数字、下划线、短横线');
        return;
    }
    if (!/^[a-zA-Z0-9_-]+$/.test(productKey)) {
        alert('产品 Key 仅允许字母、数字、下划线、短横线');
        return;
    }

    const payload = { key, type, product_key: productKey };

    if (type === 'count_limit') {
        const limit = parseInt(limitVal, 10);
        if (!limit || limit <= 0) {
            alert('count_limit 类型必须填写正整数的数量上限');
            return;
        }
        payload.config_detail = { limit };
    } else {
        payload.config_detail = {};
    }

    const saveBtn = document.getElementById('save-permission-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';

    try {
        let res;
        if (_permEditId) {
            res = await adminPermissionAPI.update(_permEditId, payload);
        } else {
            res = await adminPermissionAPI.create(payload);
        }
        if (res.code === 200 || res.code === 201) {
            hidePermissionModal();
            loadAdminPermissions();
        } else {
            alert(res.message || '保存失败');
        }
    } catch (err) {
        alert(err.message || '请求失败');
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
    }
}

async function loadAdminPermissions() {
    const container = document.getElementById('permissions-list');
    if (!container) return;
    container.innerHTML = '<p class="perm-loading">加载中…</p>';

    try {
        const res = await adminPermissionAPI.list();
        if (res.code !== 200) {
            container.innerHTML = `<p class="perm-empty">加载失败: ${res.message || '未知错误'}</p>`;
            return;
        }
        const groups = res.data || [];
        if (groups.length === 0) {
            container.innerHTML = '<p class="perm-empty">暂无权限配置，点击右上角「新增配置」添加</p>';
            return;
        }
        container.innerHTML = groups.map(renderPermissionGroup).join('');

        // 绑定操作按钮
        container.querySelectorAll('.perm-edit-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const item = JSON.parse(btn.dataset.item);
                showPermissionModal(item);
            });
        });
        container.querySelectorAll('.perm-delete-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = parseInt(btn.dataset.id, 10);
                if (!confirm('确定删除该权限配置？')) return;
                try {
                    const res = await adminPermissionAPI.remove(id);
                    if (res.code === 200) {
                        loadAdminPermissions();
                    } else {
                        alert(res.message || '删除失败');
                    }
                } catch (err) {
                    alert(err.message || '删除失败');
                }
            });
        });
    } catch (err) {
        container.innerHTML = `<p class="perm-empty">加载失败: ${err.message || '网络错误'}</p>`;
    }
}

function renderPermissionGroup(group) {
    const typeLabel = group.type === 'count_limit' ? '总量限制' : '订阅限制';
    const typeBadge = group.type === 'count_limit'
        ? '<span class="perm-type-badge perm-type-count">count_limit</span>'
        : '<span class="perm-type-badge perm-type-sub">subscribed</span>';

    const productRows = (group.products || []).map(p => {
        const detail = p.config_detail || {};
        const limitText = detail.limit != null ? `上限: ${detail.limit}` : '-';
        const itemData = JSON.stringify({
            id: p.id,
            key: group.key,
            type: group.type,
            product_key: p.product_key,
            config_detail: detail,
        }).replace(/"/g, '&quot;');

        return `<tr>
            <td>${escapeHtml(p.product_key)}</td>
            <td>${group.type === 'count_limit' ? limitText : '<span class="perm-text-muted">不适用</span>'}</td>
            <td class="perm-actions-cell">
                <button type="button" class="perm-edit-btn perm-action-btn" data-item="${itemData}" title="编辑">✏️</button>
                <button type="button" class="perm-delete-btn perm-action-btn perm-action-danger" data-id="${p.id}" title="删除">🗑️</button>
            </td>
        </tr>`;
    }).join('');

    return `<div class="perm-group-card">
        <div class="perm-group-header">
            <div class="perm-group-title">
                <span class="perm-key-label">${escapeHtml(group.key)}</span>
                ${typeBadge}
            </div>
            <span class="perm-type-desc">${typeLabel}</span>
        </div>
        <table class="perm-table">
            <thead><tr><th>产品 Key</th><th>配置详情</th><th>操作</th></tr></thead>
            <tbody>${productRows || '<tr><td colspan="3" class="perm-text-muted">暂无产品配置</td></tr>'}</tbody>
        </table>
    </div>`;
}

// ===== 资源管理（admin） =====

let currentEditResourceId = null;

function initResources() {
    const createBtn = document.getElementById('open-create-resource-btn');
    if (createBtn && createBtn.dataset.bound !== 'true') {
        createBtn.addEventListener('click', () => showResourceModal(null));
        createBtn.dataset.bound = 'true';
    }
    const closeBtn = document.getElementById('close-resource-modal');
    if (closeBtn && closeBtn.dataset.bound !== 'true') {
        closeBtn.addEventListener('click', hideResourceModal);
        closeBtn.dataset.bound = 'true';
    }
    const cancelBtn = document.getElementById('cancel-resource-btn');
    if (cancelBtn && cancelBtn.dataset.bound !== 'true') {
        cancelBtn.addEventListener('click', hideResourceModal);
        cancelBtn.dataset.bound = 'true';
    }
    const form = document.getElementById('resource-form');
    if (form && form.dataset.bound !== 'true') {
        form.addEventListener('submit', handleResourceFormSubmit);
        form.dataset.bound = 'true';
    }
    // 监听类型和来源选择变化，动态显示补充信息
    const typeSelect = document.getElementById('resource-type');
    const sourceSelect = document.getElementById('resource-source');
    if (typeSelect && typeSelect.dataset.bound !== 'true') {
        typeSelect.addEventListener('change', toggleResourceExtraFields);
        typeSelect.dataset.bound = 'true';
    }
    if (sourceSelect && sourceSelect.dataset.bound !== 'true') {
        sourceSelect.addEventListener('change', toggleResourceExtraFields);
        sourceSelect.dataset.bound = 'true';
    }
    const toggleSecretBtn = document.getElementById('toggle-ak-secret');
    if (toggleSecretBtn && toggleSecretBtn.dataset.bound !== 'true') {
        toggleSecretBtn.addEventListener('click', () => {
            const input = document.getElementById('resource-extra-ak-secret');
            const isPassword = input.type === 'password';
            input.type = isPassword ? 'text' : 'password';
            toggleSecretBtn.textContent = isPassword ? '🙈' : '👁️';
        });
        toggleSecretBtn.dataset.bound = 'true';
    }
    const toggleLoginPwdBtn = document.getElementById('toggle-login-password');
    if (toggleLoginPwdBtn && toggleLoginPwdBtn.dataset.bound !== 'true') {
        toggleLoginPwdBtn.addEventListener('click', () => {
            const input = document.getElementById('resource-extra-login-password');
            const isPassword = input.type === 'password';
            input.type = isPassword ? 'text' : 'password';
            toggleLoginPwdBtn.textContent = isPassword ? '🙈' : '👁️';
        });
        toggleLoginPwdBtn.dataset.bound = 'true';
    }
}

function toggleResourceExtraFields() {
    const type = document.getElementById('resource-type').value;
    const source = document.getElementById('resource-source').value;
    const mysqlFields = document.getElementById('resource-extra-fields');
    const githubFields = document.getElementById('resource-extra-github-fields');
    const cloudServerFields = document.getElementById('resource-extra-cloud-server-fields');
    const sourceSelect = document.getElementById('resource-source');

    // 根据资源类型动态过滤来源选项
    const sourceOptions = sourceSelect.querySelectorAll('option');
    sourceOptions.forEach(opt => {
        if (!opt.value) return; // 跳过占位选项
        if (type === 'mysql') {
            opt.style.display = opt.value === 'aliyun' ? '' : 'none';
        } else if (type === 'code_repo') {
            opt.style.display = opt.value === 'github' ? '' : 'none';
        } else if (type === 'cloud_server') {
            opt.style.display = opt.value === 'tencent_cloud' ? '' : 'none';
        } else {
            opt.style.display = '';
        }
    });

    mysqlFields.style.display = (type === 'mysql' && source === 'aliyun') ? '' : 'none';
    githubFields.style.display = (type === 'code_repo' && source === 'github') ? '' : 'none';
    cloudServerFields.style.display = (type === 'cloud_server' && source === 'tencent_cloud') ? '' : 'none';
}

function showResourceModal(item) {
    const modal = document.getElementById('resource-modal');
    const title = document.getElementById('resource-modal-title');
    const form = document.getElementById('resource-form');

    form.reset();
    document.getElementById('resource-extra-fields').style.display = 'none';
    document.getElementById('resource-extra-ak-secret').type = 'password';
    const toggleBtn = document.getElementById('toggle-ak-secret');
    if (toggleBtn) toggleBtn.textContent = '👁️';
    document.getElementById('resource-extra-github-fields').style.display = 'none';
    document.getElementById('resource-extra-cloud-server-fields').style.display = 'none';
    document.getElementById('resource-extra-login-password').type = 'password';
    const toggleLoginBtn = document.getElementById('toggle-login-password');
    if (toggleLoginBtn) toggleLoginBtn.textContent = '👁️';

    if (item) {
        currentEditResourceId = item.id;
        title.textContent = '编辑资源';
        document.getElementById('resource-name').value = item.name || '';
        document.getElementById('resource-type').value = item.type || '';
        document.getElementById('resource-source').value = item.source || '';
        // 设置环境复选框
        const envCheckboxes = document.querySelectorAll('input[name="resource-envs"]');
        const envs = item.envs || [];
        envCheckboxes.forEach(cb => {
            cb.checked = envs.includes(cb.value);
        });
        // 设置补充信息
        const extra = item.extra || {};
        document.getElementById('resource-extra-url').value = extra.url || '';
        document.getElementById('resource-extra-ak-id').value = extra.access_key_id || '';
        document.getElementById('resource-extra-ak-secret').value = extra.access_key_secret || '';
        document.getElementById('resource-extra-organization').value = extra.organization || '';
        document.getElementById('resource-extra-app-id').value = extra.app_id || '';
        document.getElementById('resource-extra-private-key').value = extra.private_key || '';
        document.getElementById('resource-extra-login-user').value = extra.login_user || '';
        document.getElementById('resource-extra-login-password').value = extra.login_password || '';
        document.getElementById('resource-extra-server-ip').value = extra.server_ip || '';
        toggleResourceExtraFields();
    } else {
        currentEditResourceId = null;
        title.textContent = '新增资源';
    }
    modal.style.display = 'flex';
}

function hideResourceModal() {
    document.getElementById('resource-modal').style.display = 'none';
    currentEditResourceId = null;
}

async function handleResourceFormSubmit(e) {
    e.preventDefault();
    const saveBtn = document.getElementById('save-resource-btn');
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';

    try {
        const name = document.getElementById('resource-name').value.trim();
        const type = document.getElementById('resource-type').value;
        const source = document.getElementById('resource-source').value;

        if (!name) {
            alert('请填写资源名称');
            return;
        }
        if (!type || !source) {
            alert('请选择资源类型和来源');
            return;
        }

        const envCheckboxes = document.querySelectorAll('input[name="resource-envs"]:checked');
        const envs = Array.from(envCheckboxes).map(cb => cb.value);
        if (envs.length === 0) {
            alert('请至少选择一个可用环境');
            return;
        }

        const extra = {};
        if (type === 'mysql' && source === 'aliyun') {
            const url = document.getElementById('resource-extra-url').value.trim();
            const akId = document.getElementById('resource-extra-ak-id').value.trim();
            const akSecret = document.getElementById('resource-extra-ak-secret').value.trim();
            if (!url || !akId) {
                alert('请填写数据库实例地址和 AccessKey ID');
                return;
            }
            extra.url = url;
            extra.access_key_id = akId;
            if (akSecret) {
                extra.access_key_secret = akSecret;
            } else if (!currentEditResourceId) {
                alert('请填写 AccessKey Secret');
                return;
            }
        } else if (type === 'code_repo' && source === 'github') {
            const organization = document.getElementById('resource-extra-organization').value.trim();
            const appId = document.getElementById('resource-extra-app-id').value.trim();
            const privateKey = document.getElementById('resource-extra-private-key').value.trim();
            if (!organization) {
                alert('请填写 GitHub Organization');
                return;
            }
            if (!appId) {
                alert('请填写 GitHub App ID');
                return;
            }
            extra.organization = organization;
            extra.app_id = appId;
            if (privateKey) {
                extra.private_key = privateKey;
            } else if (!currentEditResourceId) {
                alert('请填写 GitHub App Private Key（PEM 格式，非 Client Secret）');
                return;
            }
        } else if (type === 'cloud_server' && source === 'tencent_cloud') {
            const loginUser = document.getElementById('resource-extra-login-user').value.trim();
            const loginPassword = document.getElementById('resource-extra-login-password').value.trim();
            const serverIp = document.getElementById('resource-extra-server-ip').value.trim();
            if (!loginUser) {
                alert('请填写登录账号');
                return;
            }
            if (!serverIp) {
                alert('请填写服务器IP');
                return;
            }
            extra.login_user = loginUser;
            extra.server_ip = serverIp;
            if (loginPassword) {
                extra.login_password = loginPassword;
            } else if (!currentEditResourceId) {
                alert('请填写登录密码');
                return;
            }
        }

        const payload = { name, type, source, envs, extra };

        let res;
        if (currentEditResourceId) {
            // 编辑时如果没填敏感字段，不传该字段（保留原值）
            if (type === 'mysql' && source === 'aliyun' && !extra.access_key_secret) {
                delete payload.extra.access_key_secret;
            }
            if (type === 'code_repo' && source === 'github' && !extra.private_key) {
                delete payload.extra.private_key;
            }
            if (type === 'cloud_server' && source === 'tencent_cloud' && !extra.login_password) {
                delete payload.extra.login_password;
            }
            res = await adminResourceAPI.update(currentEditResourceId, payload);
        } else {
            res = await adminResourceAPI.create(payload);
        }

        if (res.code === 200 || res.code === 201) {
            hideResourceModal();
            loadAdminResources();
        } else {
            alert(res.message || '保存失败');
        }
    } catch (err) {
        alert(err.message || '请求失败');
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = '保存';
    }
}

async function loadAdminResources() {
    initResources();
    const container = document.getElementById('resources-list');
    if (!container) return;
    container.innerHTML = '<p class="perm-loading">加载中…</p>';

    try {
        const res = await adminResourceAPI.list();
        if (res.code !== 200) {
            container.innerHTML = `<p class="perm-empty">加载失败: ${res.message || '未知错误'}</p>`;
            return;
        }
        const resources = res.data || [];
        if (resources.length === 0) {
            container.innerHTML = '<p class="perm-empty">暂无资源，点击右上角「新增资源」添加</p>';
            return;
        }
        container.innerHTML = `
            <table class="data-table resource-table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>名称</th>
                        <th>类型</th>
                        <th>来源</th>
                        <th>可用环境</th>
                        <th>实例信息</th>
                        <th>状态</th>
                        <th>创建时间</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>
                    ${resources.map(renderResourceRow).join('')}
                </tbody>
            </table>`;

        // 绑定操作按钮
        container.querySelectorAll('.resource-edit-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const item = JSON.parse(btn.dataset.item);
                showResourceModal(item);
            });
        });
        container.querySelectorAll('.resource-toggle-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const id = parseInt(btn.dataset.id, 10);
                const action = btn.dataset.action;
                try {
                    const res = action === 'offline'
                        ? await adminResourceAPI.offline(id)
                        : await adminResourceAPI.online(id);
                    if (res.code === 200) {
                        loadAdminResources();
                    } else {
                        alert(res.message || '操作失败');
                    }
                } catch (err) {
                    alert(err.message || '操作失败');
                }
            });
        });
    } catch (err) {
        container.innerHTML = `<p class="perm-empty">加载失败: ${err.message || '网络错误'}</p>`;
    }
}

function renderResourceRow(resource) {
    const typeLabels = { mysql: 'MySQL', code_repo: '代码仓库', cloud_server: '云服务器' };
    const sourceLabels = { aliyun: '阿里云', github: 'GitHub', tencent_cloud: '腾讯云' };
    const envLabels = { test: '测试', prod: '生产' };

    const envsHtml = (resource.envs || []).map(e =>
        `<span class="resource-env-tag">${envLabels[e] || e}</span>`
    ).join(' ');

    const extra = resource.extra || {};
    let instanceInfo = '-';
    if (resource.type === 'code_repo') {
        instanceInfo = extra.organization || '-';
    } else if (resource.type === 'cloud_server') {
        instanceInfo = extra.server_ip || '-';
    } else {
        instanceInfo = extra.url || '-';
    }

    const statusClass = resource.is_online ? 'resource-status-online' : 'resource-status-offline';
    const statusText = resource.is_online ? '上架' : '下架';
    const toggleAction = resource.is_online ? 'offline' : 'online';
    const toggleText = resource.is_online ? '下架' : '上架';

    const itemData = JSON.stringify({
        id: resource.id,
        name: resource.name,
        type: resource.type,
        source: resource.source,
        envs: resource.envs,
        extra: resource.extra,
    }).replace(/"/g, '&quot;');

    const createdAt = resource.created_at
        ? new Date(resource.created_at).toLocaleDateString('zh-CN')
        : '-';

    return `<tr>
        <td>${resource.id}</td>
        <td class="resource-url-cell" title="${escapeHtml(resource.name || '')}">${escapeHtml(resource.name || '-')}</td>
        <td>${typeLabels[resource.type] || resource.type}</td>
        <td>${sourceLabels[resource.source] || resource.source}</td>
        <td>${envsHtml}</td>
        <td class="resource-url-cell" title="${escapeHtml(instanceInfo)}">${escapeHtml(instanceInfo)}</td>
        <td><span class="resource-status-badge ${statusClass}">${statusText}</span></td>
        <td>${createdAt}</td>
        <td class="perm-actions-cell">
            <button type="button" class="resource-edit-btn perm-action-btn" data-item="${itemData}" title="编辑">✏️</button>
            <button type="button" class="resource-toggle-btn perm-action-btn" data-id="${resource.id}" data-action="${toggleAction}" title="${toggleText}">${resource.is_online ? '⏸️' : '▶️'}</button>
        </td>
    </tr>`;
}

