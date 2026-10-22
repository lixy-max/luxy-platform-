// Provider配置（E-1 拆分 · 2026-10-19）
// 从 index3.html 抽出 ✓ 依赖全局：api / showToast / escapeHtml / API_BASE 等 ✓
// 顶层 function 声明 ⇒ 浏览器里自动成为 window 属性 ✓（onclick 才能调到 ✓）
'use strict';

function switchProviderTab(name) {
    currentProviderTab = name;
    ['vllm', 'ollama', 'openai'].forEach(n => {
        document.getElementById('provider-form-' + n).style.display = (n === name) ? 'block' : 'none';
        const btn = document.getElementById('ptab-' + n);
        btn.className = (n === name) ? 'ptab active' : 'ptab inactive';
    });
}
async function loadProviderConfigs() {
    try {
        const res = await fetch(`${API_BASE}/providers`);
        providerConfigs = await res.json();
        ['vllm', 'ollama', 'openai'].forEach(name => {
            const cfg = providerConfigs[name] || {};
            ['api_base', 'model', 'api_key'].forEach(field => {
                const el = document.getElementById(`pv-${name}-${field}`);
                if (!el) return;
                el.value = cfg[field] || '';
                if (field === 'api_key') {
                    // 后端不再回传明文 Key：已配置时用占位提示，留空保存即表示不修改
                    el.placeholder = cfg.api_key_set ? '已配置（留空则不修改）' : '未配置';
                }
            });
        });
    } catch (e) {}
}
async function saveProviderConfig() {
    const name = currentProviderTab;
    const payload = {
        api_base: document.getElementById(`pv-${name}-api_base`).value.trim(),
        model: document.getElementById(`pv-${name}-model`).value.trim(),
        api_key: document.getElementById(`pv-${name}-api_key`)?.value || '',
    };
    const res = await fetch(`${API_BASE}/providers/${name}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    if (res.ok) { showToast(`Provider '${name}' 配置已保存`, true); await loadProviderConfigs(); }
    else { showToast('保存失败', false); }
}
async function onProviderChange() {
    const type = document.getElementById('train-provider').value;
    const area = document.getElementById('train-provider-fields');
    if (!Object.keys(providerConfigs).length) await loadProviderConfigs();
    const cfg = providerConfigs[type] || {};
    if (type === 'vllm') {
        area.innerHTML = `<div class="field-hint" style="color:var(--c-success);padding:8px;background:var(--c-hexf0f8f0);border-radius:4px;"><svg class="icon icon-sm"><use href="vendor/icons.svg#circle-check"></use></svg> 使用本地 vLLM</div>`;
    } else if (type === 'ollama') {
        area.innerHTML = `<div class="field-hint" style="color:var(--c-success);padding:8px;background:var(--c-hexf0f8f0);border-radius:4px;"><svg class="icon icon-sm"><use href="vendor/icons.svg#circle-check"></use></svg> Ollama: ${cfg.api_base || '未配置'} / ${cfg.model || '未配置'}</div>`;
    } else if (type === 'openai') {
        if (!cfg.api_key && !cfg.api_key_set) {
            area.innerHTML = `<div class="field-hint" style="color:var(--c-danger);padding:8px;background:var(--c-surface-err3);border-radius:4px;"><svg class="icon icon-sm"><use href="vendor/icons.svg#triangle-alert"></use></svg>️ 云 API 未配置 Key，请到"系统设置"填写</div>`;
        } else {
            area.innerHTML = `<div class="field-hint" style="color:var(--c-success);padding:8px;background:var(--c-hexf0f8f0);border-radius:4px;"><svg class="icon icon-sm"><use href="vendor/icons.svg#circle-check"></use></svg> 云 API: ${cfg.api_base} / ${cfg.model}（Key 已配置）</div>`;
        }
    }
}
