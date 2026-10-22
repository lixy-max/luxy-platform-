/* 🎭 角色对话（精简版 ✓ 按用户新方案重写）
 *
 * 设计（用户定的 ✓）:
 *   · 只有一个【角色下拉】（目前只有孙悟空 ✓）
 *   · 选角色 → 【自动】挂它绑定的 LoRA + 知识库 ✓（不用手选 ✗）
 *   · 模型【跟随全局底座】✓ 不在这里切底座/云模型 ✗
 *   · 左侧: ＋新建会话 + 历史对话 ✓（用 mode="role" 与普通对话隔离 ✓）
 *   · ✗ 不设全局默认（xiyouji 只在这个页面按角色自动挂 ✓）
 *
 * 角色从哪来: GET /roles ✓ = registry.json 里带 agent_role 字段的 LoRA ✓
 *   （加角色只改配置 ✓ 代码不用动 ✓）
 */
(function () {
  // ★ 优先 window.API_BASE（index3.html 显式挂的 ✓）—— 顶层 const 外部读不到 ✗
  var API = window.API_BASE
    || ((typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '')
    || 'http://127.0.0.1:8001';
  var roles = [];
  var sessionId = null;
  var history = [];
  var streaming = false;
  var learnCfg = null;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
    });
  }
  function jget(p) { return fetch(API + p).then(function (r) { return r.json(); }); }
  function jpost(p, b) {
    return fetch(API + p, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(b || {}) }).then(function (r) { return r.json(); });
  }

  function currentRole() {
    var sel = $('rc-role');
    if (!sel) return null;
    for (var i = 0; i < roles.length; i++) { if (roles[i].lora === sel.value) return roles[i]; }
    return null;
  }

  // ---------- 角色下拉（数据来自 /roles ✓）
  window.rcLoadRoles = function () {
    var sel = $('rc-role');
    if (!sel) return Promise.resolve();
    return jget('/roles').then(function (d) {
      roles = (d.roles || []).filter(function (r) { return r.exists; });
      var html = '';
      roles.forEach(function (r) {
        var dis = (r.compatible === false) ? ' disabled' : '';
        var tag = (r.compatible === false) ? '（与当前底座不兼容）' : '';
        html += '<option value="' + esc(r.lora) + '"' + dis + '><svg class="icon icon-sm"><use href="vendor/icons.svg#drama"></use></svg> ' + esc(r.name) + tag + '</option>';
      });
      sel.innerHTML = html || '<option value="">（还没有角色）</option>';
      rcOnRoleChange();
    }).catch(function () { sel.innerHTML = '<option value="">（读取角色失败）</option>'; });
  };

  window.rcOnRoleChange = function () {
    var r = currentRole();
    // ★ 记住选的角色 + 对账真实加载状态（重启后 vLLM 里没有它 ✗ 要自动挂 ✓）
    if (r && window.rememberLora) {
      window.rememberLora(r.lora);
      if (window.ensureLoraLoaded) window.ensureLoraLoaded(r.lora);
    }
    var line = $('rc-role-line');
    if (line) {
      line.textContent = r
        ? ('已自动挂载：LoRA ' + r.lora + (r.kb ? ' + 知识库 ' + r.kb : '') + '　（模型跟随全局底座）')
        : '';
    }
    if (!sessionId) rcResetMessages();
  };

  function rcResetMessages() {
    var box = $('rc-messages');
    if (!box) return;
    var r = currentRole();
    box.innerHTML = '<div class="chat-empty">' + (r ? ('<svg class="icon icon-sm"><use href="vendor/icons.svg#drama"></use></svg> 正在和「' + esc(r.name) + '」聊天') : '<svg class="icon icon-sm"><use href="vendor/icons.svg#drama"></use></svg> 请先在左上角选择角色')
      + '<br><span style="font-size:12px;">角色自带说话方式（LoRA）与事实记忆（知识库）</span></div>';
  }

  // ---------- 会话（mode=role ✓ 与普通对话隔离 ✓）
  window.rcLoadSessions = function () {
    var box = $('rc-session-list');
    if (!box) return Promise.resolve();
    return jget('/chat/sessions?mode=role&limit=50').then(function (d) {
      var ss = d.sessions || [];
      if (!ss.length) { box.innerHTML = '<div style="opacity:.55;font-size:12px;padding:6px;">（还没有历史对话）</div>'; return; }
      box.innerHTML = ss.map(function (s) {
        var on = (s.session_id === sessionId);
        return '<div class="rc-sess" data-id="' + esc(s.session_id) + '"'
          + ' style="cursor:pointer;padding:6px 8px;border-radius:6px;font-size:13px;'
          + (on ? 'background:#e8f5e9;font-weight:600;' : '') + '">'
          + '💬 ' + esc(s.title || '新对话')
          + '<span style="opacity:.5;font-size:11px;"> ×' + (s.message_count || 0) + '</span>'
          + '</div>';
      }).join('');
      Array.prototype.forEach.call(box.querySelectorAll('.rc-sess'), function (el) {
        el.onclick = function () { rcOpenSession(el.getAttribute('data-id')); };
      });
    }).catch(function () {});
  };

  window.rcNewSession = function () {
    sessionId = null;
    history = [];
    rcResetMessages();
    rcLoadSessions();
    var i = $('rc-input'); if (i) i.focus();
  };

  window.rcOpenSession = function (sid) {
    return jget('/chat/sessions/' + encodeURIComponent(sid)).then(function (d) {
      var s = d.session || d;
      sessionId = s.session_id || sid;
      history = [];
      var box = $('rc-messages');
      if (box) box.innerHTML = '';
      (s.messages || []).forEach(function (m) {
        if (m.role === 'user' || m.role === 'assistant') {
          history.push({ role: m.role, content: m.content });
          bubble(m.role, m.content);
        }
      });
      if (!(s.messages || []).length) rcResetMessages();
      rcLoadSessions();
      // 恢复该会话用的角色（如果还在 ✓）
      var sel = $('rc-role');
      if (sel && s.model && roles.some(function (r) { return r.lora === s.model; })) {
        // model 里存的是 vllm:lora:xxx ✓ 取末段 ✓
        var nm = String(s.model).split(':').pop();
        if (roles.some(function (r) { return r.lora === nm; })) sel.value = nm;
        rcOnRoleChange();
      }
    }).catch(function () {});
  };

  function persist(role, content) {
    if (!sessionId) {
      var r = currentRole();
      var title = content.slice(0, 18) || '新对话';
      return jpost('/chat/sessions', { title: title, mode: 'role',
                                       model: r ? ('vllm:lora:' + r.lora) : null })
        .then(function (d) {
          sessionId = (d.session || {}).session_id || null;
          // ★ 接口要的是批量数组 {messages:[…]} ✗ 传单个对象会 422 ✓
          if (sessionId) return jpost('/chat/sessions/' + sessionId + '/messages',
                                      { messages: [{ role: role, content: content }] });
        }).then(function () { rcLoadSessions(); }).catch(function () {});
    }
    return jpost('/chat/sessions/' + sessionId + '/messages',
                 { messages: [{ role: role, content: content }] })
      .catch(function () {});
  }

  // ---------- 消息气泡（复用同款样式变量 ✓）
  function bubble(role, text) {
    var box = $('rc-messages');
    if (!box) return null;
    var empty = box.querySelector('.chat-empty');
    if (empty) empty.remove();
    var wrap = document.createElement('div');
    var mine = (role === 'user');
    wrap.style.cssText = 'display:flex;width:100%;'
      + (mine ? 'justify-content:flex-end;' : 'justify-content:flex-start;');
    var b = document.createElement('div');
    b.style.cssText = 'max-width:78%;padding:10px 14px;border-radius:12px;white-space:pre-wrap;'
      + 'line-height:1.7;font-size:14px;'
      + (mine ? 'background:var(--c-primary,#2f6fed);color:#fff;'
              : 'background:var(--c-bg-white,#fff);border:1px solid var(--c-border-soft,#e5e5e5);');
    b.textContent = text;
    wrap.appendChild(b);
    box.appendChild(wrap);
    box.scrollTop = box.scrollHeight;
    return b;
  }

  // ---------- 发送（角色自动带上 LoRA + 知识库 ✓）
  window.rcSend = function () {
    if (streaming) return;
    var r = currentRole();
    if (!r) { if (typeof showToast === 'function') showToast('请先选择角色', false); return; }
    var input = $('rc-input');
    var text = input ? input.value.trim() : '';
    if (!text) return;
    input.value = '';
    bubble('user', text);
    history.push({ role: 'user', content: text });
    persist('user', text);
    var holder = bubble('assistant', '…');
    var acc = '';
    streaming = true;

    var body = {
      provider: 'vllm',                       // ★ 跟随全局底座 ✓ 不在这里切 ✗
      lora: r.lora,                           // ★ 角色的 LoRA（后端会据此注入 persona ✓）
      messages: history.slice(-20),
      temperature: 0.8, max_tokens: 512,
      kb: r.kb ? { name: r.kb, top_k: 5, query: text } : null,   // ★ top_k=5（有主语重排保护后 ✓ 5 比 3 好 ✓）
      learn: (window.__learnPayload ? window.__learnPayload() : null)
    };
    fetch(API + '/chat/completions/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    }).then(function (res) {
      if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
      var reader = res.body.getReader(), dec = new TextDecoder(), buf = '';
      function pump() {
        return reader.read().then(function (x) {
          if (x.done) {
            streaming = false;
            history.push({ role: 'assistant', content: acc });
            if (acc) persist('assistant', acc);
            if (!acc && holder) holder.textContent = '（没有回复）';
            return;
          }
          buf += dec.decode(x.value, { stream: true });
          var parts = buf.split('\n\n');
          buf = parts.pop();
          parts.forEach(function (line) {
            if (line.indexOf('data: ') !== 0) return;
            var d; try { d = JSON.parse(line.slice(6)); } catch (e) { return; }
            if (d.delta) { acc += d.delta; if (holder) holder.textContent = acc; }
            if (d.handoff && holder) { acc += '\n\n⏱️ 本地未响应，已由【' + (d.handoff.to || '云模型') + '】接手\n\n'; holder.textContent = acc; }
            if (d.error) { acc += '\n[错误] ' + d.error; if (holder) holder.textContent = acc; }
          });
          var box = $('rc-messages');
          if (box) box.scrollTop = box.scrollHeight;
          return pump();
        });
      }
      return pump();
    }).catch(function (e) {
      streaming = false;
      if (holder) holder.textContent = '✗ ' + (e && e.message ? e.message : e);
    });
  };

  // ---------- 学习增强（读全局配置 ✓ 未开=null ✓ 不改后端行为 ✓）
  function loadLearn() {
    jget('/chat/learn/config').then(function (d) {
      learnCfg = (d && d.config) || {};
      window.__learnPayload = function () {
        try {
          if (!learnCfg || !learnCfg.enabled) return null;
          return { enabled: true, timeout_sec: learnCfg.timeout_sec || 300,
                   escalate_model: learnCfg.escalate_model || '', escalate_provider: 'openai',
                   build_kb: !!learnCfg.build_kb, kb_mode: learnCfg.kb_mode || 'new',
                   kb_name: learnCfg.kb_name || 'learned', sync_train: !!learnCfg.sync_train,
                   train_every: learnCfg.train_every || 50 };
        } catch (e) { return null; }
      };
    }).catch(function () {});
  }

  function boot() {
    if (!$('page-rolechat')) return;
    var input = $('rc-input');
    if (input && !input.__rcHooked) {
      input.__rcHooked = true;
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); window.rcSend(); }
      });
    }
    if (!roles.length) rcLoadRoles();
    if (!$('rc-session-list').__rcLoaded) {
      $('rc-session-list').__rcLoaded = true;
      rcLoadSessions();
    }
    if (!learnCfg) loadLearn();
  }
  setTimeout(boot, 1600);
  setInterval(function () { if ($('page-rolechat')) boot(); }, 4000);
})();
