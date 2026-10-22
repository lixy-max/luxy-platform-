// 底座页（E-1 拆分 · 2026-10-19）
// 从 index3.html 抽出 ✓ 依赖全局：api / showToast / escapeHtml / API_BASE 等 ✓
// 顶层 function 声明 ⇒ 浏览器里自动成为 window 属性 ✓（onclick 才能调到 ✓）
'use strict';

async function deleteLora(name) {
    if (!confirm(`确定删除 LoRA '${name}' 吗？`)) return;
    const res = await fetch(`${API_BASE}/loras/${name}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok) { showToast(data.message, true); fetchAvailable(); }
    else { showToast(data.detail || '失败', false); }
}
async function uploadLora() {
    const file = document.getElementById('fileInput').files[0];
    if (!file) return;
    const name = prompt("请输入 LoRA 名称：");
    if (!name) return;
    const fd = new FormData();
    fd.append('file', file);
    fd.append('lora_name', name);
    try {
        const res = await fetch(`${API_BASE}/loras/upload`, { method: 'POST', body: fd });
        const data = await res.json();
        if (res.ok) { showToast(data.message, true); fetchAvailable(); }
        else { showToast(data.detail || '失败', false); }
    } catch (e) { showToast('上传失败', false); }
    finally { document.getElementById('fileInput').value = ''; }
}
