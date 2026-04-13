// admin-commerce.js - Admin 商品/订单（/admin 下嵌入 index.html，样式见 style.css .commerce-*）
let adminOrderPage = 1;

const ORDER_STATUS_LABELS = { pending: '待支付', paid: '已支付', failed: '失败', refunded: '已退款' };

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
    if (!/^\d+(\.\d{1,2})?$/.test(s)) return { ok: false, message: '价格须为数字，最多两位小数' };
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
            if (!title || title.length > 128) { showToast('请填写商品名称（不超过 128 字）', 'error'); return; }
            const pr = parseAdminProductPriceInput(form.querySelector('[name=price]').value);
            if (!pr.ok) { showToast(pr.message, 'error'); return; }
            const desc = (form.querySelector('[name=desc]').value || '');
            if (desc.length > COMMERCE_MAX_DESC_LEN) {
                showToast(`描述过长（最多 ${COMMERCE_MAX_DESC_LEN} 字符）`, 'error');
                return;
            }
            const ev = (form.querySelector('[name=expire_val]').value || '').trim();
            let expireSeconds = null;
            if (ev !== '') {
                if (!/^\d+$/.test(ev)) { showToast('有效期须为正整数', 'error'); return; }
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
        if (!/^[1-9]\d{5}$/.test(s)) { showToast('用户编号须为 6 位数字且首位不能为 0', 'error'); return; }
        userIdFilter = s;
    }
    try {
        const opts = { page, page_size: 20, user_id: userIdFilter, status: statusFilter || undefined };
        const resp = await adminCommerceAPI.getOrders(opts);
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
