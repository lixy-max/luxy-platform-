// 知识库页（E-1 拆分 · 2026-10-19）
// 从 index3.html 抽出 ✓ 依赖全局：api / showToast / escapeHtml / API_BASE 等 ✓
// 顶层 function 声明 ⇒ 浏览器里自动成为 window 属性 ✓（onclick 才能调到 ✓）
'use strict';

async function loadKBList() {
    const list = document.getElementById('kb-list');
    list.innerHTML = '<li style="padding:12px;color:var(--c-text-faint);">加载中...</li>';
    try {
        const _kbl = await api.tryGet('/kb/list');
    const res = { ok: _kbl.ok, status: _kbl.status, json: async () => (_kbl.data || {}) };
        const data = await res.json();
        availableKBNames = (data.knowledge_bases || []).map(k => k.name);
        list.innerHTML = '';
        if (!data.knowledge_bases || !data.knowledge_bases.length) {
            list.innerHTML = '<li style="padding:12px;color:var(--c-text-faint);">暂无知识库，点右上角"新建知识库"</li>';
            return;
        }
        data.knowledge_bases.forEach(kb => {
            const li = document.createElement('li');
            li.className = 'hist-item';
            li.setAttribute('data-kb-name', kb.name || '');
            li.onclick = () => {
                document.querySelectorAll('#kb-list .hist-item').forEach(x => x.classList.remove('selected'));
                li.classList.add('selected');
                showKBDetail(kb.name);
            };
            li.innerHTML = `
                <div style="font-weight:600;font-size:13px;"><svg class="icon icon-sm"><use href="vendor/icons.svg#library"></use></svg> ${escapeHtml(kb.name)}</div>
                <div style="font-size:11px;color:var(--c-text-muted);margin-top:4px;">
                    ${kb.total_chunks} 分块 · ${(kb.documents||[]).length} 文档
                </div>
                <div style="font-size:11px;color:var(--c-text-faint);margin-top:2px;">
                    ${escapeHtml(kb.description || '')}
                </div>
            `;
            list.appendChild(li);
        });
    } catch (e) {
        list.innerHTML = '<li style="padding:12px;color:var(--c-danger);">加载失败: ' + e + '</li>';
    }
}
async function showKBDetail(kbName) {
    const panel = document.getElementById('kb-detail');
    panel.innerHTML = '<p>加载中...</p>';
    try {
        const res = await fetch(`${API_BASE}/kb/${kbName}`);
        const kb = await res.json();

        const docsHtml = (kb.documents || []).map(d =>
            `<li style="padding:8px 0;border-bottom:1px solid var(--c-border-light);font-size:12px;">
                <div style="font-weight:500;">${escapeHtml(d.name)}</div>
                <div style="color:var(--c-text-muted);font-size:11px;">
                    ${d.chunks} 分块 · ${d.chars} 字符
                    · ${(d.added_at || '').slice(0,19).replace('T',' ')}
                </div>
            </li>`
        ).join('') || '<li style="color:var(--c-text-faint);padding:8px 0;">（暂无文档）</li>';

        panel.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h3 style="margin:0;"><svg class="icon icon-sm"><use href="vendor/icons.svg#library"></use></svg> ${escapeHtml(kb.name)}</h3>
                <button class="btn-ghost btn-small" onclick="deleteKB('${escapeHtml(kb.name)}')"><svg class="icon icon-sm"><use href="vendor/icons.svg#trash-2"></use></svg>️ 删除</button>
            </div>
            <div style="font-size:12px;color:var(--c-text-muted);margin-bottom:16px;">
                ${escapeHtml(kb.description || '')}
                · 创建于 ${(kb.created_at||'').slice(0,19).replace('T',' ')}
                · ${kb.total_chunks} 分块
            </div>

            <!-- ★ P-109（2026-10-20）: 转换（升维/降维）✓ -->
            <div style="margin-bottom:16px;padding:12px;background:var(--c-surface-2,#f5f5f5);border-radius:8px;">
                <div style="font-weight:600;font-size:13px;margin-bottom:8px;">
                    <svg class="icon icon-sm"><use href="vendor/icons.svg#sliders"></use></svg> 转换（升维 / 降维）
                </div>
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                    <span style="font-size:12.5px;color:var(--c-text-2);">当前：<b id="kb-cur-dim">—</b></span>
                    <select class="select" id="kb-conv-model" style="min-width:220px;padding:6px 10px;"></select>
                    <button class="btn-primary btn-small" onclick="kbConvertDim('${escapeHtml(kb.name)}')">转换 →</button>
                </div>
                <div style="font-size:11.5px;color:var(--c-text-3);margin-top:6px;">
                    ⚠ 换向量模型会【重建整个索引】✓ 块数不变 ✓ 维度随新模型变 ✓ 可能几分钟 ⚠
                </div>
                <div id="kb-conv-msg" style="margin-top:6px;font-size:12.5px;"></div>
            </div>

            <div style="margin-bottom:16px;padding:12px;background:var(--c-bg-light);border-radius:8px;">
                <div style="font-weight:600;font-size:13px;margin-bottom:8px;"><svg class="icon icon-sm"><use href="vendor/icons.svg#download"></use></svg> 添加文档</div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    <button class="btn-primary" onclick="uploadKBZip('${escapeHtml(kb.name)}')"><svg class="icon icon-sm"><use href="vendor/icons.svg#package"></use></svg> 上传 zip（内含多个 PDF/TXT）</button>
                    <button class="btn-primary" onclick="ingestKBFolder('${escapeHtml(kb.name)}')"><svg class="icon icon-sm"><use href="vendor/icons.svg#folder"></use></svg> 从服务器文件夹导入</button>
                </div>
                <div style="font-size:11px;color:var(--c-text-faint);margin-top:6px;">
                    文件夹导入示例：~/papers/ 或 /mnt/e/papers/
                </div>
            </div>

            <div style="margin-bottom:16px;padding:12px;background:var(--c-info-bg);border-radius:8px;">
                <div style="font-weight:600;font-size:13px;margin-bottom:8px;color:var(--c-primary-dark);"><svg class="icon icon-sm"><use href="vendor/icons.svg#search"></use></svg> 检索测试</div>
                <div style="display:flex;gap:8px;">
                    <input id="kb-query-input" placeholder="输入查询语句" style="flex:1;padding:8px 12px;border:1px solid var(--c-border);border-radius:6px;">
                    <button class="btn-success" onclick="testKBQuery('${escapeHtml(kb.name)}')">检索</button>
                </div>
                <div id="kb-query-results" style="margin-top:12px;"></div>
            </div>

            <!-- ★ K2（2026-10-20）: 4 个 KB 数据工具面板 ✓ -->
            <div id="kb-folders" style="margin-bottom:12px;">
              <!-- 📊 入库检测 -->
              <div style="margin-bottom:10px;padding:12px;background:var(--c-surface-2,#f5f5f5);border-radius:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
                  <b style="font-size:13px;">入库检测（只读 ✓ 不动库）</b>
                  <div style="display:flex;gap:6px;">
                    <button class="btn-ghost btn-small" onclick="kbAnalyze('${escapeHtml(kb.name)}')">检测已有文档</button>
                    <input type="file" id="kb-analyze-files" multiple style="display:none;"
                           onchange="kbAnalyzeUpload('${escapeHtml(kb.name)}')">
                    <button class="btn-primary btn-small" onclick="document.getElementById('kb-analyze-files').click()">上传检测</button>
                  </div>
                </div>
                <div id="kb-analyze-result" style="margin-top:8px;font-size:12.5px;line-height:1.8;color:var(--c-text-2);"></div>
              </div>

              <!-- 🧹 一键清洗 -->
              <div style="margin-bottom:10px;padding:12px;background:var(--c-surface-2,#f5f5f5);border-radius:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
                  <b style="font-size:13px;">一键清洗 → 建新库（不动原库 ✓）</b>
                  <button class="btn-success btn-small" onclick="kbClean('${escapeHtml(kb.name)}')">执行清洗 →</button>
                </div>
                <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px;font-size:12.5px;">
                  <label>chunk_size <input id="kb-clean-size" type="number" value="400" min="100" max="1500" style="width:70px;padding:3px 6px;"></label>
                  <label>overlap <input id="kb-clean-overlap" type="number" value="50" min="0" max="200" style="width:60px;padding:3px 6px;"></label>
                  <label><input type="checkbox" id="kb-clean-dedup" checked> 精确去重</label>
                  <label>新库名 <input id="kb-clean-name" placeholder="${escapeHtml(kb.name)}_clean" style="width:150px;padding:3px 6px;"></label>
                </div>
                <div id="kb-clean-result" style="margin-top:8px;font-size:12.5px;line-height:1.8;color:var(--c-text-2);"></div>
              </div>

              <!-- 👁 块预览 -->
              <div style="margin-bottom:10px;padding:12px;background:var(--c-surface-2,#f5f5f5);border-radius:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
                  <b style="font-size:13px;">块预览（看实际切出来的片段 ✓）</b>
                  <button class="btn-ghost btn-small" onclick="kbPreviewChunks('${escapeHtml(kb.name)}')">预览前 10 块</button>
                </div>
                <div id="kb-chunks-result" style="margin-top:8px;font-size:12px;max-height:280px;overflow-y:auto;"></div>
              </div>

              <!-- 🎯 检索评测 -->
              <div style="margin-bottom:12px;padding:12px;background:var(--c-info-bg,#eef4ff);border-radius:8px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
                  <b style="font-size:13px;color:var(--c-primary-dark,#2b7de9);">检索评测（Recall@k / MRR / nDCG ✓）</b>
                  <div style="display:flex;gap:6px;align-items:center;">
                    <label style="font-size:12px;">n=<input id="kb-eval-n" type="number" value="10" min="3" max="50" style="width:55px;padding:3px 5px;"></label>
                    <label style="font-size:12px;">k=<input id="kb-eval-k" type="number" value="5" min="1" max="20" style="width:45px;padding:3px 5px;"></label>
                    <button class="btn-success btn-small" onclick="kbEval('${escapeHtml(kb.name)}')">跑评测</button>
                  </div>
                </div>
                <div id="kb-eval-result" style="margin-top:8px;font-size:12.5px;line-height:1.8;"></div>
              </div>
            </div>

            <div style="font-weight:600;font-size:13px;margin-bottom:8px;"><svg class="icon icon-sm"><use href="vendor/icons.svg#file-text"></use></svg> 已入库文档</div>
            <ul style="list-style:none;padding:0;margin:0;">
                ${docsHtml}
            </ul>
        `;
        // ★ P-109: 填充转换用模型下拉 + 当前维度 ✓
        try { kbFillConvModels(kbName); } catch (e) {}
    } catch (e) {
        panel.innerHTML = '<p style="color:var(--c-danger);">加载失败: ' + e + '</p>';
    }
}

// ══════════════════════════════════════════════════════════
// ★ P-109（2026-10-20）: 知识库搜索 + 转换（升维/降维）
// ══════════════════════════════════════════════════════════
let _kbAllNames = [];

function filterKBList() {
    const inp = document.getElementById('kb-search');
    const q = ((inp && inp.value) || '').trim().toLowerCase();
    const items = document.querySelectorAll('#kb-list .hist-item');
    let shown = 0;
    items.forEach(function (li) {
        const name = (li.getAttribute('data-kb-name') || li.textContent || '').toLowerCase();
        const ok = !q || name.indexOf(q) >= 0;
        li.style.display = ok ? '' : 'none';
        if (ok) shown++;
    });
    const empty = document.getElementById('kb-search-empty');
    if (!items.length) return;
    if (shown === 0) {
        if (!empty) {
            const d = document.createElement('li');
            d.id = 'kb-search-empty';
            d.style.cssText = 'padding:12px;color:var(--c-text-faint);font-size:13px;';
            d.textContent = '没有匹配的知识库';
            document.getElementById('kb-list').appendChild(d);
        }
    } else if (empty) {
        empty.remove();
    }
}

async function kbFillConvModels(kbName) {
    const sel = document.getElementById('kb-conv-model');
    const curEl = document.getElementById('kb-cur-dim');
    if (!sel) return;
    // 模型列表 ✓
    if (!sel.options.length) {
        try {
            const r = await fetch(API_BASE + '/embedding-models', { cache: 'no-store' });
            const d = await r.json();
            const models = d.models || [];
            sel.innerHTML = models.map(function (m) {
                return '<option value="' + escapeHtml(m.path || m.id || '') + '">' +
                    escapeHtml(m.id || '') + (m.dim ? '（' + m.dim + '维）' : '') + '</option>';
            }).join('') || '<option value="">（无可用模型）</option>';
        } catch (e) { sel.innerHTML = '<option value="">（读取失败）</option>'; }
    }
    // 当前维度 ✓
    try {
        const r2 = await fetch(API_BASE + '/kb/' + encodeURIComponent(kbName) + '/embedding', { cache: 'no-store' });
        const d2 = await r2.json();
        const sig = d2.embedding || d2.active || {};
        const dim = sig.dim || d2.dim || '?';
        const model = sig.model || (typeof d2.active === 'string' ? d2.active : '') || '—';
        if (curEl) curEl.textContent = String(dim) + ' 维（' + model + '）';
    } catch (e) {
        if (curEl) curEl.textContent = '—';
    }
}

async function kbConvertDim(kbName) {
    const sel = document.getElementById('kb-conv-model');
    const msg = document.getElementById('kb-conv-msg');
    const val = (sel && sel.value || '').trim();
    if (!val) { showToast('请先选一个目标向量模型', false); return; }
    if (!confirm('把「' + kbName + '」的向量模型换成：\n' + val +
                 '\n\n会【重建整个索引】（块数不变、维度随新模型调整），可能要几分钟。继续？')) return;
    if (msg) { msg.textContent = '转换中…（重建索引 ⚠ 请勿关闭页面）'; msg.style.color = 'var(--c-text-muted)'; }
    showToast('正在转换（升维/降维）并重建索引…', true);
    try {
        const r = await fetch(API_BASE + '/kb/' + encodeURIComponent(kbName) + '/embedding', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: val, rebuild: true }),
        });
        const d = await r.json();
        if (!r.ok) {
            if (msg) { msg.textContent = '✗ 失败：' + (d.detail || r.status); msg.style.color = 'var(--c-danger,#c55)'; }
            return;
        }
        const info = '✓ 已转换：' + (d.model || val) + '（' + (d.dim || '?') + ' 维 · ' + (d.chunks || '?') + ' 块）';
        if (msg) { msg.textContent = info; msg.style.color = 'var(--c-success,#2a9d63)'; }
        showToast(info, true);
        // 刷新 ✓
        if (typeof kbFillConvModels === 'function') { try { kbFillConvModels(kbName); } catch (e) {} }
        if (typeof showKBDetail === 'function') { try { showKBDetail(kbName); } catch (e) {} }
        if (typeof loadKBList === 'function') { try { loadKBList(); } catch (e) {} }
    } catch (e) {
        if (msg) { msg.textContent = '✗ ' + String(e); msg.style.color = 'var(--c-danger,#c55)'; }
    }
}

function openCreateKBModal() {
    document.getElementById('kb-new-name').value = '';
    document.getElementById('kb-new-desc').value = '';
    document.getElementById('kb-create-modal').style.display = 'flex';
}
function closeCreateKBModal() {
    document.getElementById('kb-create-modal').style.display = 'none';
}
async function confirmCreateKB() {
    const name = document.getElementById('kb-new-name').value.trim();
    const desc = document.getElementById('kb-new-desc').value.trim();
    if (!name) { showToast('请输入名称', false); return; }
    try {
        const res = await fetch(`${API_BASE}/kb/create`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name, description: desc})
        });
        const data = await res.json();
        if (!res.ok) { showToast(data.detail || '创建失败', false); return; }
        showToast(`知识库 '${name}' 已创建`, true);
        closeCreateKBModal();
        loadKBList();
    } catch (e) {
        showToast('网络错误: ' + e, false);
    }
}
async function deleteKB(name) {
    if (!confirm(`确定删除知识库 '${name}'？这会清空所有入库数据。`)) return;
    try {
        const res = await fetch(`${API_BASE}/kb/${name}`, {method: 'DELETE'});
        const data = await res.json();
        if (!res.ok) { showToast(data.detail || '删除失败', false); return; }
        showToast('已删除', true);
        loadKBList();
        document.getElementById('kb-detail').innerHTML = '<p style="color:var(--c-text-faint);">已删除</p>';
    } catch (e) {
        showToast('网络错误: ' + e, false);
    }
}
function uploadKBZip(kbName) {
    kbZipTargetName = kbName;
    const input = document.getElementById('kb-zip-input');
    input.value = '';
    input.onchange = async () => {
        const file = input.files[0];
        if (!file) return;
        const fd = new FormData();
        fd.append('file', file);
        showToast('上传并处理中，请稍候...', true);
        try {
            const res = await fetch(`${API_BASE}/kb/${kbName}/upload_zip`, {
                method: 'POST', body: fd
            });
            const data = await res.json();
            if (!res.ok) { showToast(data.detail || '上传失败', false); return; }
            const skipped = (data.skipped || []).length;
            showToast(`入库 ${data.added_docs} 文档 / ${data.added_chunks} 分块` +
                      (skipped ? `，跳过 ${skipped} 个` : ''), true);
            showKBDetail(kbName);
        } catch (e) {
            showToast('网络错误: ' + e, false);
        }
    };
    input.click();
}
async function ingestKBFolder(kbName) {
    const folder = prompt('输入服务器文件夹路径（绝对路径）：\n例如 ~/papers 或 /mnt/e/papers');
    if (!folder) return;
    showToast('扫描并处理中，请稍候...', true);
    try {
        const res = await fetch(`${API_BASE}/kb/${kbName}/ingest_folder`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({folder_path: folder})
        });
        const data = await res.json();
        if (!res.ok) { showToast(data.detail || '导入失败', false); return; }
        const skipped = (data.skipped || []).length;
        showToast(`入库 ${data.added_docs} 文档 / ${data.added_chunks} 分块` +
                  (skipped ? `，跳过 ${skipped} 个` : ''), true);
        showKBDetail(kbName);
    } catch (e) {
        showToast('网络错误: ' + e, false);
    }
}
async function testKBQuery(kbName) {
    const input = document.getElementById('kb-query-input');
    const q = input.value.trim();
    if (!q) { showToast('请输入查询语句', false); return; }
    const results = document.getElementById('kb-query-results');
    results.innerHTML = '<div style="color:var(--c-text-faint);font-size:12px;">检索中...</div>';
    try {
        const res = await fetch(`${API_BASE}/kb/${kbName}/query`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({query: q, top_k: 3})
        });
        const data = await res.json();
        if (!res.ok) { results.innerHTML = `<div style="color:var(--c-danger);font-size:12px;">${data.detail}</div>`; return; }
        if (!data.results.length) { results.innerHTML = '<div style="color:var(--c-text-faint);font-size:12px;">无结果</div>'; return; }
        results.innerHTML = data.results.map((r, i) => `
            <div style="padding:10px;background:var(--c-surface);border-radius:6px;margin-bottom:8px;font-size:12px;border-left:3px solid var(--c-primary);">
                <div style="font-weight:600;color:var(--c-primary-dark);margin-bottom:4px;">
                    片段 ${i+1} · ${escapeHtml(r.source)} · 相似度 ${r.score ? r.score.toFixed(3) : '-'}
                </div>
                <div style="white-space:pre-wrap;color:var(--c-text);">${escapeHtml(r.text.slice(0, 400))}${r.text.length > 400 ? '...' : ''}</div>
            </div>
        `).join('');
    } catch (e) {
        results.innerHTML = `<div style="color:var(--c-danger);font-size:12px;">网络错误: ${e}</div>`;
    }
}

// ══════════════════════════════════════════════════════════
// ★ K0.5（2026-10-20）: 默认向量模型 + 推理设备（CPU / GPU ✓）
// ══════════════════════════════════════════════════════════
async function loadEmbedSettings() {
    const msel = document.getElementById('embed-model-select');
    const dsel = document.getElementById('embed-device-select');
    const msg = document.getElementById('embed-settings-msg');
    if (!msel || !dsel) return;
    try {
        const r = await fetch(API_BASE + '/settings/embedding', { cache: 'no-store' });
        const d = await r.json();
        if (!d.ok) { if (msg) msg.textContent = '✗ ' + (d.error || '读取失败'); return; }
        // 模型列表 ✓
        const models = d.models || [];
        msel.innerHTML = models.map(function (m) {
            const id = (typeof m === 'string') ? m : (m.id || m.name || m.model || '');
            const dim = (typeof m === 'object' && m.dim) ? (' · ' + m.dim + '维') : '';
            const sel = (id === d.model_env) ? ' selected' : '';
            return '<option value="' + escapeHtml(id) + '"' + sel + '>' + escapeHtml(id + dim) + '</option>';
        }).join('') || '<option value="">（无可用模型）</option>';
        // 设备列表 ✓
        const devices = d.devices || [];
        dsel.innerHTML = devices.map(function (x) {
            const sel = (x.value === d.device) ? ' selected' : '';
            return '<option value="' + escapeHtml(x.value) + '"' + sel + '>' + escapeHtml(x.label || x.value) + '</option>';
        }).join('');
        if (msg) { msg.textContent = ''; msg.style.color = ''; }
    } catch (e) {
        if (msg) { msg.textContent = '✗ ' + String(e); msg.style.color = 'var(--c-danger, #c55)'; }
    }
}

async function saveEmbedSettings() {
    const msel = document.getElementById('embed-model-select');
    const dsel = document.getElementById('embed-device-select');
    const msg = document.getElementById('embed-settings-msg');
    const payload = {};
    if (msel) payload.model = msel.value;
    if (dsel) payload.device = dsel.value;
    if (msg) { msg.textContent = '保存中…'; msg.style.color = 'var(--c-text-muted)'; }
    try {
        const r = await fetch(API_BASE + '/settings/embedding', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (d.ok) {
            if (msg) {
                msg.innerHTML = '✅ 已保存：' + (d.changed || []).join(' · ') +
                    ' <span style="opacity:.7">' + escapeHtml(d.note || '') + '</span>';
                msg.style.color = 'var(--c-success, #2a9d63)';
            }
            if (typeof kbReloadEmbedding === 'function') { try { kbReloadEmbedding(); } catch (e) {} }
        } else {
            if (msg) { msg.textContent = '✗ ' + (d.error || '保存失败'); msg.style.color = 'var(--c-danger, #c55)'; }
        }
    } catch (e) {
        if (msg) { msg.textContent = '✗ ' + String(e); msg.style.color = 'var(--c-danger, #c55)'; }
    }
}

// 页面加载后自动初始化 ✓（延迟等 DOM ✓）
try {
    setTimeout(function () { try { loadEmbedSettings(); } catch (e) {} }, 1800);
} catch (e) {}

// ══════════════════════════════════════════════════════════
// ★ K2（2026-10-20）: KB 数据工具 4 个面板的 JS
// ══════════════════════════════════════════════════════════
function _kbPanelMsg(id, html, isErr) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = html;
    el.style.color = isErr ? 'var(--c-danger,#c55)' : 'var(--c-text-2,#555)';
}

async function kbAnalyze(kbName) {
    _kbPanelMsg('kb-analyze-result', '检测中…（读取已保存文档 ✓）');
    try {
        const r = await fetch(API_BASE + '/kb/' + encodeURIComponent(kbName) + '/analyze', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        });
        const d = await r.json();
        if (!d.ok) { _kbPanelMsg('kb-analyze-result', '✗ ' + escapeHtml(d.error || '检测失败'), true); return; }
        const probs = d.problems || {};
        const probTxt = Object.keys(probs).length
            ? Object.keys(probs).map(function (k) { return k + '×' + probs[k]; }).join(' · ')
            : '<span style="color:#2a9d63">无 ✓</span>';
        let rows = (d.docs || []).slice(0, 8).map(function (x) {
            return '<div style="font-size:12px;color:var(--c-text-3);">· ' + escapeHtml(x.file || '') +
                '：' + (x.chars || 0) + ' 字 · raw ' + (x.raw_chunks || 0) + ' 块 → clean ' + (x.clean_chunks || 0) + ' 块</div>';
        }).join('');
        _kbPanelMsg('kb-analyze-result',
            '<div><b>文档 ' + (d.doc_count || 0) + '</b> · 字符 ' + (d.total_chars || 0) +
            ' · <b>raw ' + (d.raw_chunks || 0) + ' 块 → clean ' + (d.clean_chunks || 0) + ' 块</b></div>' +
            '<div>问题：' + probTxt + '</div>' + rows);
    } catch (e) {
        _kbPanelMsg('kb-analyze-result', '✗ ' + String(e), true);
    }
}

async function kbAnalyzeUpload(kbName) {
    const inp = document.getElementById('kb-analyze-files');
    if (!inp || !inp.files || !inp.files.length) return;
    _kbPanelMsg('kb-analyze-result', '上传检测中…（' + inp.files.length + ' 个文件）');
    const fd = new FormData();
    for (let i = 0; i < inp.files.length; i++) fd.append('files', inp.files[i]);
    try {
        const r = await fetch(API_BASE + '/kb/' + encodeURIComponent(kbName) + '/analyze', { method: 'POST', body: fd });
        const d = await r.json();
        if (!d.ok) { _kbPanelMsg('kb-analyze-result', '✗ ' + escapeHtml(d.error || '检测失败'), true); return; }
        _kbPanelMsg('kb-analyze-result',
            '<div><b>文档 ' + (d.doc_count || 0) + '</b> · 字符 ' + (d.total_chars || 0) +
            ' · raw ' + (d.raw_chunks || 0) + ' 块 → clean ' + (d.clean_chunks || 0) + ' 块</div>' +
            '<div>问题：' + (Object.keys(d.problems || {}).length ? JSON.stringify(d.problems) : '无 ✓') + '</div>');
    } catch (e) {
        _kbPanelMsg('kb-analyze-result', '✗ ' + String(e), true);
    } finally {
        inp.value = '';
    }
}

async function kbClean(kbName) {
    const size = parseInt((document.getElementById('kb-clean-size') || {}).value || '400', 10);
    const ov = parseInt((document.getElementById('kb-clean-overlap') || {}).value || '50', 10);
    const dedup = !!((document.getElementById('kb-clean-dedup') || {}).checked);
    const newName = ((document.getElementById('kb-clean-name') || {}).value || '').trim();
    _kbPanelMsg('kb-clean-result', '清洗中…（可能 1-3 分钟 ⚠ 要先加载 embedding）');
    try {
        const r = await fetch(API_BASE + '/kb/' + encodeURIComponent(kbName) + '/clean', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                opts: { chunk_size: size, overlap: ov, dedup_exact: dedup },
                new_name: newName || undefined,
            }),
        });
        const d = await r.json();
        if (!d.ok) { _kbPanelMsg('kb-clean-result', '✗ ' + escapeHtml(d.error || '清洗失败'), true); return; }
        const st = d.stats || {};
        _kbPanelMsg('kb-clean-result',
            '<div style="color:#2a9d63;font-weight:600;">✅ 已建新库：' + escapeHtml(d.new_kb) +
            '（' + ((d.created || {}).chunks || 0) + ' 块 · ' + ((d.created || {}).dimensions || '?') + ' 维）</div>' +
            '<div>块 ' + (st.chunks || 0) + ' · 字符 ' + (st.chars_in || 0) + ' → ' + (st.chars_out || 0) +
            ' · 噪声删 ' + (st.noise_removed || 0) + ' · 去重 ' + (st.dedup_removed || 0) + '</div>' +
            '<div style="font-size:11.5px;color:var(--c-text-3);margin-top:4px;">原库未改动 ✓ 可在左侧列表切换到新库查看</div>');
        if (typeof loadKBList === 'function') { try { loadKBList(); } catch (e) {} }
    } catch (e) {
        _kbPanelMsg('kb-clean-result', '✗ ' + String(e), true);
    }
}

async function kbPreviewChunks(kbName) {
    _kbPanelMsg('kb-chunks-result', '读取中…');
    try {
        const r = await fetch(API_BASE + '/kb/' + encodeURIComponent(kbName) + '/chunks/preview?limit=10');
        const d = await r.json();
        if (!d.ok) { _kbPanelMsg('kb-chunks-result', '✗ ' + escapeHtml(d.error || '读取失败'), true); return; }
        const html = (d.items || []).map(function (it) {
            return '<div style="padding:6px 0;border-bottom:1px solid var(--c-border-light,#eee);">' +
                '<div style="font-size:11px;color:var(--c-text-3);">[' + it.i + '] ' +
                escapeHtml(it.source || '') + (it.section ? (' / ' + escapeHtml(it.section)) : '') +
                ' · ' + it.chars + ' 字</div>' +
                '<div style="font-size:12px;white-space:pre-wrap;">' + escapeHtml((it.text || '').slice(0, 240)) + '</div></div>';
        }).join('');
        _kbPanelMsg('kb-chunks-result',
            '<div style="font-size:11.5px;color:var(--c-text-3);margin-bottom:6px;">共 ' + (d.total || 0) + ' 块 · 显示前 ' + (d.items || []).length + ' 块</div>' + html);
    } catch (e) {
        _kbPanelMsg('kb-chunks-result', '✗ ' + String(e), true);
    }
}

async function kbEval(kbName) {
    const n = parseInt((document.getElementById('kb-eval-n') || {}).value || '10', 10);
    const k = parseInt((document.getElementById('kb-eval-k') || {}).value || '5', 10);
    _kbPanelMsg('kb-eval-result', '评测中…（本地模型出题 ⚠ 约 1-3 分钟）');
    try {
        const r = await fetch(API_BASE + '/kb/' + encodeURIComponent(kbName) + '/eval', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ n_questions: n, top_k: k }),
        });
        const d = await r.json();
        if (!d.ok) { _kbPanelMsg('kb-eval-result', '✗ ' + escapeHtml(d.error || '评测失败'), true); return; }
        const fmt = function (x) { return (x == null) ? '—' : Number(x).toFixed(3); };
        let details = (d.details || []).slice(0, 6).map(function (x) {
            return '<div style="font-size:11.5px;color:var(--c-text-3);">rank=' + (x.rank || '✗') +
                ' [' + escapeHtml(x.source || '') + '] ' + escapeHtml((x.q || '').slice(0, 50)) + '</div>';
        }).join('');
        _kbPanelMsg('kb-eval-result',
            '<div style="font-size:13px;"><b>Recall@' + (d.top_k || k) + ' = ' + fmt(d['recall@k']) + '</b>' +
            ' · MRR = ' + fmt(d.mrr) + ' · nDCG = ' + fmt(d['ndcg@k(近似)']) +
            ' · Hit@1 = ' + fmt(d['hit@1']) + '</div>' +
            '<div style="font-size:11.5px;color:var(--c-text-3);">n=' + (d.n_questions || 0) +
            ' · 总块 ' + (d.total_chunks || 0) + ' · ' + escapeHtml(d.note || '') + '</div>' + details);
    } catch (e) {
        _kbPanelMsg('kb-eval-result', '✗ ' + String(e), true);
    }
}
