// 前端统一 API 层（Phase 1-1 · 2026-10-19）
//
// 为什么需要（实测取证 ✓）：
//   index3.html 里有 168 处裸 fetch ✗，其中 50 处 POST；无统一超时/重试 ✗（AbortController=0 ✗）
//   ⇒ 每个调用点自己写错误处理 ✓ 写法不一致 ✗ 超时/网络抖动各写各的 ✗
//
// 设计原则：
//   ① **纯增量**：老代码不改也能跑 ✓（只有迁移到的调用点才用新 API ✓）
//   ② 两套语义：
//      · `api.request/get/post`  —— 失败 **抛 ApiError** ✓（配合 try/catch ✓）
//      · `api.tryGet/tryPost`    —— 失败 **不抛**，返回 `{ok, data, error, status}` ✓
//        用于替换原来"`if (!r.ok) return`"的写法 ✓ 行为不变 ✓
//   ③ 统一能力：超时（默认 60s ✓ 可配 ✓）· 网络错误/5xx 自动重试（默认 0 次 ✓ 可配 ✓）·
//      统一错误信息（后端 detail/error ✓）· JSON 解析容错 ✓
//   ④ 显式声明 **不改** 关键路径：agent 循环 `/chat/tools`、流式对话暂不迁移 ✗（风险高 ✓）
//
// 用法：
//   const d = await api.get('/kb/list');
//   const d = await api.post('/sim/run', { node: 'math_eval', args: { expr: '1+1' } });
//   const { ok, data, error } = await api.tryGet('/docs/tool/list');
//
// 依赖：window.API_BASE（由 index3.html 主脚本设置 ✓）
(function () {
  'use strict';

  var DEFAULT_TIMEOUT = 60000;   // 60s（本地后端足够 ✓）
  var RETRY_DELAY = 800;

  function base() {
    try { return window.API_BASE || (window.location.origin.replace(/:8080$/, ':8001')) || 'http://127.0.0.1:8001'; }
    catch (e) { return 'http://127.0.0.1:8001'; }
  }

  function ApiError(message, status, body, url) {
    var e = new Error(message);
    e.name = 'ApiError';
    e.status = status || 0;
    e.body = body || null;
    e.url = url || '';
    return e;
  }

  function errorText(status, body) {
    if (body && typeof body === 'object') {
      var m = body.detail || body.error || body.message;
      if (m) {
        if (typeof m === 'string') return m;
        try { return JSON.stringify(m).slice(0, 300); } catch (e) { return '请求失败'; }
      }
    }
    if (typeof body === 'string' && body.trim()) return body.trim().slice(0, 300);
    return 'HTTP ' + status;
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  /**
   * 统一请求（失败抛 ApiError ✓）
   * @param {string} path   '/kb/list' 或完整 URL
   * @param {object} [opts] { method, body, headers, timeout, retry, raw }
   */
  async function request(path, opts) {
    opts = opts || {};
    var url = /^https?:\/\//i.test(String(path)) ? String(path) : base() + String(path);
    var method = (opts.method || 'GET').toUpperCase();
    var timeout = (opts.timeout == null) ? DEFAULT_TIMEOUT : opts.timeout;
    var retry = opts.retry == null ? 0 : Math.max(0, opts.retry | 0);
    var headers = Object.assign({}, opts.headers || {});
    var body = null;

    if (opts.body !== undefined && opts.body !== null) {
      if (typeof opts.body === 'string') {
        body = opts.body;
      } else if (typeof FormData !== 'undefined' && opts.body instanceof FormData) {
        body = opts.body;                       // 让浏览器自己设 Content-Type ✓
      } else {
        headers['Content-Type'] = headers['Content-Type'] || 'application/json';
        body = JSON.stringify(opts.body);
      }
    }

    var lastErr = null;
    for (var attempt = 0; attempt <= retry; attempt++) {
      if (attempt > 0) await sleep(RETRY_DELAY * attempt);
      var ac = null, tid = null;
      try {
        if (typeof AbortController !== 'undefined') {
          ac = new AbortController();
          if (timeout > 0) tid = setTimeout(function () { try { ac.abort(); } catch (e) {} }, timeout);
        }
        var resp = await fetch(url, {
          method: method,
          headers: headers,
          body: body,
          signal: ac ? ac.signal : undefined,
          cache: 'no-store'
        });
        if (tid) { clearTimeout(tid); tid = null; }

        // 解析（容错：非 JSON 不炸 ✓）
        // ★ 2026-10-19: 兼容 Response-like mock（有的测试桩只有 .json() 没有 .text() ✓）
        var text = '';
        var data = null;
        try {
          if (typeof resp.text === 'function') text = await resp.text();
        } catch (e) { text = ''; }
        if (text) {
          try { data = JSON.parse(text); } catch (e) { data = text; }
        } else if (typeof resp.json === 'function') {
          try { data = await resp.json(); } catch (e) { data = null; }
        }

        if (opts.raw) return { ok: resp.ok, status: resp.status, data: data, text: text };

        if (!resp.ok) {
          var ae = ApiError(errorText(resp.status, data), resp.status, data, url);
          // 5xx 可重试 ✓；4xx 不重试（参数/权限问题重试无用 ✗）
          if (resp.status >= 500 && attempt < retry) { lastErr = ae; continue; }
          throw ae;
        }
        return data;
      } catch (e) {
        if (tid) { clearTimeout(tid); tid = null; }
        if (e && e.name === 'ApiError') throw e;
        // 网络错误 / 超时 ⇒ 可重试 ✓
        var msg = (e && e.name === 'AbortError')
          ? ('请求超时（' + timeout + 'ms）: ' + url)
          : ('网络错误（' + (e && e.message ? e.message : e) + '）: ' + url);
        lastErr = ApiError(msg, 0, null, url);
        if (attempt >= retry) throw lastErr;
      }
    }
    throw lastErr || ApiError('未知请求错误', 0, null, url);
  }

  /** 不抛版：返回 {ok, data/error, status} ✓（用于替换 "if (!r.ok) return" 老写法 ✓） */
  async function attempt(fn) {
    try {
      var data = await fn();
      return { ok: true, data: data, error: null, status: 200 };
    } catch (e) {
      return { ok: false, data: null, status: (e && e.status) || 0,
               error: (e && e.message) || String(e), body: e && e.body };
    }
  }

  var api = {
    base: base,
    request: request,
    get: function (p, o) { return request(p, Object.assign({}, o || {}, { method: 'GET' })); },
    post: function (p, b, o) { return request(p, Object.assign({}, o || {}, { method: 'POST', body: b })); },
    put: function (p, b, o) { return request(p, Object.assign({}, o || {}, { method: 'PUT', body: b })); },
    del: function (p, o) { return request(p, Object.assign({}, o || {}, { method: 'DELETE' })); },
    tryGet: function (p, o) { return attempt(function () { return api.get(p, o); }); },
    tryPost: function (p, b, o) { return attempt(function () { return api.post(p, b, o); }); },
    tryPut: function (p, b, o) { return attempt(function () { return api.put(p, b, o); }); },
    tryDel: function (p, o) { return attempt(function () { return api.del(p, o); }); },
    ApiError: ApiError,
    version: '1.0.0'
  };

  window.api = api;
  try { console.debug('[api] 统一 API 层已加载 v' + api.version + ' · base=' + base()); } catch (e) {}
})();
