/* 学习增强 —— 对话页的开关与设置面板（外置 ✓）
 *
 * 为什么外置：内联 <script> 会被前端测试台抽出来在 vm 里跑 ✗ 实测弄挂过测试 ✓
 * 为什么挂在底座管理旁边：复用 .base-menu-wrap / .base-menu 样式 ✓ 零新增 CSS ✓ 视觉一致 ✓
 * 可用性规则（用户要求 ✓）：只有所选模型是【本地】(provider == 'vllm') 时开关才可选 ✓
 */
(function () {
  try {
    var CFG = null;

    function $(id) { return document.getElementById(id); }
    function api(p, body) {
      // ★ 修潜伏 bug: 顶层 const 外部读不到 ✗ → 优先 window.API_BASE ✓ 兜底后端地址 ✓
      var base = (window.API_BASE)
        || ((typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '')
        || 'http://127.0.0.1:8001';
      return fetch(base + p, {
        method: body === undefined ? 'GET' : 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body)
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; },
                                                        function () { return { ok: r.ok, d: {} }; }); });
    }
    function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]; }); }

    // 是否为本地模型：直接看 <select> 的 value 前缀 ✓
    // ✗ 不能读主脚本的 chatModels —— <script> 顶层 let 是脚本作用域，不挂 window，跨文件读不到
    //   （实测就栽在这：明明选 vllm:base 却显示"不可用"）
    // 模型 id 本身带 provider 前缀：vllm:base / vllm:lora:x = 本地；openai:… / ollama:… = 云端 ✓
    function currentModelId() {
      var sel = $('chat-model-select');
      return sel ? String(sel.value || '') : '';
    }
    var _baseIsLocal = null;      // 后端 /vllm/status 的结论（兜底 ✓）
    function isLocal() {
      var id = currentModelId();
      if (id.indexOf('vllm') === 0 || id.indexOf('local') === 0) return true;
      // ★ 下拉第一项「（不挂 LoRA · 用当前底座）」以前 value='' ✗ → 被判成"不可用"✗
      //   现在 value='vllm:base' ✓ 这里再加兜底: 空值时用【后端底座状态】判断 ✓
      if (!id && _baseIsLocal === true) return true;
      if (id.indexOf('openai') === 0 || id.indexOf('ollama') === 0) return false;
      return _baseIsLocal === true;      // 未知时: 底座在跑就算本地 ✓
    }
    function probeBase() {
      try {
        fetch(((window.API_BASE) || 'http://127.0.0.1:8001') + '/vllm/status')
          .then(function (r) { return r.json(); })
          .then(function (d) {
            _baseIsLocal = !!d.running;
            window.updateLearnAvailability();
          }).catch(function () {});
      } catch (e) {}
    }

    // 选云模型 → 开关置灰 + 提示（用户要求 ✓）
    window.updateLearnAvailability = function () {
      var btn = $('learn-btn'), hint = $('learn-hint'), box = $('learn-enabled');
      var local = isLocal();
      var enabled = !!(CFG && CFG.enabled);
      if (btn) {
        btn.style.opacity = local ? '1' : '0.5';
        btn.title = local ? '学习增强（仅本地模型可用）'
                          : '学习增强只在用本地模型时有意义：本地答不出来才需要大模型接手';
        var st = $('learn-state');
        if (st) { st.textContent = local ? (enabled ? '开' : '关') : '不可用'; }
      }
      if (box) { box.disabled = !local; }
      if (hint) {
        hint.style.display = local ? 'none' : 'block';
        hint.textContent = '学习增强只在用本地模型时有意义：本地答不出来才需要大模型接手。';
      }
    };

    window.toggleLearnMenu = function () {
      var m = $('learn-submenu');
      if (!m) { return; }
      if (m.style.display === 'none' || !m.style.display) {
        window.loadLearnConfig();
        m.style.display = 'block';
      } else {
        m.style.display = 'none';
      }
    };

    function fill(cfg) {
      CFG = cfg;
      function set(id, v) { var e = $(id); if (e) { if (e.type === 'checkbox') { e.checked = !!v; } else { e.value = v; } } }
      set('learn-enabled', cfg.enabled);
      set('learn-sync', cfg.sync_train);
      set('learn-every', cfg.train_every);
      set('learn-prefix', cfg.lora_prefix);
      set('learn-kb-mode', cfg.kb_mode);
      set('learn-kb-name', cfg.kb_name);
      set('learn-timeout', cfg.timeout_sec);
      set('learn-escalate', cfg.escalate_model);
      set('learn-build-kb', cfg.build_kb);
      set('learn-distill', cfg.distill_model);
      var r = document.querySelector('input[name="learn-kbmode"][value="' + (cfg.kb_mode || 'new') + '"]');
      if (r) { r.checked = true; }
      window.updateLearnAvailability();
    }

    window.loadLearnConfig = function () {
      return api('/chat/learn/config').then(function (r) {
        fill((r.d && r.d.config) || {});
        return r.d;
      });
    };

    window.saveLearnConfig = function () {
      var g = function (id) { var e = $(id); return e ? (e.type === 'checkbox' ? e.checked : e.value) : undefined; };
      var mode = (document.querySelector('input[name="learn-kbmode"]:checked') || {}).value || 'new';
      var body = {
        enabled: g('learn-enabled'),
        sync_train: g('learn-sync'),
        train_every: parseInt(g('learn-every'), 10) || 50,
        lora_prefix: g('learn-prefix'),
        kb_mode: mode,
        kb_name: g('learn-kb-name'),
        timeout_sec: parseInt(g('learn-timeout'), 10) || 300,
        escalate_model: g('learn-escalate'),
        build_kb: g('learn-build-kb'),
        distill_model: g('learn-distill')
      };
      return api('/chat/learn/config', body).then(function (r) {
        if (r.ok) { fill((r.d && r.d.config) || body); }
        var t = $('learn-saved');
        if (t) { t.textContent = r.ok ? '已保存 ✓' : '保存失败 ✗'; setTimeout(function () { t.textContent = ''; }, 2500); }
        return r;
      });
    };

    // 聊天请求要的 learn 负载（未开启或非本地 → 返回 null ✓ 后端行为不变 ✓）
    window.__learnPayload = function () {
      try {
        if (!CFG || !CFG.enabled || !isLocal()) { return null; }
        return {
          enabled: true,
          timeout_sec: parseInt(CFG.timeout_sec, 10) || 300,
          escalate_model: (CFG.escalate_model || ''),
          escalate_provider: (CFG.escalate_provider || 'openai'),
          distill_model: (CFG.distill_model || ''),
          build_kb: !!CFG.build_kb,
          kb_mode: CFG.kb_mode || 'new',
          kb_name: CFG.kb_name || 'learned',
          sync_train: !!CFG.sync_train,
          train_every: parseInt(CFG.train_every, 10) || 50,
        };
      } catch (e) { return null; }
    };

    // 选模型时同步可用性（不动原有 onchange ✓ 另加监听 ✓）
    function hook() {
      var sel = $('chat-model-select');
      if (sel && !sel.__learnHooked) {
        sel.__learnHooked = true;
        sel.addEventListener('change', function () { setTimeout(window.updateLearnAvailability, 50); });
      }
    }

    function boot() {
      if (!$('learn-submenu')) { return; }        // 不在这个页面/测试环境 → 什么都不做 ✓
      hook();
      window.loadLearnConfig().then(function () { hook(); });
      setInterval(function () { hook(); probeBase(); window.updateLearnAvailability(); }, 3000);
    setTimeout(probeBase, 1200);     // ★ 页面一开就探一次底座状态 ✓
    }
    setTimeout(boot, 1200);
  } catch (e) {
    // 测试台/异常环境静默 ✓ 绝不影响其它脚本 ✓
  }
})();
