// 历史记录页（E-1 拆分样板 · 2026-10-19）
// 从 index3.html 抽出 ✓ 依赖全局：API_BASE / escapeHtml / polishReport / showToast / api ✓
// 页面 HTML 里的 onclick="deleteHistory(...)"/"saveHistoryNotes(...)" 需要这些函数是全局的 ✓
//   ⇒ 本文件用顶层 function 声明 ✓（浏览器里它们自动成为 window 的属性 ✓）
'use strict';

var histSearchTimer = null;

function debouncedLoadHistory() {
    clearTimeout(histSearchTimer);
    histSearchTimer = setTimeout(loadHistory, 400);
}

async function loadHistory() {
    const q = document.getElementById('hist-search').value.trim();
    const status = document.getElementById('hist-status').value;
    const url = new URL(`${API_BASE}/history`);
    url.searchParams.set('limit', '50');
    if (q) url.searchParams.set('q', q);
    if (status) url.searchParams.set('status', status);

    const list = document.getElementById('history-list');
    list.innerHTML = '<li style="padding:12px;color:var(--c-text-faint);">加载中...</li>';
    try {
        const res = await fetch(url);
        const data = await res.json();
        list.innerHTML = '';
        if (!data.tasks.length) {
            list.innerHTML = '<li style="padding:12px;color:var(--c-text-faint);">暂无记录</li>';
            return;
        }
        data.tasks.forEach(t => {
            const li = document.createElement('li');
            li.className = 'hist-item';
            li.onclick = () => {
                document.querySelectorAll('.hist-item').forEach(x => x.classList.remove('selected'));
                li.classList.add('selected');
                showHistoryDetail(t.task_id);
            };
            const badge = {success: '✅', error: '❌', running: '⏳'}[t.status] || '·';
            const elapsed = t.elapsed ? ` · ${t.elapsed.toFixed(1)}s` : '';
            li.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div style="font-weight:600;font-size:13px;">
                        ${badge} ${escapeHtml(t.file_name || t.task_id)}
                    </div>
                    <div style="font-size:11px;color:var(--c-text-faint);">${(t.started_at||'').slice(5,19).replace('T',' ')}</div>
                </div>
                <div style="font-size:11px;color:var(--c-text-muted);margin-top:4px;">
                    ID: ${t.task_id}${elapsed} · 节点 ${t.total_nodes}
                </div>
            `;
            list.appendChild(li);
        });
    } catch (e) {
        list.innerHTML = '<li style="padding:12px;color:var(--c-danger);">加载失败: ' + e + '</li>';
    }
}

async function showHistoryDetail(taskId) {
    const panel = document.getElementById('history-detail');
    panel.innerHTML = '<p>加载中...</p>';
    try {
        const _hi = await api.tryGet('/history/' + encodeURIComponent(taskId));
    const res = { ok: _hi.ok, status: _hi.status, json: async () => (_hi.data || {}) };
        const t = await res.json();
        const r = t.result_json || {};
        const statusColor = {success:'var(--c-success)', error:'var(--c-danger)', running:'var(--c-warning)'}[t.status] || 'var(--c-text-muted)';

        panel.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                <h3 style="margin:0;">${escapeHtml(t.file_name || taskId)}</h3>
                <button class="btn-ghost btn-small" onclick="deleteHistory('${taskId}')"><svg class="icon icon-sm"><use href="vendor/icons.svg#trash-2"></use></svg>️ 删除</button>
            </div>
            <div style="font-size:12px;color:var(--c-text-muted);margin-bottom:12px;">
                状态：<span style="color:${statusColor};font-weight:600;">${t.status}</span>
                · 耗时：${t.elapsed ? t.elapsed.toFixed(1) + 's' : '-'}
                · 节点：${t.total_nodes}
                · 开始：${(t.started_at||'').slice(0,19).replace('T',' ')}
            </div>
            ${t.error ? `<div style="background:var(--c-danger-bg);padding:10px;border-radius:6px;margin-bottom:12px;font-size:12px;color:var(--c-danger-text);white-space:pre-wrap;">
                <b>错误：</b>${escapeHtml(t.error)}
            </div>` : ''}
            <div style="margin-bottom:12px;">
                <label style="font-size:12px;color:var(--c-text-muted);">备注：</label>
                <input id="hist-notes" value="${escapeHtml(t.notes || '')}"
                       style="width:100%;padding:6px 10px;border:1px solid var(--c-border);border-radius:6px;margin-top:4px;"
                       onblur="saveHistoryNotes('${taskId}')">
            </div>
            ${r._meta && r._meta.usage && r._meta.usage.total_calls ? `
                <div style="background:var(--c-info-bg);padding:10px;border-radius:6px;margin-bottom:12px;font-size:12px;color:var(--c-primary-dark);">
                    <b><svg class="icon icon-sm"><use href="vendor/icons.svg#chart-column"></use></svg> LLM 用量：</b>
                    ${Object.entries(r._meta.usage.by_provider || {}).map(([p, s]) =>
                        `<span style="margin-right:12px;">${p}: ${s.calls} 次调用` +
                        (s.total_tokens ? ` / ${s.total_tokens} tokens` : '') + `</span>`
                    ).join('')}
                </div>
            ` : ''}
            ${r.final_report ? `
                <div style="background:var(--c-bg-light);padding:16px;border-radius:8px;margin-bottom:12px;">
                    <h4 style="margin:0 0 8px 0;color:var(--c-primary);"><svg class="icon icon-sm"><use href="vendor/icons.svg#file-text"></use></svg> 最终报告</h4>
                    <div style="white-space:pre-wrap;font-size:13px;line-height:1.6;">${escapeHtml(polishReport(r.final_report))}</div>
                </div>
            ` : ''}
            ${Object.entries(r.nodes || {}).map(([k, v]) => {
                const content = typeof v === 'string' ? v : JSON.stringify(v, null, 2);
                return `<details style="margin-bottom:8px;padding:8px;background:var(--c-bg-light);border-radius:6px;">
                    <summary style="cursor:pointer;font-weight:600;color:var(--c-primary);">🔹 ${k}</summary>
                    <pre style="white-space:pre-wrap;font-size:11px;margin-top:8px;">${escapeHtml(content.slice(0, 3000))}</pre>
                </details>`;
            }).join('')}
        `;
    } catch (e) {
        panel.innerHTML = '<p style="color:var(--c-danger);">加载失败: ' + e + '</p>';
    }
}

async function saveHistoryNotes(taskId) {
    const notes = document.getElementById('hist-notes').value;
    await fetch(`${API_BASE}/history/${taskId}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({notes})
    });
    showToast('备注已保存', true);
}

async function deleteHistory(taskId) {
    if (!confirm('确定删除这条历史记录吗？')) return;
    await fetch(`${API_BASE}/history/${taskId}`, {method: 'DELETE'});
    showToast('已删除', true);
    loadHistory();
    document.getElementById('history-detail').innerHTML = '<p style="color:var(--c-text-faint);">已删除</p>';
}
