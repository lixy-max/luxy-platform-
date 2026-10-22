// 训练页（E-1 拆分 · 2026-10-19）
// 从 index3.html 抽出 ✓ 依赖全局：api / showToast / escapeHtml / API_BASE 等 ✓
// 顶层 function 声明 ⇒ 浏览器里自动成为 window 属性 ✓（onclick 才能调到 ✓）
'use strict';

async function cancelTraining() {
    if (!reviewTaskId) return;
    if (!confirm('确定取消这次训练？')) return;
    await fetch(`${API_BASE}/training/${reviewTaskId}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'cancel' })
    });
    document.getElementById('review-modal').style.display = 'none';
    showToast('已取消', true);
    reviewTaskId = null;
}
async function startTraining() {
    const loraName = document.getElementById('train-lora-name').value.trim();
    const taskDesc = document.getElementById('train-task-desc').value.trim();
    const formatHint = document.getElementById('train-format-hint').value.trim();
    const dataCount = parseInt(document.getElementById('train-count').value);
    const baseModel = document.getElementById('train-base-model').value;
    const providerType = document.getElementById('train-provider').value;
    const autoRestart = document.getElementById('train-auto-restart').checked;

    const dataSource = document.getElementById('train-data-source').value;
    const reviewBeforeTrain = document.getElementById('train-review').checked;

    if (!loraName) { showToast('请填写 LoRA 名称', false); return; }
    if (dataSource === 'generate') {
        if (!taskDesc) { showToast('生成新数据需要填写任务需求', false); return; }
        if (!dataCount || dataCount < 10) { showToast('数据条数至少 10', false); return; }
    }
    if (!baseModel) { showToast('请选择训练底座', false); return; }

    let datasetFile = null;
    if (dataSource === 'reuse') {
        datasetFile = document.getElementById('train-dataset-select').value;
        if (!datasetFile) { showToast('请选择数据集', false); return; }
    } else if (dataSource === 'upload') {
        datasetFile = await uploadDatasetIfNeeded();
        if (!datasetFile) { showToast('上传失败或取消', false); return; }
    }
    const providerCfg = providerConfigs[providerType] || {};
    let providerConfig = {};
    if (providerType === 'vllm') {
        providerConfig = { vllm: { api_base: providerCfg.api_base || 'http://127.0.0.1:8000/v1', model: providerCfg.model || baseModel } };
    } else if (providerType === 'ollama') {
        providerConfig = { ollama: { api_base: providerCfg.api_base || 'http://127.0.0.1:11434', model: providerCfg.model || 'qwen2.5:7b' } };
    } else if (providerType === 'openai') {
        if (!providerCfg.api_key && !providerCfg.api_key_set) { showToast('云 API 未配置 Key', false); return; }
        // api_key 不再由前端提供：留空即可，后端会从 settings 注入
        providerConfig = { openai: { api_base: providerCfg.api_base, model: providerCfg.model, api_key: providerCfg.api_key || '' } };
    }

    if (!confirm(`启动训练：\nLoRA: ${loraName}\n条数: ${dataCount}\n底座: ${baseModel.split('/').pop()}\nProvider: ${providerType}\n\n继续？`)) return;

    const logBox = document.getElementById('train-log');
    const stageEl = document.getElementById('train-stage');
    const statusEl = document.getElementById('train-status');
    const btn = document.getElementById('btn-start-train');

    logBox.textContent = '📤 提交训练任务...\n';
    stageEl.textContent = '准备中...';
    statusEl.innerHTML = '<span class="status-badge status-running">运行中</span>';
    btn.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/training/start`, {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                lora_name: loraName, task_desc: taskDesc, format_hint: formatHint,
                data_count: dataCount, base_model: baseModel,
                provider_type: providerType, provider_config: providerConfig,
                auto_restart_vllm: autoRestart,
                dataset_file: datasetFile,
                review_before_train: reviewBeforeTrain && dataSource === 'generate'
            })
        });
        const data = await res.json();
        if (!res.ok) {
            logBox.textContent += `❌ 启动失败：${data.detail}\n`;
            stageEl.textContent = '启动失败';
            statusEl.innerHTML = '<span class="status-badge status-error">失败</span>';
            btn.disabled = false;
            return;
        }
        const taskId = data.task_id;
        logBox.textContent += `✅ 任务 ID: ${taskId}\n\n`;
        if (trainingTimer) clearInterval(trainingTimer);
        trainingTimer = setInterval(() => pollTraining(taskId, btn), 3000);
    } catch (e) {
        logBox.textContent += `❌ 网络错误：${e}\n`;
        statusEl.innerHTML = '<span class="status-badge status-error">失败</span>';
        btn.disabled = false;
    }
}
