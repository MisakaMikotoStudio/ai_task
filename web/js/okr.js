/**
 * OKR管理模块 - 瀑布流版本
 */

// 当前选中的周期类型和周期偏移量
let currentCycleType = 'week';
let currentCycleOffset = 0;  // 0表示当前周期，-1表示上一周期，以此类推

// 自动保存相关
let autoSaveTimers = {};
const AUTO_SAVE_DELAY = 800;  // 800ms防抖

// 当前加载的OKR数据（用于排序操作）
let currentObjectives = [];

// 计算周期日期范围
function getCycleDates(cycleType, offset = 0) {
    const now = new Date();
    let start, end;

    if (cycleType === 'week') {
        // 计算当前周的周一
        const dayOfWeek = now.getDay() || 7;
        const monday = new Date(now);
        monday.setDate(now.getDate() - dayOfWeek + 1 + (offset * 7));
        monday.setHours(0, 0, 0, 0);

        const sunday = new Date(monday);
        sunday.setDate(monday.getDate() + 6);
        sunday.setHours(23, 59, 59, 999);

        start = monday;
        end = sunday;
    } else if (cycleType === 'month') {
        const year = now.getFullYear();
        const month = now.getMonth() + offset;

        start = new Date(year, month, 1, 0, 0, 0, 0);
        end = new Date(year, month + 1, 0, 23, 59, 59, 999);
    }

    return { start, end };
}

// 格式化日期为 YYYY-MM-DD（使用本地时间，避免时区转换问题）
function formatDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

// 获取周期显示文本
function getCyclePeriodText(cycleType, offset) {
    const { start, end } = getCycleDates(cycleType, offset);
    const startStr = `${start.getMonth() + 1}/${start.getDate()}`;
    const endStr = `${end.getMonth() + 1}/${end.getDate()}`;

    if (cycleType === 'week') {
        if (offset === 0) return `本周 (${startStr} - ${endStr})`;
        if (offset === 1) return `下周 (${startStr} - ${endStr})`;
        if (offset === -1) return `上周 (${startStr} - ${endStr})`;
        if (offset > 1) return `${offset}周后 (${startStr} - ${endStr})`;
        return `${Math.abs(offset)}周前 (${startStr} - ${endStr})`;
    } else {
        const monthName = start.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long' });
        if (offset === 0) return `本月 (${monthName})`;
        if (offset === 1) return `下月 (${monthName})`;
        if (offset === -1) return `上月 (${monthName})`;
        if (offset > 1) return `${offset}月后 (${monthName})`;
        return `${Math.abs(offset)}月前 (${monthName})`;
    }
}

