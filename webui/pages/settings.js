// 设置页（E-1 拆分第二页 · 2026-10-19）
// 从 index3.html 抽出 ✓ 依赖全局：api / showToast / loadProviderConfigs / escapeHtml 等 ✓
// HTML 里 onclick="saveSettings()" 需要这些函数是全局的 ✓
//   ⇒ 顶层 function 声明 ⇒ 浏览器里自动成为 window 属性 ✓
'use strict';

async function loadSettings() {
    const _se = await api.tryGet('/settings');
    const res = { ok: _se.ok, status: _se.status, json: async () => (_se.data || {}) };
    const data = await res.json();
    ['base_model_path', 'lora_dir', 'vllm_chat_api', 'lora_manager_api', 'venv_python'].forEach(k => {
        const el = document.getElementById('set-' + k);
        if (el) el.value = data[k] || '';
    });
    loadProviderConfigs();
}

async function saveSettings() {
    const payload = {
        base_model_path: document.getElementById('set-base_model_path').value,
        lora_dir: document.getElementById('set-lora_dir').value,
        vllm_chat_api: document.getElementById('set-vllm_chat_api').value,
        lora_manager_api: document.getElementById('set-lora_manager_api').value,
        venv_python: document.getElementById('set-venv_python').value,
    };
    // ★ 2026-10-19: 顺手迁移到 api 层（统一超时/重试/错误 ✓）
    const r = await api.tryPut('/settings', payload);
    if (r.ok) showToast('设置已保存', true);
    else showToast('保存失败' + (r.error ? '：' + r.error : ''), false);
}