// 加载目标列表
async function loadObjectives() {
    try {
        const { start, end } = getCycleDates(currentCycleType, currentCycleOffset);
        // 传递周期范围给后端，由后端过滤和拼接数据，避免N+1查询
        const response = await okrAPI.listObjectives(currentCycleType, null, formatDate(start), formatDate(end));

        // 后端已过滤，直接使用返回数据
        currentObjectives = response.data || [];
        renderWaterfallOKR(currentObjectives);
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// 渲染瀑布流OKR界面
function renderWaterfallOKR(objectives) {
    const container = document.getElementById('okr-list');
    const emptyState = document.getElementById('okr-empty');

    if (!container) return;

    // 更新周期导航
    updateCycleNavigation();

    if (objectives.length === 0) {
        container.innerHTML = `
            <div class="okr-waterfall">
                <div class="okr-add-row">
                    <button class="okr-add-btn okr-add-objective" onclick="createNewObjective()">
                        <span class="add-icon">+</span> 添加目标 (O)
                    </button>
                </div>
            </div>
        `;
        if (emptyState) emptyState.style.display = 'none';
        return;
    }

    if (emptyState) emptyState.style.display = 'none';

    let html = '<div class="okr-waterfall">';

    objectives.forEach((obj, objIndex) => {
        html += renderObjectiveBlock(obj, objIndex, objectives.length);
    });

    // 添加新目标按钮
    html += `
        <div class="okr-add-row">
            <button class="okr-add-btn okr-add-objective" onclick="createNewObjective()">
                <span class="add-icon">+</span> 添加目标 (O)
            </button>
        </div>
    `;

    html += '</div>';
    container.innerHTML = html;
}

// 渲染单个Objective块
function renderObjectiveBlock(obj, objIndex, totalObjectives) {
    const krs = obj.key_results || [];

    let html = `
        <div class="okr-objective-block" data-id="${obj.id}">
            <div class="okr-o-row">
                <div class="okr-reorder-side">
                    <button class="okr-reorder-btn" onclick="moveObjective(${obj.id}, 'up')" title="上移" ${objIndex === 0 ? 'disabled' : ''}>
                        <span>↑</span>
                    </button>
                    <button class="okr-reorder-btn" onclick="moveObjective(${obj.id}, 'down')" title="下移" ${objIndex === totalObjectives - 1 ? 'disabled' : ''}>
                        <span>↓</span>
                    </button>
                </div>
                <div class="okr-add-side">
                    <button class="okr-side-add-btn" onclick="createNewObjective(${obj.id})" title="在下方添加目标">
                        <span>+</span>
                    </button>
                </div>
                <div class="okr-o-content">
                    <span class="okr-o-label">O${objIndex + 1}</span>
                    <input type="text" class="okr-inline-input okr-o-title"
                        value="${escapeHtml(obj.title)}"
                        data-id="${obj.id}"
                        data-field="title"
                        placeholder="输入目标标题..."
                        onchange="autoSaveObjective(${obj.id}, 'title', this.value)"
                        onkeyup="scheduleAutoSave(${obj.id}, 'objective', 'title', this.value)">
                    <button class="okr-delete-btn" onclick="deleteObjective(${obj.id})" title="删除目标">
                        <span>×</span>
                    </button>
                </div>
            </div>
    `;

    // 渲染KRs
    krs.forEach((kr, krIndex) => {
        html += renderKeyResultBlock(kr, krIndex, obj.id, krs.length);
    });

    // 添加KR按钮
    html += `
        <div class="okr-kr-row okr-add-kr-row">
            <div class="okr-add-side"></div>
            <div class="okr-kr-content">
                <button class="okr-add-btn okr-add-kr" onclick="createNewKeyResult(${obj.id})">
                    <span class="add-icon">+</span> 添加关键结果 (KR)
                </button>
            </div>
        </div>
    `;

    html += '</div>';
    return html;
}

// 渲染单个KeyResult块
function renderKeyResultBlock(kr, krIndex, objectiveId, totalKrs) {
    return `
        <div class="okr-kr-row" data-kr-id="${kr.id}" data-objective-id="${objectiveId}">
            <div class="okr-reorder-side">
                <button class="okr-reorder-btn" onclick="moveKeyResult(${objectiveId}, ${kr.id}, 'up')" title="上移" ${krIndex === 0 ? 'disabled' : ''}>
                    <span>↑</span>
                </button>
                <button class="okr-reorder-btn" onclick="moveKeyResult(${objectiveId}, ${kr.id}, 'down')" title="下移" ${krIndex === totalKrs - 1 ? 'disabled' : ''}>
                    <span>↓</span>
                </button>
            </div>
            <div class="okr-add-side">
                <button class="okr-side-add-btn" onclick="createNewKeyResult(${objectiveId}, ${kr.id})" title="在下方添加KR">
                    <span>+</span>
                </button>
            </div>
            <div class="okr-kr-content">
                <span class="okr-kr-label">KR${krIndex + 1}</span>
                <input type="text" class="okr-inline-input okr-kr-title"
                    value="${escapeHtml(kr.title)}"
                    data-kr-id="${kr.id}"
                    data-field="title"
                    placeholder="输入关键结果..."
                    onchange="autoSaveKeyResult(${kr.id}, 'title', this.value)"
                    onkeyup="scheduleAutoSave(${kr.id}, 'keyResult', 'title', this.value)">
                <button class="okr-delete-btn" onclick="deleteKeyResult(${kr.id}, ${objectiveId})" title="删除KR">
                    <span>×</span>
                </button>
            </div>
        </div>
    `;
}

// 生成周期选项列表（下一周期、当前周期及过去N个周期）
function getCycleOptions(cycleType, count = 12) {
    const options = [];
    // 先添加下一周期选项
    options.push({ offset: 1, text: getCyclePeriodText(cycleType, 1) });
    // 再添加当前及过去周期
    for (let i = 0; i >= -count + 1; i--) {
        options.push({ offset: i, text: getCyclePeriodText(cycleType, i) });
    }
    return options;
}

// 更新周期导航
function updateCycleNavigation() {
    const nav = document.getElementById('okr-cycle-nav');
    if (!nav) return;

    const options = getCycleOptions(currentCycleType);
    const optionsHtml = options.map(opt =>
        `<option value="${opt.offset}" ${opt.offset === currentCycleOffset ? 'selected' : ''}>${opt.text}</option>`
    ).join('');

    nav.innerHTML = `
        <div class="cycle-type-btns">
            <button class="cycle-type-btn ${currentCycleType === 'week' ? 'active' : ''}"
                onclick="switchCycleType('week')">周目标</button>
            <button class="cycle-type-btn ${currentCycleType === 'month' ? 'active' : ''}"
                onclick="switchCycleType('month')">月目标</button>
        </div>
        <div class="cycle-period-nav">
            <select class="cycle-period-select" onchange="selectCyclePeriod(this.value)">
                ${optionsHtml}
            </select>
        </div>
    `;
}

// 选择周期
function selectCyclePeriod(offset) {
    currentCycleOffset = parseInt(offset);
    loadObjectives();
}

// 切换周期类型
function switchCycleType(type) {
    currentCycleType = type;
    currentCycleOffset = 0;  // 重置为当前周期
    loadObjectives();
}


// 创建新目标
async function createNewObjective(afterId = null) {
    console.log('[OKR] createNewObjective called, afterId:', afterId);
    try {
        const { start, end } = getCycleDates(currentCycleType, currentCycleOffset);
        console.log('[OKR] Creating objective with cycle:', currentCycleType, formatDate(start), formatDate(end));

        const result = await okrAPI.createObjective('', null, currentCycleType, formatDate(start), formatDate(end));
        console.log('[OKR] Objective created:', result);
        loadObjectives();
    } catch (error) {
        console.error('[OKR] createNewObjective error:', error);
        showToast(error.message || '创建目标失败', 'error');
    }
}

// 创建新KR
async function createNewKeyResult(objectiveId, afterKrId = null) {
    try {
        await okrAPI.createKeyResult(objectiveId, '');  // 空标题
        // 重新加载整个列表
        loadObjectives();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// 自动保存调度器
function scheduleAutoSave(id, type, field, value) {
    const key = `${type}-${id}-${field}`;

    if (autoSaveTimers[key]) {
        clearTimeout(autoSaveTimers[key]);
    }

    autoSaveTimers[key] = setTimeout(() => {
        if (type === 'objective') {
            autoSaveObjective(id, field, value);
        } else {
            autoSaveKeyResult(id, field, value);
        }
        delete autoSaveTimers[key];
    }, AUTO_SAVE_DELAY);
}

// 自动保存目标
async function autoSaveObjective(id, field, value) {
    try {
        const data = {};
        data[field] = value;
        await okrAPI.updateObjective(id, data);
        showSaveIndicator();
    } catch (error) {
        showToast(`保存失败: ${error.message}`, 'error');
    }
}

// 自动保存KR
async function autoSaveKeyResult(id, field, value) {
    try {
        const data = {};
        data[field] = value;
        await okrAPI.updateKeyResult(id, data);
        showSaveIndicator();
    } catch (error) {
        showToast(`保存失败: ${error.message}`, 'error');
    }
}

// 显示保存指示器
function showSaveIndicator() {
    let indicator = document.getElementById('okr-save-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.id = 'okr-save-indicator';
        indicator.className = 'okr-save-indicator';
        indicator.innerHTML = '✓ 已保存';
        document.body.appendChild(indicator);
    }

    indicator.classList.add('show');
    setTimeout(() => {
        indicator.classList.remove('show');
    }, 1500);
}

// 删除目标
async function deleteObjective(id) {
    if (!confirm('确定删除该目标？相关的关键结果也会被删除。')) return;

    try {
        await okrAPI.deleteObjective(id);
        showToast('目标已删除', 'success');
        loadObjectives();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// 删除KR
async function deleteKeyResult(krId, objectiveId) {
    if (!confirm('确定删除该关键结果？')) return;

    try {
        await okrAPI.deleteKeyResult(krId);
        showToast('KR已删除', 'success');
        loadObjectives();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// 初始化OKR事件监听
function initOKREvents() {
    // 移除旧的按钮事件，新版本不需要
}

// 移动目标（上移/下移）
async function moveObjective(objectiveId, direction) {
    const index = currentObjectives.findIndex(o => o.id === objectiveId);
    if (index === -1) return;

    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= currentObjectives.length) return;

    // 交换位置
    const temp = currentObjectives[index];
    currentObjectives[index] = currentObjectives[newIndex];
    currentObjectives[newIndex] = temp;

    // 获取新的ID顺序
    const objectiveIds = currentObjectives.map(o => o.id);

    try {
        await okrAPI.reorderObjectives(objectiveIds);
        renderWaterfallOKR(currentObjectives);
        showSaveIndicator();
    } catch (error) {
        showToast(error.message || '排序失败', 'error');
        loadObjectives();  // 重新加载恢复原状态
    }
}

// 移动关键结果（上移/下移）
async function moveKeyResult(objectiveId, krId, direction) {
    const obj = currentObjectives.find(o => o.id === objectiveId);
    if (!obj || !obj.key_results) return;

    const krs = obj.key_results;
    const index = krs.findIndex(kr => kr.id === krId);
    if (index === -1) return;

    const newIndex = direction === 'up' ? index - 1 : index + 1;
    if (newIndex < 0 || newIndex >= krs.length) return;

    // 交换位置
    const temp = krs[index];
    krs[index] = krs[newIndex];
    krs[newIndex] = temp;

    // 获取新的ID顺序
    const krIds = krs.map(kr => kr.id);

    try {
        await okrAPI.reorderKeyResults(objectiveId, krIds);
        renderWaterfallOKR(currentObjectives);
        showSaveIndicator();
    } catch (error) {
        showToast(error.message || '排序失败', 'error');
        loadObjectives();  // 重新加载恢复原状态
    }
}
