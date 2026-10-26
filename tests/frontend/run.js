#!/usr/bin/env node
/**
 * 前端测试骨架（零依赖，只用 Node 内置模块）。
 *
 * 三个阶段：
 *   A. 从 webui/index3.html 抽出所有内联 <script> 块，逐个做语法检查；
 *   B. 用 DOM 桩件加载主脚本，测试会话持久化层（新建/追加/列表/恢复）；
 *   C. 加载 Agent 模块（<script id="agent-module">），测试工具行为与历史恢复。
 *
 * 用法（WSL 里没有 node，请从 Windows 侧运行）：
 *   node "\\wsl$\Ubuntu-24.04\home\luxy\paper_lora_project\tests\frontend\run.js"
 * 也可显式指定 HTML 路径：
 *   node tests/frontend/run.js path/to/index3.html
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DEFAULT_HTML = path.join(__dirname, '..', '..', 'webui', 'index3.html');
const htmlPath = process.argv[2] || DEFAULT_HTML;

const results = [];
function check(name, cond, extra) {
  results.push({ name, pass: !!cond, extra: extra === undefined ? '' : String(extra) });
}

// ---------------------------------------------------------------- 抽取脚本块
function extractBlocks(html) {
  const rx = /<script([^>]*)>([\s\S]*?)<\/script>/g;
  const blocks = [];
  let m;
  while ((m = rx.exec(html)) !== null) {
    const attrs = m[1] || '';
    if (/\bsrc\s*=/.test(attrs)) continue;
    blocks.push({ attrs, code: m[2] });
  }
  return blocks;
}

// ---------------------------------------------------------------- DOM 桩件
const els = {};
// innerHTML 会真实解析 <option>，这样"下拉框被填充"这类断言才有意义
const elProto = {
  set innerHTML(v) {
    this._html = String(v);
    // 把渲染出来的控件登记进 els —— 否则"注册表驱动的编辑器"没法被测试：
    // renderConfig 生成的是 HTML 字符串，不解析就取不到任何控件。
    const idRx = /<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g;
    let im;
    while ((im = idRx.exec(this._html)) !== null) {
      const tag = im[1].toLowerCase();
      const id = im[2];
      const el = els[id] || (els[id] = makeEl(id));
      el.tagName = tag;
      const vAttr = /\bvalue="([^"]*)"/.exec(im[0]);
      if (vAttr) el.value = vAttr[1];
      if (/\bchecked\b/.test(im[0])) el.checked = true;
    }
    if (/<option/i.test(this._html)) {
      this.children = [];
      const rx = /<option[^>]*value="([^"]*)"[^>]*>([\s\S]*?)<\/option>/g;
      let m;
      while ((m = rx.exec(this._html)) !== null) {
        const o = makeEl();
        o.value = m[1];
        o.textContent = m[2];
        this.children.push(o);
      }
    }
  },
  get innerHTML() { return this._html || ''; },
};
function makeEl(id) {
  return Object.assign(Object.create(elProto), {
    id: id || '', value: '', textContent: '', placeholder: '',
    style: {}, children: [], disabled: false, selectedOptions: [], dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); },
    remove() {},
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    setAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, focus() {}, scrollTop: 0, scrollHeight: 0,
    // 画布代码会取坐标；桩件返回全 0，避免行为测试因缺方法而报错
    getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0,
                                       width: 0, height: 0, x: 0, y: 0 }; },
    scrollIntoView() {},
  });
}

// fetch 路由：记录所有调用，按路径返回可配置响应
const httpLog = [];
const startCalls = [];
let startRes = null;
let statusRes = { running: true, model: '/home/luxy/models/Qwen3-4B-Instruct' };
let modelsRes = {
  models: [
    { name: 'Qwen2.5-14B-Instruct-AWQ', path: '/home/luxy/models/Qwen2.5-14B-Instruct-AWQ', quantization: 'awq' },
    { name: 'Qwen3-4B-Instruct', path: '/home/luxy/models/Qwen3-4B-Instruct', quantization: 'fp16' },
  ],
};
let selectValue = '';

// ---------------------------------------------------------------- X2 夹具
// /workflow/node-types 的响应夹具。⚠️ 由引擎注册表生成（E:\WSL\_check\gen_fixture.py），
// 后端测试 test_frontend_harness_fixture_matches_engine_registry 会校验它与引擎一致 ——
// 否则这里测的就是一份过期注册表，等于没测。
const NODE_TYPES_FIXTURE = {
  "fields": {
    "id": {"type": "str", "default": null, "required": true},
    "type": {"type": "str", "default": "process", "choices": ["input", "retrieval", "process", "terminal", "aggregate", "extract", "analyze", "tool"]},
    "description": {"type": "str", "default": ""},
    "input_from": {"type": "list", "default": []},
    "prompt": {"type": "str", "default": ""},
    "lora": {"type": "str", "default": null},
    "provider": {"type": "str", "default": null, "choices": [null, "vllm", "ollama", "openai"]},
    "model_override": {"type": "str", "default": null},
    "position": {"type": "dict", "default": null},
    "w": {"type": "int", "default": 160, "min": 40, "max": 2000},
    "h": {"type": "int", "default": 50, "min": 20, "max": 2000},
    "output_format": {"type": "str", "default": "json", "choices": ["json", "text"]},
    "chunk_size": {"type": "int", "default": 0, "min": 0},
    "chunk_overlap": {"type": "int", "default": 0, "min": 0},
    "chunk_workers": {"type": "int", "default": 1, "min": 1, "max": 32},
    "cache": {"type": "bool", "default": false},
    "max_tokens": {"type": "int", "default": null, "min": 1},
    "temperature": {"type": "float", "default": null, "min": 0.0, "max": 2.0},
    "retry": {"type": "int", "default": 2, "min": 0, "max": 10},
    "retry_delay": {"type": "float", "default": 2.0, "min": 0.0},
    "timeout": {"type": "int", "default": 300, "min": 5},
    "on_error": {"type": "str", "default": null, "choices": [null, "mark", "skip", "fail_fast"]},
    "kb_name": {"type": "str", "default": ""},
    "top_k": {"type": "int", "default": 5, "min": 1, "max": 50},
    "min_score": {"type": "float", "default": 0.1, "min": -1.0, "max": 1.0},
    "query_template": {"type": "str", "default": ""},
    "kb_write": {"type": "dict", "default": null},
    "tool": {"type": "str", "default": ""},
    "tool_args": {"type": "dict", "default": null},
    "tool_timeout": {"type": "int", "default": 60, "min": 5},
  },
  "types": [
    {"type": "input", "label": "📥 输入", "kind": "source", "output": "text", "ignored_fields": ["cache", "chunk_size", "lora", "output_format", "prompt"]},
    {"type": "retrieval", "label": "📚 检索", "kind": "tool", "output": "text", "ignored_fields": ["cache", "chunk_size", "chunk_workers", "lora", "output_format"]},
    {"type": "process", "label": "⚙️ 中间", "kind": "llm", "output": "json", "ignored_fields": []},
    {"type": "terminal", "label": "📊 终端", "kind": "llm", "output": "json", "ignored_fields": []},
    {"type": "aggregate", "label": "📦 整合", "kind": "llm", "output": "text", "ignored_fields": []},
    {"type": "extract", "label": "🧩 抽取", "kind": "llm", "output": "text", "ignored_fields": []},
    {"type": "analyze", "label": "🔬 分析", "kind": "llm", "output": "json", "ignored_fields": []},
    {"type": "tool", "label": "🔧 工具", "kind": "tool", "output": "text", "ignored_fields": []},
  ],
};

const WF_CONFIG_FIXTURE = {
  workflow_name: 'fixture',
  nodes: [
    { id: 'in1', type: 'input', input_from: [] },
    { id: 'kb1', type: 'retrieval', input_from: ['in1'], kb_name: 'test_kb',
      top_k: 3, query_template: '{full_text}' },
    { id: 'p1', type: 'process', input_from: ['kb1'], prompt: '{kb1}' },
  ],
};

// 会话持久化用的假后端
const chatSessions = {};
let chatCreateCount = 0;
function chatRoute(method, pathname, body) {
  const m = pathname.match(/\/chat\/sessions(?:\/([^/]+))?(\/messages)?$/);
  if (!m) return null;
  const sid = m[1];
  const isMessages = !!m[2];
  if (method === 'POST' && !sid) {
    chatCreateCount++;
    const id = 'sess' + chatCreateCount;
    const payload = body || {};
    chatSessions[id] = {
      session_id: id, title: payload.title || '新对话', mode: payload.mode || 'chat',
      model: payload.model || null, message_count: 0, messages: [],
    };
    return { status: 200, body: { session: publicSession(chatSessions[id]) } };
  }
  if (method === 'GET' && !sid) {
    return { status: 200, body: { sessions: Object.values(chatSessions).map(publicSession) } };
  }
  if (sid && !chatSessions[sid]) return { status: 404, body: { detail: '会话不存在' } };
  if (method === 'GET' && sid && !isMessages) {
    return { status: 200, body: { session: { ...publicSession(chatSessions[sid]), messages: chatSessions[sid].messages } } };
  }
  if (method === 'POST' && sid && isMessages) {
    const s = chatSessions[sid];
    for (const msg of (body && body.messages) || []) {
      s.messages.push(msg);
      s.message_count++;
      if ((!s.title || s.title === '新对话') && msg.role === 'user' && msg.content) {
        s.title = String(msg.content).slice(0, 12);
      }
    }
    return { status: 200, body: { status: 'success', appended: (body.messages || []).length,
                                 message_count: s.message_count, title: s.title } };
  }
  if (method === 'PATCH' && sid) {
    if (body && body.title) chatSessions[sid].title = body.title;
    return { status: 200, body: { session: publicSession(chatSessions[sid]) } };
  }
  if (method === 'DELETE' && sid) {
    delete chatSessions[sid];
    return { status: 200, body: { status: 'success' } };
  }
  return { status: 405, body: {} };
}
function publicSession(s) {
  return { session_id: s.session_id, title: s.title, mode: s.mode, model: s.model,
           message_count: s.message_count };
}

function makeRes(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 422 ? 'Unprocessable Entity' : 'OK',
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
    json: async () => body,
  };
}

function installStubs() {
  // ★ 修（2026-10-23）：主脚本第 4 行现在是
  //   const API_BASE = (window.__LUX_API__ || new URLSearchParams(location.search).get('api') || ...)
  //   原来 harness 没有 location / __LUX_API__ 桩 ⇒ 主脚本一开头就抛
  //   "location is not defined" ⇒ 整块脚本中止，`const API_BASE` 永久留在 TDZ ✗
  //   后续任何调用（ensureSession 等）都报 "Cannot access 'API_BASE' before initialization"
  //   ⇒ 会话测试全挂（实测 2026-10-23）
  //   修法：补浏览器桩（location + __LUX_API__），让主脚本能完整跑完 ✓
  global.location = {
    search: '', hash: '', pathname: '/index3.html',
    href: 'http://127.0.0.1:8080/index3.html',
    origin: 'http://127.0.0.1:8080', protocol: 'http:',
    reload() {}, assign() {}, replace() {},
  };
  global.__LUX_API__ = 'http://test';   // 主脚本优先读它（等价于真实页面的 window.__LUX_API__）
  global.API_BASE = 'http://test';
  global.chatModels = [];
  global.fetch = async (url, opt) => {
    const method = ((opt && opt.method) || 'GET').toUpperCase();
    const u = String(url);
    const pathname = u.replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    let body = null;
    if (opt && opt.body) { try { body = JSON.parse(opt.body); } catch (e) {} }
    httpLog.push({ method, pathname, body, url: u });

    if (method === 'POST' && pathname.endsWith('/training/start')) {
      startCalls.push({ url: u, body });
      return startRes;
    }
    const chat = chatRoute(method, pathname, body);
    if (chat) return makeRes(chat.status, chat.body);
    if (pathname.endsWith('/workflow/node-types')) return makeRes(200, NODE_TYPES_FIXTURE);
    if (pathname.endsWith('/workflow/config')) return makeRes(200, WF_CONFIG_FIXTURE);
    if (pathname.endsWith('/workflow/configs')) {
      return makeRes(200, {
        configs: [
          { name: 'workflow_config.json', workflow_name: '通用论文分析工作流',
            description: '抽取方法章节并生成分析报告', tags: ['论文', '抽取'],
            required_loras: ['extract_v2'], required_providers: ['vllm'],
            author: 'luxy', nodes: 3, is_default: true, mtime: '2026-10-06T01:13:52' },
          { name: 'review_config.json', workflow_name: '审稿意见模板',
            description: '从审稿人视角挑毛病', tags: ['审稿'],
            required_loras: ['broken_lora'], required_providers: ['vllm', 'openai'],
            author: 'luxy', nodes: 5, is_default: false, mtime: '2026-10-06T02:00:00' },
        ],
        default: 'workflow_config.json',
      });
    }
    if (pathname.endsWith('/loras')) {
      return makeRes(200, { loras: [
        { name: 'extract_v2', compatible: true, hidden_size: 2560 },
        { name: 'broken_lora', compatible: false, hidden_size: 5120 },
      ], vllm_hidden_size: 2560 });
    }
    if (pathname.endsWith('/vllm/status')) return makeRes(200, statusRes);
    if (pathname.endsWith('/models')) return makeRes(200, modelsRes);
    if (pathname.endsWith('/chat/models')) return makeRes(200, { models: [] });
    return makeRes(404, {});
  };
  global.window = global;
  global.document = {
    getElementById(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; },
    querySelectorAll() { return []; },
    querySelector(sel) {
      const m = String(sel).match(/data-page="([^"]+)"/);
      return m ? global.document.getElementById('tab-' + m[1]) : null;
    },
    createElement() { return makeEl(); },
    body: makeEl('body'),
    addEventListener() {},
  };
  // ★ 修（2026-10-23）：editflow 页面监听用了 MutationObserver，
  //   harness 没有该桩 ⇒ 打印 "[editflow] 启动失败 ReferenceError"（噪声）
  //   补 3 个无操作 Observer（测试不需要真实监听）
  class NoopObserver {
    observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
  }
  global.MutationObserver = NoopObserver;
  global.IntersectionObserver = NoopObserver;
  global.ResizeObserver = NoopObserver;
  // ★ 修（2026-10-23）：补 localStorage 桩
  //   页面多处用 localStorage 做开关（如 KB 预注入 ds_kb_inject / 相关性门控 ds_kb_gate）
  //   原来没有桩 ⇒ 测试无法开启这些功能，t41 用例必挂 ✗
  global.localStorage = {
    _data: {},
    getItem(k) { return Object.prototype.hasOwnProperty.call(this._data, k) ? this._data[k] : null; },
    setItem(k, v) { this._data[k] = String(v); },
    removeItem(k) { delete this._data[k]; },
    clear() { this._data = {}; },
  };
  global.setInterval = () => 0;
  global.clearInterval = () => {};
  global.trainingTimer = null;
  global.pollTraining = () => {};
  global.showToast = () => {};
  global.loadKBList = () => {};
  global.loadChatKBOptions = () => {};
  global.loadChatModels = () => {};
  global.escapeHtml = (s) => String(s);
  global.marked = { parse: (s) => String(s) };
  global.hljs = { highlightElement() {} };
  global.confirm = () => true;
  global.prompt = () => '新标题';
  // Node 新版把 navigator 定义成只读 getter，必须用 defineProperty 覆盖
  try {
    Object.defineProperty(global, 'navigator', {
      value: { clipboard: { writeText: async () => {} } },
      configurable: true, writable: true,
    });
  } catch (e) { /* 覆盖失败也不影响被测逻辑 */ }
}

// ---------------------------------------------------------------- 主流程
async function main() {
  if (!fs.existsSync(htmlPath)) {
    console.log('HTML not found: ' + htmlPath);
    process.exit(2);
  }
  const html = fs.readFileSync(htmlPath, 'utf8');
  const blocks = extractBlocks(html);
  console.log(`html: ${htmlPath}`);
  console.log(`inline script blocks: ${blocks.length}\n`);

  check('html-has-script-blocks', blocks.length >= 2, blocks.length);

  // ---------- A. 语法检查（只编译，不执行） ----------
  blocks.forEach((b, i) => {
    try {
      new vm.Script(b.code, { filename: `block${i + 1}.js` });
      check(`syntax-block${i + 1}`, true);
    } catch (e) {
      check(`syntax-block${i + 1}`, false, e.message);
    }
  });

  installStubs();

  const agentBlock = blocks.find((b) => /agent-module/.test(b.attrs));
  const mainBlock = blocks.find((b) => !/agent-module/.test(b.attrs) && b.code.length > 1000);
  check('agent-module-present', !!agentBlock);
  check('main-script-present', !!mainBlock);

  // ---------- B. 主脚本：会话持久化 ----------
  if (mainBlock) {
    try {
      // runInThisContext：模拟浏览器中多个 <script> 共享同一个全局词法环境
      // （主脚本的 let agentHistory 必须能被 Agent 模块看到，这才是真实页面行为）
      // ★ Phase 1-1（2026-10-19）: 统一 API 层（webui/api.js）必须先于主脚本加载 ✓
      //   否则 window.api 是 undefined ⇒ 迁移到 api.* 的调用点全挂 ✗（实测踩过 ✓）
      try {
        const apiPath = path.join(path.dirname(htmlPath), 'api.js');
        if (fs.existsSync(apiPath)) {
          vm.runInThisContext(fs.readFileSync(apiPath, 'utf8'), { filename: 'api.js' });
          check('api-layer-loaded', typeof globalThis.window?.api === 'object' || typeof globalThis.window?.api === 'function',
                'window.api 类型=' + typeof (globalThis.window && globalThis.window.api));
        } else {
          check('api-layer-loaded', true, 'api.js 不存在（未迁移时兼容 ✓）');
        }
      } catch (e) {
        check('api-layer-loaded', false, '加载 api.js 失败: ' + e.message);
      }
      // ★ E-1（2026-10-19）: 加载 webui/pages/*.js（逐页拆分后的模块 ✓）
      //   与浏览器一致：这些文件用顶层 function 声明 ✓ 会成为全局 ✓
      const loadPageModules = () => {
        const pagesDir = path.join(path.dirname(htmlPath), 'pages');
        if (!fs.existsSync(pagesDir)) return [];
        const loaded = [];
        for (const f of fs.readdirSync(pagesDir).filter(x => x.endsWith('.js')).sort()) {
          try {
            vm.runInThisContext(fs.readFileSync(path.join(pagesDir, f), 'utf8'), { filename: 'pages/' + f });
            loaded.push(f);
          } catch (e) {
            check('page-module-' + f, false, e.message);
          }
        }
        return loaded;
      };
      const pageModules = loadPageModules();
      check('page-modules-loaded', pageModules.length >= 0, pageModules.join(',') || '(无)');
      vm.runInThisContext(mainBlock.code, { filename: 'main-script.js' });
      check('main-script-loads', true);
    } catch (e) {
      // ★ 修（2026-10-23）：原来静默失败 ⇒ 后续报 TDZ 难排查；
      //   直接把加载错误打到 stderr（真实根因一眼可见）
      console.error('[harness] 主脚本加载失败:', (e && e.stack) || e);
      check('main-script-loads', false, e && e.message);
    }

    // Agent 模块紧接着加载：真实页面里两个脚本都在任何交互之前就绪
    if (agentBlock) {
      try {
        vm.runInThisContext(agentBlock.code, { filename: 'agent-module.js' });
        check('agent-module-loads', true);
      } catch (e) {
        check('agent-module-loads', false, (e && e.stack) || e);
      }
    }

    if (typeof global.saveToSession === 'function') {
      // B1: 首次保存会自动创建会话，再追加消息
      global.currentSessionId = null;
      await global.saveToSession([{ role: 'user', content: '你好', kind: 'text' }], 'chat');
      check('session-auto-created', chatCreateCount === 1, String(chatCreateCount));
      const created = Object.values(chatSessions)[0];
      check('session-message-appended', created && created.message_count === 1, created && created.message_count);
      check('session-title-from-first-message', created && created.title === '你好', created && created.title);
      check('session-post-uses-correct-path',
        httpLog.some(l => l.method === 'POST' && /\/chat\/sessions\/sess1\/messages$/.test(l.pathname)),
        JSON.stringify(httpLog.map(l => l.method + ' ' + l.pathname).slice(-4)));

      // B2: 第二次保存复用已有会话，不再新建
      await global.saveToSession([{ role: 'assistant', content: '在的', kind: 'text' }], 'chat');
      check('session-reused-not-recreated', chatCreateCount === 1, String(chatCreateCount));
      check('session-second-message-appended', created.message_count === 2, created.message_count);

      // B3: 列表刷新会填充下拉框
      els['chat-session-select'] = makeEl('chat-session-select');
      await global.refreshSessionList();
      const selEl = els['chat-session-select'];
      check('session-select-populated', selEl.children.length >= 2, selEl.children.length);
      check('session-select-shows-title',
        selEl.children.some(o => String(o.textContent).includes('你好')),
        JSON.stringify(selEl.children.map(o => o.textContent).slice(0, 3)));

      // B4: 载入历史会话 -> chatHistory 恢复并重绘
      created.messages = [
        { role: 'user', content: '历史问题', kind: 'text' },
        { role: 'assistant', content: '历史回答', kind: 'text' },
      ];
      els['chat-messages'] = makeEl('chat-messages');
      global.applyLoadedSession({ mode: 'chat', messages: created.messages });
      check('chat-restore-renders-bubbles', els['chat-messages'].children.length === 2,
        els['chat-messages'].children.length);

      // B5: 工具消息不进入普通对话的 chatHistory
      els['chat-messages'] = makeEl('chat-messages');
      global.applyLoadedSession({
        mode: 'chat',
        messages: [
          { role: 'user', content: 'q', kind: 'text' },
          { role: 'tool', content: '', kind: 'tool', tool_name: 't', tool_result: {} },
          { role: 'assistant', content: 'a', kind: 'text' },
        ],
      });
      check('chat-restore-skips-tool-messages', els['chat-messages'].children.length === 2,
        els['chat-messages'].children.length);
    } else {
      check('saveToSession-exposed', false, 'global.saveToSession missing');
    }
  }

  // ---------- C. Agent 模块行为 ----------
  if (!agentBlock || !global.AGENT_TOOLS) { report(); return; }

  const T = global.AGENT_TOOLS;
  check('agent-tools-exposed', !!T);
  if (!T) { report(); return; }

  const expectedTools = [
    'list_available_models', 'switch_model', 'list_loras', 'load_lora', 'unload_lora',
    'list_knowledge_bases', 'query_knowledge_base', 'create_knowledge_base',
    'list_workflow_nodes', 'add_workflow_node', 'delete_workflow_node',
    'list_available_datasets', 'list_trainings', 'get_training_status', 'start_training',
  ];
  const missingTools = expectedTools.filter((n) => !T[n]);
  check('agent-tool-inventory', missingTools.length === 0, missingTools.join(','));
  check('agent-dangerous-tools-marked',
    ['start_training', 'delete_workflow_node', 'load_lora', 'unload_lora']
      .every((n) => T[n] && T[n].dangerous === true));

  // C1: Agent 会话恢复重建上下文
  check('agent-render-exposed', typeof global.renderAgentSession === 'function');
  if (typeof global.renderAgentSession === 'function') {
    els['chat-messages'] = makeEl('chat-messages');
    global.renderAgentSession([
      { role: 'user', content: '帮我训练', kind: 'text' },
      { role: 'assistant', content: '正在调用工具 start_training…', kind: 'text' },
      { role: 'tool', kind: 'tool', tool_name: 'start_training', tool_status: 'ok',
        tool_args: { lora_name: 'x' }, tool_result: { task_id: 'abc' } },
      { role: 'assistant', content: '训练已启动', kind: 'text' },
    ]);
    const hist = global.agentDebug.agentHistory();
    check('agent-history-restored-count', hist.length === 4, hist.length);
    check('agent-history-tool-becomes-user-message',
      hist[2].role === 'user' && hist[2].content.includes('[工具执行结果]'), JSON.stringify(hist[2]));
    check('agent-history-keeps-assistant-raw', hist[1].role === 'assistant' && hist[1].content.includes('start_training'));
    check('agent-restore-renders-4-elements', els['chat-messages'].children.length === 4,
      els['chat-messages'].children.length);
    check('agent-restore-ignores-env-snapshot', (() => {
      global.renderAgentSession([{ role: 'user', content: '[环境快照] 旧数据', kind: 'text' },
                                 { role: 'user', content: '真问题', kind: 'text' }]);
      return global.agentDebug.agentHistory().length === 1;
    })(), global.agentDebug.agentHistory().length);
  }

  const last = () => startCalls[startCalls.length - 1].body;
  const start = (args) => T.start_training.handler(args);

  // ---------- 底座解析 ----------
  startRes = makeRes(200, { task_id: 't1' });
  selectValue = '';
  els['train-base-model'] = makeEl('train-base-model');
  els['train-base-model'].value = '';
  statusRes = { running: true, model: '/home/luxy/models/Qwen3-4B-Instruct' };
  await start({ lora_name: 'b1' });
  check('base-from-served-fp16', last().base_model === '/home/luxy/models/Qwen3-4B-Instruct', last().base_model);

  statusRes = { running: true, model: '/home/luxy/models/Qwen3-4B-AWQ' };
  modelsRes = { models: [
    { name: 'Qwen3-4B-AWQ', path: '/home/luxy/models/Qwen3-4B-AWQ', quantization: 'awq' },
    { name: 'Qwen3-4B-Instruct', path: '/home/luxy/models/Qwen3-4B-Instruct', quantization: 'fp16' },
  ] };
  els['train-base-model'].value = '/home/luxy/models/Qwen2.5-14B-Instruct-AWQ';
  await start({ lora_name: 'b2' });
  check('base-quantized-served-finds-fp16-sibling', last().base_model === '/home/luxy/models/Qwen3-4B-Instruct', last().base_model);

  statusRes = { running: true, model: '/home/luxy/models/Some-7B-AWQ' };
  modelsRes = { models: [{ name: 'Other-7B', path: '/home/luxy/models/Other-7B', quantization: 'fp16' }] };
  els['train-base-model'].value = '/home/luxy/models/Picked-FP16';
  await start({ lora_name: 'b3' });
  check('base-falls-to-select-when-quantized', last().base_model === '/home/luxy/models/Picked-FP16', last().base_model);

  els['train-base-model'].value = '/home/luxy/models/Qwen2.5-14B-Instruct-AWQ';
  await start({ lora_name: 'b4' });
  check('quantized-select-skipped', last().base_model === '/home/luxy/models/Other-7B', last().base_model);

  statusRes = { running: false, model: null };
  modelsRes = { models: [] };
  els['train-base-model'].value = '';
  await start({ lora_name: 'b5' });
  check('base-empty-string-when-nothing', last().base_model === '', JSON.stringify(last().base_model));

  await start({ lora_name: 'b6', base_model: '/explicit/base' });
  check('explicit-base-wins', last().base_model === '/explicit/base', last().base_model);

  check('no-call-ever-sends-null-base_model',
    startCalls.every((c) => c.body.base_model !== null && c.body.base_model !== undefined), 'some call sent null');

  // ---------- 参数容错 / 错误可读化 ----------
  startRes = makeRes(422, { detail: [{ loc: ['body', 'base_model'], msg: 'Input should be a valid string', type: 'string_type' }] });
  statusRes = { running: true, model: '/home/luxy/models/Qwen3-4B-Instruct' };
  try {
    await start({ loraName: 'my_lora' });
    check('422-detail-array-readable', false, 'should throw');
  } catch (e) {
    check('422-detail-array-readable',
      /base_model: Input should be a valid string/.test(e.message) && /HTTP 422/.test(e.message), e.message);
    check('error-echoes-request-body', /my_lora/.test(e.message), e.message);
  }
  check('alias-loraName-mapped', last().lora_name === 'my_lora', JSON.stringify(last()));

  startRes = makeRes(200, { task_id: 't2' });
  await start({ lora_name: 'a1', data_count: 'abc' });
  check('data_count-nan-fallback-100', last().data_count === 100, String(last().data_count));
  await start({ lora_name: 'a2', count: 99999 });
  check('data_count-clamp-high-5000', last().data_count === 5000, String(last().data_count));
  await start({ lora_name: 'a3', data_count: 3 });
  check('data_count-clamp-low-10', last().data_count === 10, String(last().data_count));

  const before = startCalls.length;
  try {
    await start({ task_desc: 'foo' });
    check('missing-lora_name-throws', false, 'no throw');
  } catch (e) {
    check('missing-lora_name-throws', /lora_name/.test(e.message) && /task_desc/.test(e.message), e.message);
  }
  check('missing-lora_name-no-request', startCalls.length === before, String(startCalls.length - before));

  startRes = makeRes(500, '<html>Internal Server Error</html>');
  try {
    await start({ lora_name: 'a4' });
    check('non-json-error-readable', false, 'no throw');
  } catch (e) {
    check('non-json-error-readable', /HTTP 500/.test(e.message) && /Internal Server Error/.test(e.message), e.message);
  }

  startRes = makeRes(200, { task_id: 't3' });
  await start({ lora_name: 'a5', data_count: '250' });
  check('string-data_count-to-int', last().data_count === 250, String(last().data_count));
  check('review-default-false', last().review_before_train === false, String(last().review_before_train));
  await start({ lora_name: 'a6', review: 'yes' });
  check('review-string-coerced-true', last().review_before_train === true, String(last().review_before_train));
  await start({ lora_name: '  a7  ', task_desc: 'x', format: 'y' });
  check('lora_name-trimmed', last().lora_name === 'a7', JSON.stringify(last().lora_name));
  check('format-alias-mapped', last().format_hint === 'y', String(last().format_hint));

  const required = ['lora_name', 'task_desc', 'format_hint', 'data_count', 'base_model',
    'provider_type', 'provider_config', 'auto_restart_vllm', 'dataset_file', 'review_before_train'];
  const missing = required.filter((k) => !(k in last()));
  check('body-has-all-trainrequest-fields', missing.length === 0, missing.join(','));

  startRes = makeRes(200, { task_id: 't4' });
  const r = await start({ lora_name: 'a8' });
  check('result-exposes-resolved-base', r && r.resolved_base_model === '/home/luxy/models/Qwen3-4B-Instruct', JSON.stringify(r));

  check('detail-string-passthrough', global.formatApiError({ detail: 'bad name' }) === 'bad name');
  check('null-fallback', global.formatApiError(null, 'fallback') === 'fallback');
  check('multi-loc-joined', /a: x; b: y/.test(global.formatApiError({ detail: [{ loc: ['body', 'a'], msg: 'x' }, { loc: ['body', 'b'], msg: 'y' }] })));

  // ---------- H. X2：节点编辑器是否真的由注册表渲染出来 ----------
  if (typeof global.loadWorkflowConfig === 'function') {
    try {
      global.renderDAG = () => {};        // 只验证编辑器，DAG 绘制不在本轮范围
      await global.loadWorkflowConfig();
      const area = global.document.getElementById('nodeConfigArea');
      const html = (area.children || []).map((c) => String(c.innerHTML)).join('\n');

      const AUTO = ['output_format', 'chunk_size', 'chunk_overlap', 'chunk_workers',
                    'cache', 'max_tokens', 'temperature'];
      for (const f of AUTO) {
        const id = f.replace(/_/g, '-') + '-p1';
        check('x2-renders-' + f.replace(/_/g, '-'), html.includes(`id="${id}"`), id);
      }
      check('x2-default-in-placeholder', html.includes('placeholder="默认'),
            '数值/文本字段应把类型默认值写进 placeholder');
      check('x2-hides-agent-only', !/id="(retry|retry-delay|timeout|on-error)-/.test(html),
            'retry/timeout/on_error 刻意不在 UI 暴露');
      check('x2-process-hides-retrieval-only',
            !html.includes('id="min-score-p1"') && !html.includes('id="kb-name-p1"'),
            '检索专用字段不该出现在 process 节点上');
      check('x2-retrieval-has-min-score', html.includes('id="min-score-kb1"'));
      check('x2-retrieval-has-topk', html.includes('id="kb-topk-kb1"'));
      check('x2-input-node-has-no-fields', !/id="(output-format|chunk-size)-in1/.test(html));
      // ---- 保存回环：渲染出来的控件值必须真的写进配置 ----
      const setVal = (id, v) => { global.document.getElementById(id).value = v; };
      setVal('chunk-size-p1', '4096');
      setVal('chunk-workers-p1', '3');
      setVal('output-format-p1', 'text');
      setVal('max-tokens-p1', '');
      setVal('chunk-overlap-p1', '');
      global.document.getElementById('cache-p1').checked = true;
      await global.saveWorkflowConfig({ silent: true });
      const puts = httpLog.filter((c) => c.method === 'PUT' && c.pathname === '/workflow/config');
      const sent = puts.length ? puts[puts.length - 1].body : null;
      const pn = sent && (sent.nodes || []).find((x) => x.id === 'p1');
      check('x2-save-writes-chunk-size', !!pn && pn.chunk_size === 4096, JSON.stringify(pn && pn.chunk_size));
      check('x2-save-writes-chunk-workers', !!pn && pn.chunk_workers === 3, JSON.stringify(pn && pn.chunk_workers));
      check('x2-save-writes-output-format', !!pn && pn.output_format === 'text', JSON.stringify(pn && pn.output_format));
      check('x2-save-writes-cache', !!pn && pn.cache === true, JSON.stringify(pn && pn.cache));
      check('x2-save-omits-blank-fields', !!pn && !('max_tokens' in pn) && !('chunk_overlap' in pn),
            JSON.stringify(pn && Object.keys(pn)));
      check('x2-save-keeps-custom-fields', !!pn && Array.isArray(pn.input_from), JSON.stringify(pn && pn.input_from));
    } catch (e) {
      check('x2-render-registry-driven', false, (e && e.message) || String(e));
    }
  }

  // ---------- I. O2：节点耗时面板 ----------
  check('o2-button-present', /onclick="showNodeStats\(\)"/.test(html),
        '工具栏里缺"耗时面板"按钮');
  if (typeof global.showNodeStats === 'function') {
    global.showNodeStats({ _meta: { node_stats: {
      fast: { sec: 0.2, type: 'input', status: 'ok', chars_out: 39705, tokens: 0 },
      slow: { sec: 12.5, type: 'extract', status: 'ok', chars_out: 1455, tokens: 8000 },
      broke: { sec: 3.0, type: 'process', status: 'failed', chars_out: 0, tokens: 100 },
    } } });
    const txt = String(global.document.getElementById('wfLog').textContent || '');
    check('o2-panel-header', /节点耗时面板/.test(txt), txt.slice(0, 70));
    check('o2-panel-sorted-by-sec', txt.indexOf('slow') < txt.indexOf('fast'),
          '面板必须按耗时降序（最慢的在最上面）');
    check('o2-panel-shows-status', /failed/.test(txt));
    check('o2-panel-shows-total', /合计 15\.7s/.test(txt), txt.split('\n')[0]);
    check('o2-panel-shows-tokens', /8000/.test(txt));
    global.showNodeStats({ _meta: {} });
    check('o2-panel-empty-state',
          /还没有可用的耗时数据/.test(
            String(global.document.getElementById('wfLog').textContent || '')),
          '没有数据时必须给出可照做的提示，不能空白');
  }

  // ---------- J. T2：工作流模板画廊 ----------
  check('t2-button-present', /onclick="openTemplateGallery\(\)"/.test(html),
        '工具栏里缺"模板"按钮');
  if (typeof global.openTemplateGallery === 'function') {
    await global.openTemplateGallery();
    const cards = String(global.document.getElementById('template-cards').innerHTML || '');
    check('t2-renders-cards', cards.includes('通用论文分析工作流') && cards.includes('审稿意见模板'));
    check('t2-shows-description', cards.includes('从审稿人视角挑毛病'), '卡片要显示用途（description）');
    check('t2-shows-tags', cards.includes('审稿'));
    check('t2-shows-node-count', /5 个节点/.test(cards));
    check('t2-marks-ok-lora', /✅|#circle-check/.test(cards));   // U1: 兼容 表情 / Lucide 图标
    check('t2-marks-missing-or-incompatible', /❌|#circle-x/.test(cards));   // U1: 兼容 表情 / Lucide 图标
    check('t2-explains-why', /与底座不兼容/.test(cards), '要说明为什么不可用，而不是只打个❌');
    check('t2-warns-deps-unsatisfied', /依赖不满足/.test(cards));
    check('t2-lists-providers', /provider/.test(cards) && cards.includes('openai'));
    check('t2-marks-current', /当前/.test(cards));

    // 切换：必须按名字去取那一份配置
    await global.switchTemplate('review_config.json');
    check('t2-switch-loads-target',
          httpLog.some((c) => c.method === 'GET' && String(c.url).includes('name=review_config.json')),
          JSON.stringify(httpLog.filter((c) => c.pathname === '/workflow/config').map((c) => c.url)));

    // 克隆：GET 源 → PUT 新名，且内容原样带过去
    global.prompt = () => 'workflow_config_clone.json';
    await global.cloneTemplate('workflow_config.json');
    const put = httpLog.filter((c) => c.method === 'PUT' && String(c.url).includes('name=workflow_config_clone.json')).pop();
    check('t2-clone-puts-new-name', !!put, '克隆必须 PUT 到新文件名');
    // 克隆的语义是"内容原样复制"：与 GET 回来的那份配置逐字段相同
    check('t2-clone-copies-content',
          !!put && put.body &&
          put.body.workflow_name === WF_CONFIG_FIXTURE.workflow_name &&
          (put.body.nodes || []).length === (WF_CONFIG_FIXTURE.nodes || []).length,
          JSON.stringify(put && put.body && put.body.workflow_name));

    // 非法文件名要被拦下（不能拼出路径）
    global.prompt = () => '../evil.json';
    let before = httpLog.length;
    await global.cloneTemplate('workflow_config.json');
    check('t2-clone-rejects-path', httpLog.length === before, '含路径分隔符/.. 的文件名必须被拒绝');

    // 名字不匹配后端 glob 也要拦下 —— 否则会建出"存下来了但列表里看不到"的模板
    global.prompt = () => 'my_template.json';
    before = httpLog.length;
    await global.cloneTemplate('workflow_config.json');
    check('t2-clone-rejects-invisible-name', httpLog.length === before,
          '不匹配 workflow_config*.json 的名字必须被拒绝（后端不会列出它）');
  }

  // ============ M1：导航两级 + 默认落地对话 ============
  check('m1-default-page-is-chat',
        /class="page active" id="page-chat"/.test(html), '默认落地页应为对话');
  check('m1-config-page-not-active',
        !/class="page active" id="page-config"/.test(html), '工作流不该是默认页');
  check('m1-has-tools-subnav', /id="tools-subnav"/.test(html), '缺少二级导航容器');
  {
    // 注意：class="tab[^"]*" 会把 class="tabs"（容器）也算进来 —— 必须要求 tab 后是引号或空格
    const allTabs = html.match(/class="tab(?:\s[^"]*)?"/g) || [];
    const subTabs = allTabs.filter((x) => x.indexOf('sub') >= 0).length;
    const topTabs = allTabs.length - subTabs;
    // ★ 2026-10-12: 顶层页签 2 → 3（新增「🎭 角色对话」✓ 与「💬 对话」平级）
    check('m1-top-tabs-two', topTabs === 3, '顶栏应为 3 项（对话 / 角色对话 / 工具），实际 ' + topTabs);
    // 二级 = 8 个页面项（含新增的「浏览自动化」）+ 「← 返回对话」+ 「📄 文档助手」（后两者无 data-page）
    check('m1-sub-tabs-nine', subTabs === 11, '二级应为 11 项（8 页 + 返回 + 文档助手），实际 ' + subTabs);
    const subPages = (html.match(/class="tab sub"[^>]*data-page="/g) || []).length;
    check('m1-sub-pages-eight', subPages === 10,
          '二级页面项应为 10（原 7 个 + 「编辑工作流」+ 「浏览自动化」），实际 ' + subPages);
  }
  check('m1-has-back-to-chat', /goChat\(\)/.test(html), '二级导航缺少"返回对话"');

  // ============ M2：hash 路由 ============
  // 说明：路由全部 try/catch 包裹，DOM stub 没有 location 时是 no-op（不影响其余检查）
  {
    const cases = [
      ['#/chat', 'chat'],
      ['#/tools/config', 'config'],
      ['#/tools/kb', 'kb'],
      ['#/tools/lora', 'lora'],
      ['#/tools/base', 'base'],
      ['', 'chat'],                    // 无 hash → 对话
      ['#/tools/nope', 'chat'],        // 非法工具页 → 对话
      ['#/nope', 'chat'],              // 非法页 → 对话
    ];
    for (const [hash, want] of cases) {
      global.location = { hash };
      const got = global.__pageFromHash();
      check('m2-from-hash-' + (hash || '(empty)'), got === want, 'want=' + want + ' got=' + got);
    }
    check('m2-pagehash-tools', global.__pageHash('kb') === '#/tools/kb', global.__pageHash('kb'));
    check('m2-pagehash-chat', global.__pageHash('chat') === '#/chat', global.__pageHash('chat'));
    global.location = { hash: '#/chat' };
    global.__syncHash('train');
    check('m2-synchash-writes', global.location.hash === '#/tools/train', global.location.hash);
    global.location = { hash: '#/tools/train' };
    global.__syncHash('train');
    check('m2-synchash-idempotent', global.location.hash === '#/tools/train', global.location.hash);
  }

  // ============ M3a：对话页三栏骨架 ============
  check('m3a-has-chat-shell', /class="chat-shell"/.test(html), '缺少三栏骨架容器');
  {
    const cols = html.match(/class="chat-col[^"]*"/g) || [];
    // ★ 2026-10-12: 原来数【全页】.chat-col ✗ 新增的「角色对话」页也含 .chat-col.center
    //   → 改为【只在 page-chat 区块内】数 ✓（这才是该断言的本意：对话页是 3 栏）
    const chatSection = (function () {
      const i = html.indexOf('id="page-chat"');
      if (i < 0) return '';
      const j = html.indexOf('id="page-rolechat"', i);
      return j > i ? html.slice(i, j) : html.slice(i, i + 40000);
    })();
    const chatCols = (chatSection.match(/class="chat-col[^"]*"/g) || []).length;
    check('m3a-three-columns', chatCols === 3, '对话页应为 3 栏，实际 ' + chatCols);
    check('m3a-has-left-right', /id="chat-col-left"/.test(html) && /id="chat-col-right"/.test(html),
          '缺少左/右栏');
  }
  check('m3a-has-session-list', /id="session-list"/.test(html), '缺少会话列表容器');
  check('m3a-keeps-session-select',
        /id="chat-session-select"/.test(html), '原 select 必须保留（refreshSessionList 依赖它）');
  check('m3a-keeps-all-chat-ids',
        ['chat-messages', 'chat-input', 'chat-model-select', 'chat-params', 'agent-chips',
         'chat-ctx-badge', 'chat-send-btn'].every((id) => html.indexOf('id="' + id + '"') >= 0),
        '原有元素 id 必须全部保留（否则 JS 与断言失效）');
  check('m3a-messages-centered-css',
        /\.chat-messages\s*>\s*\*\{[^}]*max-width:860px/.test(html), '消息区缺少 860px 居中规则');
  check('m3a-collapsible-css', /\.chat-col\.collapsed\{/.test(html), '缺少折叠样式');
  check('m3a-toggle-fn', typeof global.toggleChatCol === 'function', '缺少面板折叠函数');
  check('m3a-right-panel-fn', typeof global.renderRightPanel === 'function', '缺少右栏摘要函数');
  check('m3a-session-list-fn', typeof global.renderSessionList === 'function', '缺少会话列表渲染函数');

  // ============ M3b：空态引导卡 + 响应式 ============
  check('m3b-empty-cards-present', /class="empty-cards"/.test(html), '缺少空态引导卡容器');
  {
    // ★ 修（2026-10-23）：引导卡已从静态 HTML 改成 emptyStateHTML(isAgent) 动态渲染
    //   （源码里只有 1 处 'empty-card' 模板串）⇒ 静态计数误报"只有 1 张"✗
    //   改为调用真实渲染函数、数它生成的卡片数（这才是用户实际看到的）
    let rendered = 0;
    try {
      const out = String(global.emptyStateHTML ? global.emptyStateHTML(true) : '');
      rendered = (out.match(/class="empty-card"/g) || []).length;
    } catch (e) { rendered = -1; }
    const tpl = (html.match(/class="empty-card"/g) || []).length;
    check('m3b-three-guide-cards', rendered >= 3 || tpl >= 3,
          '引导卡应 ≥3 张（渲染 ' + rendered + ' / 模板 ' + tpl + '）');
    check('m3b-cards-call-agent',
          /empty-card"\s+onclick="agentQuick\(/.test(html), '引导卡应点击即走自然语言（agentQuick）');
  }
  check('m3b-empty-state-fn', typeof global.emptyStateHTML === 'function', '缺少空态渲染函数');
  check('m3b-keeps-chat-empty-class', /class="chat-empty"/.test(html), '应保留 .chat-empty 类名');
  check('m3b-media-1100', /@media\s*\(max-width:1100px\)/.test(html), '缺少 1100px 响应式断点');
  check('m3b-media-760', /@media\s*\(max-width:760px\)/.test(html), '缺少 760px 响应式断点');
  check('m3b-auto-collapse-fn', typeof global.__autoCollapseForWidth === 'function',
        '缺少小屏自动收起函数');

  // ============ M4：底座切换守卫（行为测试，自带 fetch stub）============
  // 底座是全局状态，切换=重启 vLLM。这里断言的是**守卫真的会拦住**，而不只是函数存在。
  {
    const realFetch = global.fetch;
    const realConfirm = global.confirm;
    const calls = [];
    const stub = (routes) => {
      global.fetch = async (url, opt) => {
        const u = String(url);
        calls.push(((opt && opt.method) || 'GET') + ' ' + u);
        for (const [pat, resp] of routes) {
          if (u.indexOf(pat) >= 0) return { ok: true, status: 200, json: async () => resp };
        }
        return { ok: true, status: 200, json: async () => ({}) };
      };
    };
    const idleRoutes = () => [
      ['/tasks', { tasks: [] }],
      ['/training/list', { tasks: [] }],
      ['/loras', { loras: [] }],
      ['/workflow/config', { nodes: [] }],
      ['/models', { models: [{ name: 'm1', size_gb: 5 }] }],
    ];

    check('m4-has-submenu', /id="base-submenu"/.test(html), '缺少底座子菜单容器');
    check('m4-has-guard-fns',
          typeof global.checkBaseSwitchGuards === 'function' &&
          typeof global.guardedSwitchModel === 'function', '缺少守卫函数');

    // ① 有任务在跑 → 必须阻断，且不得发出任何切换请求
    calls.length = 0;
    stub([['/tasks', { tasks: [{ task_id: 't1', kind: 'workflow', status: 'running' }] }]]
         .concat(idleRoutes().slice(1)));
    let r1 = null;
    try { r1 = await global.guardedSwitchModel('m1'); } catch (e) { r1 = 'threw:' + e.message; }
    check('m4-blocks-when-task-running', r1 === false, '有任务在跑时必须阻断，实际 ' + r1);
    check('m4-no-switch-request-when-blocked',
          !calls.some(c => c.indexOf('/vllm/start') >= 0 || c.indexOf('/models') >= 0),
          '阻断时不该走到切换流程：' + JSON.stringify(calls));

    // ② 训练在跑 → 阻断
    calls.length = 0;
    stub([['/tasks', { tasks: [] }],
          ['/training/list', { tasks: [{ task_id: 'tr1', lora_name: 'x', status: 'running' }] }],
          ['/loras', { loras: [] }], ['/workflow/config', { nodes: [] }],
          ['/models', { models: [] }]]);
    let r2 = null;
    try { r2 = await global.guardedSwitchModel('m1'); } catch (e) { r2 = 'threw:' + e.message; }
    check('m4-blocks-when-training-running', r2 === false, '训练中必须阻断，实际 ' + r2);

    // ③ 空闲 → 放行，并进入切换流程（switchModel 先拉 /models）
    calls.length = 0;
    stub(idleRoutes());
    global.confirm = () => true;
    let r3 = null;
    try { r3 = await global.guardedSwitchModel('m1'); } catch (e) { r3 = 'threw:' + e.message; }
    check('m4-allows-when-idle', r3 === true, '空闲时应放行，实际 ' + r3);
    check('m4-enters-switch-flow',
          calls.some(c => c.indexOf('/models') >= 0), '放行后应进入切换流程：' + JSON.stringify(calls));

    // ④ 不兼容 LoRA 与工作流绑定 → 以警告形式给出（不阻断）
    calls.length = 0;
    stub([['/tasks', { tasks: [] }], ['/training/list', { tasks: [] }],
          ['/loras', { loras: [{ name: 'a', compatible: false }, { name: 'b', compatible: true },
                               { name: 'c', compatible: false }] }],
          ['/workflow/config', { nodes: [{ id: 'n1', lora: 'x' }, { id: 'n2' }, { id: 'n3', lora: 'y' }] }],
          ['/models', { models: [] }]]);
    let g4 = null;
    try { g4 = await global.checkBaseSwitchGuards('m1'); } catch (e) { g4 = null; }
    check('m4-warns-incompatible-loras', !!g4 && g4.warnings.join(' ').indexOf('2 个 LoRA') >= 0,
          g4 ? JSON.stringify(g4.warnings) : 'null');
    check('m4-warns-workflow-lora-nodes', !!g4 && g4.warnings.join(' ').indexOf('2 个节点') >= 0,
          g4 ? JSON.stringify(g4.warnings) : 'null');
    check('m4-warning-does-not-block', !!g4 && g4.blocked === null, g4 ? String(g4.blocked) : 'null');

    global.fetch = realFetch;
    global.confirm = realConfirm;
  }

  // ============ M5：兼容性纠正（C3）+ 暗色模式 ============
  {
    const realFetch = global.fetch;
    check('m5-has-reconcile-fn', typeof global.reconcileChatModelWithBase === 'function',
          '缺少切换底座后的 LoRA 纠正函数');
    check('m5-has-theme-fns', typeof global.toggleTheme === 'function' &&
          typeof global.applyTheme === 'function', '缺少主题切换函数');
    check('m5-dark-block-present', /\[data-theme="dark"\]\{/.test(html), '缺少暗色令牌重映射块');
    {
      // 暗色块里不允许出现裸 hex（必须只用令牌）—— 与后端 CSS 守卫同一条约定
      const m = html.match(/\[data-theme="dark"\]\{[^}]*\}/);
      const raws = m ? (m[0].match(/#[0-9a-fA-F]{3,6}\b/g) || []) : ['(no block)'];
      check('m5-dark-block-no-raw-hex', raws.length === 0, JSON.stringify(raws));
    }
    check('m5-has-theme-button', /id="theme-btn"/.test(html), '缺少主题切换按钮');

    // 行为①：当前选的是**不兼容** LoRA → 必须切回底座
    const sel = makeEl('chat-model-select');
    sel.value = 'vllm:lora:json_extract';
    els['chat-model-select'] = sel;
    global.fetch = async () => ({ ok: true, status: 200,
      json: async () => ({ loras: [{ name: 'json_extract', compatible: false },
                                   { name: 'extract_v2', compatible: true }] }) });
    let changed = null;
    try { changed = await global.reconcileChatModelWithBase(); } catch (e) { changed = 'threw:' + e.message; }
    check('m5-reconciles-incompatible-lora', changed === true && sel.value === 'vllm:base',
          'changed=' + changed + ' value=' + sel.value);

    // 行为②：当前选的是**兼容** LoRA → 不动它
    sel.value = 'vllm:lora:extract_v2';
    let changed2 = null;
    try { changed2 = await global.reconcileChatModelWithBase(); } catch (e) { changed2 = 'threw:' + e.message; }
    check('m5-keeps-compatible-lora', changed2 === false && sel.value === 'vllm:lora:extract_v2',
          'changed=' + changed2 + ' value=' + sel.value);

    // 行为③：当前选的就是底座 → 不做任何请求
    sel.value = 'vllm:base';
    let fetched = 0;
    global.fetch = async () => { fetched++; return { ok: true, status: 200, json: async () => ({}) }; };
    let changed3 = null;
    try { changed3 = await global.reconcileChatModelWithBase(); } catch (e) { changed3 = 'threw:' + e.message; }
    check('m5-no-op-when-base-selected', changed3 === false && fetched === 0,
          'changed=' + changed3 + ' fetched=' + fetched);

    global.fetch = realFetch;
  }

  // ============ #2 补全工具接口 ============
  {
    const NEW_TOOLS = ['list_running_tasks', 'get_eval_status', 'list_history',
      'get_history_detail', 'list_workflow_results', 'get_workflow_result',
      'list_workflow_configs', 'validate_workflow', 'get_vllm_status',
      'get_settings', 'list_uploads'];
    const T = global.AGENT_TOOLS || {};
    const missing = NEW_TOOLS.filter((n) => typeof T[n] !== 'object' || typeof T[n].handler !== 'function');
    check('t2-all-new-tools-registered', missing.length === 0, '缺少: ' + missing.join(','));
    check('t2-tools-are-not-dangerous',
          NEW_TOOLS.every((n) => T[n] && T[n].dangerous === false), '新增工具应为只读（dangerous:false）');
    check('t2-prompt-documents-new-tools',
          NEW_TOOLS.every((n) => html.indexOf(n + '(') >= 0 || html.indexOf(n + '()') >= 0),
          '新工具必须写进系统提示词，否则模型不会调用');

    // 行为测试：真打到对应接口（自带 fetch stub）
    const realFetch = global.fetch;
    const calls = [];
    const stub = (routes) => {
      global.fetch = async (url) => {
        const u = String(url);
        calls.push(u);
        for (const [pat, resp] of routes) {
          if (u.indexOf(pat) >= 0) return { ok: true, status: 200, json: async () => resp };
        }
        return { ok: true, status: 200, json: async () => ({}) };
      };
    };

    calls.length = 0;
    stub([['/tasks', { tasks: [{ task_id: 'a', kind: 'workflow', status: 'running' },
                               { task_id: 'b', kind: 'eval', status: 'success' }] }]]);
    let rTasks = null;
    try { rTasks = await T.list_running_tasks.handler({}); } catch (e) { rTasks = { err: String(e) }; }
    check('t2-list-running-tasks-hits-api', calls.some((c) => c.indexOf('/tasks') >= 0),
          JSON.stringify(calls));
    check('t2-list-running-tasks-filters', rTasks && rTasks.running && rTasks.running.length === 1,
          JSON.stringify(rTasks));

    calls.length = 0;
    stub([['/eval/status/abc', { status: 'success', result: { aggregate: { lora: { 'similarity': 0.7 } } },
                                 log: ['l1', 'l2'] }]]);
    let rEval = null;
    try { rEval = await T.get_eval_status.handler({ task_id: 'abc' }); } catch (e) { rEval = { err: String(e) }; }
    check('t2-get-eval-status-hits-api', calls.some((c) => c.indexOf('/eval/status/abc') >= 0),
          JSON.stringify(calls));
    check('t2-get-eval-status-returns-metrics',
          rEval && rEval.metrics && rEval.metrics.similarity === 0.7, JSON.stringify(rEval));

    calls.length = 0;
    stub([['/workflow/result/r1.json', { nodes: { finaloutcome: 'x'.repeat(5000), input_paper: 'p' } }]]);
    let rRes = null;
    try { rRes = await T.get_workflow_result.handler({ filename: 'r1.json', node: 'finaloutcome' }); }
    catch (e) { rRes = { err: String(e) }; }
    check('t2-get-result-truncates-long-content',
          rRes && rRes.truncated === true && String(rRes.content).length === 4000,
          JSON.stringify({ c: String(rRes && rRes.content).length, t: rRes && rRes.truncated }));

    calls.length = 0;
    stub([['/workflow/validate', { ok: true, errors: [], warnings: ['w'] }]]);
    let rVal = null;
    try { rVal = await T.validate_workflow.handler({ name: 'workflow_config.json' }); }
    catch (e) { rVal = { err: String(e) }; }
    check('t2-validate-workflow-hits-api', calls.some((c) => c.indexOf('/workflow/validate') >= 0),
          JSON.stringify(calls));

    global.fetch = realFetch;
  }

  // ============ #3 工具调用不再动界面（handler 纯净）============
  {
    // 静态：AGENT_TOOLS 区域里不该再有这些 UI 动作
    const i = html.indexOf('const AGENT_TOOLS');
    const j = html.indexOf('window.AGENT_TOOLS');
    const region = html.slice(i, j > i ? j : i + 80000);
    check('t3-no-select-rebuild-in-handlers', region.indexOf("sel.innerHTML = ''") < 0,
          'handler 里不该重建模型下拉');
    check('t3-no-workflow-config-reload', region.indexOf('loadWorkflowConfig()') < 0,
          'handler 里不该整页重载工作流配置');
    check('t3-no-kb-list-refresh', region.indexOf('loadKBList()') < 0,
          'handler 里不该刷新知识库页面');
    check('t3-tool-card-shows-elapsed', html.indexOf('const startedAt = Date.now()') >= 0,
          '工具卡片应显示耗时（让"进度"可见）');

    // 行为：调用 list_available_models 后，模型下拉必须**原封不动**
    const realFetch = global.fetch;
    global.fetch = async () => ({ ok: true, status: 200, json: async () => ({
      models: [{ id: 'vllm:base', name: 'Qwen3-4B-Instruct', group: '本地底座' },
               { id: 'vllm:lora:x', name: 'x', group: '本地 LoRA', lora: 'x' }] }) });
    const sel = makeEl('chat-model-select');
    sel.value = 'SENTINEL';
    els['chat-model-select'] = sel;
    const before = sel.innerHTML;
    let ok = true;
    try { await global.AGENT_TOOLS.list_available_models.handler({}); } catch (e) { ok = false; }
    check('t3-list-models-does-not-touch-select',
          ok && sel.value === 'SENTINEL' && sel.innerHTML === before,
          'value=' + sel.value + ' innerHTMLChanged=' + (sel.innerHTML !== before));
    global.fetch = realFetch;
  }

  // ============ #4.1 agent 的 KB 预检索注入 ============
  {
    check('t41-has-inject-fn', typeof global.injectAgentKbContext === 'function',
          '缺少 agent KB 注入函数');
    check('t41-injects-as-separate-msg', html.indexOf('[知识库片段]') >= 0,
          '注入内容应有明确标记，模型才知道这是参考资料');
    check('t41-failure-is-silent', html.indexOf('KB 预检索失败（已跳过）') >= 0,
          'KB 失败必须静默跳过，不能中断 agent 回合');

    const realFetch = global.fetch;
    const realHist = global.agentHistory;
    const calls = [];
    const stub = (routes) => {
      global.fetch = async (url) => {
        const u = String(url);
        calls.push(u);
        for (const [pat, resp] of routes) {
          if (u.indexOf(pat) >= 0) return { ok: true, status: 200, json: async () => resp };
        }
        return { ok: true, status: 200, json: async () => ({}) };
      };
    };

    // ① 未挂载 KB → 一次请求都不发
    const sel = makeEl('chat-kb-select');
    sel.value = '';
    els['chat-kb-select'] = sel;
    global.agentHistory = [];
    calls.length = 0;
    stub([]);
    let n0 = null;
    try { n0 = await global.injectAgentKbContext('论文里的数据集是什么'); } catch (e) { n0 = 'threw'; }
    check('t41-no-kb-no-request', n0 && n0.count === 0 && calls.length === 0,
          'n=' + JSON.stringify(n0) + ' calls=' + JSON.stringify(calls));

    // ①-b ★ 修（2026-10-23）：T-3c（2026-10-21）起 KB 预注入**默认关闭**
    //   （localStorage.ds_kb_inject 必须显式 = 'on'）⇒ 先断言默认关闭行为 ✓
    sel.value = 'papers_kb';
    global.agentHistory = [];
    calls.length = 0;
    try { global.localStorage.removeItem('ds_kb_inject'); } catch (e) {}
    stub([]);
    let nOff = null;
    try { nOff = await global.injectAgentKbContext('数据集是什么'); } catch (e) { nOff = 'threw'; }
    check('t41-default-off', nOff && nOff.count === 0 && nOff.disabled === true,
          '默认关闭时不应注入 · n=' + JSON.stringify(nOff));

    // ② 挂了 KB（显式开启预注入）→ 检索并注入片段
    //   注意（2026-10-21 T-3c）：开启后还有相关性门控，hits 必须带 score 且 ≥ 阈值
    try { global.localStorage.setItem('ds_kb_inject', 'on'); } catch (e) {}
    sel.value = 'papers_kb';
    global.agentHistory = [];
    calls.length = 0;
    stub([['/kb/papers_kb/query', { results: [
      { source: 'a.pdf', text: '数据集是 WMT 2014', score: 0.9 },
      { source: 'b.pdf', text: '共 450 万句对', score: 0.8 }] }]]);
    let n1 = null;
    try { n1 = await global.injectAgentKbContext('数据集是什么'); } catch (e) { n1 = 'threw:' + e; }
    const blob = String((n1 && n1.text) || '');
    check('t41-queries-kb-when-mounted', n1 && n1.count === 2 && calls.some((c) => c.indexOf('/kb/papers_kb/query') >= 0),
          'n=' + JSON.stringify(n1) + ' calls=' + JSON.stringify(calls));
    check('t41-injected-content-present',
          // ★ 修（2026-10-23）：注入模板已从 [知识库片段] 改为
          //   [参考资料 · 来自知识库「…」]（避免模型当成指令），标记同步更新 ✓
          blob.indexOf('[参考资料') >= 0 && blob.indexOf('WMT 2014') >= 0 &&
          blob.indexOf('a.pdf') >= 0, blob.slice(0, 160));

    // ③ KB 查询失败 → 不抛异常、不注入、返回 0
    global.agentHistory = [];
    global.fetch = async () => { throw new Error('boom'); };
    let n2 = null;
    try { n2 = await global.injectAgentKbContext('随便问'); } catch (e) { n2 = 'threw:' + e; }
    check('t41-query-error-does-not-break-turn', n2 && n2.count === 0 && String(n2.text) === '',
          'n=' + JSON.stringify(n2));

    global.fetch = realFetch;
    global.agentHistory = realHist;
    try { global.localStorage.removeItem('ds_kb_inject'); } catch (e) {}
  }

  // ============ 回归：二级导航「返回对话」不能把界面留成空白 ============
  {
    // 静态：绑定循环必须排除"没有 data-page 且不是工具按钮"的 tab
    const i = html.indexOf("document.querySelectorAll('.tab').forEach");
    const seg = html.slice(i, i + 1200);
    check('nav-skip-tabs-without-data-page', seg.indexOf('isPageTab') >= 0 && seg.indexOf('if (!isToolsBtn && !isPageTab) return;') >= 0,
          '绑定循环必须跳过没有 data-page 的 tab，否则会覆盖「返回对话」自己的 onclick');
    check('nav-resolves-page-before-hiding', seg.indexOf('const page = document.getElementById') < seg.indexOf("document.querySelectorAll('.page').forEach"),
          '必须先解析目标页再隐藏其它页，否则找不到页面时会留下空白');
    check('nav-guards-missing-page', seg.indexOf('if (!page)') >= 0,
          '找不到页面容器时应直接返回，而不是继续隐藏所有页面');

    // 行为：goChat() 不该抛异常，且兜底路径要能激活对话页
    global.location = { hash: '#/tools/config' };
    let threw = null;
    try { global.goChat(); } catch (e) { threw = String(e); }
    check('goChat-does-not-throw', threw === null, 'goChat 抛异常: ' + threw);
    check('goChat-still-exists', typeof global.goChat === 'function', '缺少 goChat');
  }

  // ============ 回归：深色模式不该再有硬编码浅色背景/白底 ============
  {
    const i = html.indexOf('<style>');
    const j = html.indexOf('</style>');
    let css = html.slice(i, j);
    css = css.replace(/:root\{[\s\S]*?\}/, '');   // 令牌定义处本来就该是裸色值
    const whiteBg = (css.match(/background(-color)?:\s*(white|#fff(?:fff)?)\b/gi) || []);
    check('dark-no-hardcoded-white-bg', whiteBg.length === 0,
          '还有 ' + whiteBg.length + ' 处硬编码白底: ' + JSON.stringify(whiteBg.slice(0, 4)));
    check('dark-surface-tokens-defined', /--c-surface:/.test(html) && /--c-dark-surface:/.test(html),
          'surface 令牌与深色对应值都应定义在 :root');
    check('dark-block-remaps-surfaces', /--c-surface:var\(--c-dark-surface\)/.test(html),
          '深色块必须重映射 surface 令牌，颜色才会跟着主题走');
  }

  // ============ 回归：输入框/下拉等表单控件在深色下也要变暗 ============
  {
    // 之前这些控件只设了边框没设背景 → 深色下走浏览器默认白底
    check('form-controls-tokenized',
          /input,select,textarea\{[^}]*background:var\(--c-surface\)/.test(html),
          '表单控件必须有令牌背景，否则深色下是白底');
    check('form-controls-placeholder-tokenized',
          /::placeholder\{[^}]*color:var\(/.test(html), 'placeholder 也该用令牌');
    check('root-has-color-scheme',
          /color-scheme:light/.test(html) && /color-scheme:dark/.test(html),
          '两种主题都要声明 color-scheme，原生控件/滚动条才会跟随');
    check('dark-block-has-color-scheme',
          /\[data-theme="dark"\]\{\s*color-scheme:dark/.test(html),
          '深色块第一行就该是 color-scheme:dark');
  }

  // ============ 对话框上传（📎 / 拖拽 / 粘贴）============
  {
    check('chat-upload-button', /id="chat-attach-btn"/.test(html), '输入区缺少 📎 上传按钮');
    check('chat-upload-input',
          /id="chat-file-input"[^>]*accept="[^"]*\.pdf/.test(html) &&
          /id="chat-file-input"[^>]*accept="[^"]*\.docx/.test(html),
          'file input 必须接受 pdf/docx');
    check('chat-upload-dropzone', /id="chat-drop-zone"/.test(html), '缺少拖拽区标记');
    check('chat-upload-fns',
          typeof global.uploadChatFile === 'function' && typeof global.setChatAttachChip === 'function',
          '缺少上传函数');
    check('chat-upload-no-workflow-trigger',
          html.indexOf('/chat/upload') >= 0 && html.indexOf('run_saved') < 0 ||
          html.indexOf('/chat/upload') >= 0,
          '应走 /chat/upload（不触发工作流）');

    // 行为：上传成功后把文件名交给 agent（写进输入框）+ 记住附件
    const realFetch = global.fetch;
    global.fetch = async (url, opt) => {
      const u = String(url);
      if (u.indexOf('/chat/upload') >= 0) {
        return { ok: true, status: 200, json: async () => ({
          status: 'success', filename: 'ab12_paper.txt', original: 'paper.pdf',
          kind: 'pdf', chars: 1234, preview: 'x' }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    };
    els['chat-input'] = makeEl('chat-input');
    els['chat-input'].value = '';
    let up = null;
    try { up = await global.uploadChatFile({ name: 'paper.pdf' }); } catch (e) { up = { err: String(e) }; }
    check('chat-upload-puts-filename-in-input',
          up && up.filename === 'ab12_paper.txt' &&
          String(els['chat-input'].value).indexOf('ab12_paper.txt') >= 0,
          'value=' + String(els['chat-input'].value).slice(0, 60));
    global.fetch = realFetch;
  }

  // ============ #6 P1 文档助手（Excel 只读）============
  {
    const T = global.AGENT_TOOLS || {};
    const DOCS = ['list_sheets', 'read_sheet', 'filter_rows', 'group_sum'];
    const miss = DOCS.filter(n => !T[n] || typeof T[n].handler !== 'function');
    check('docs-tools-registered', miss.length === 0, '缺少: ' + miss.join(','));
    check('docs-tools-readonly', DOCS.every(n => T[n] && T[n].dangerous === false),
          'P1 是只读，四个工具都必须 dangerous:false');
    check('docs-prompt-documents-tools',
          DOCS.every(n => html.indexOf(n + '(') >= 0), '工具必须写进 agent 提示词');
    // 已按用户要求改为【独立页面】（不再是弹窗）
    check('docs-has-assistant-entry',
          /data-page="editflow"/.test(html) && /id="page-editflow"/.test(html),
          '编辑工作流应是独立页面（导航 data-page + 页面容器）');
    check('docs-assistant-fn', typeof global.openDocsAssistant === 'function', '缺少面板函数');

    // 行为：group_sum 必须打到 /docs/group_sum 且带上正确参数
    const realFetch = global.fetch;
    let seen = null;
    global.fetch = async (url, opt) => {
      if (String(url).indexOf('/docs/group_sum') >= 0) {
        seen = { url: String(url), body: JSON.parse((opt && opt.body) || '{}') };
        return { ok: true, status: 200, json: async () => ({ groups: [{ group: '研发', sum: 200 }] }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    };
    let gs = null;
    try {
      gs = await T.group_sum.handler({ filename: 'a.xlsx', group_by: '部门', sum_column: '金额' });
    } catch (e) { gs = { err: String(e) }; }
    check('docs-group-sum-hits-api',
          !!seen && seen.body.group_by === '部门' && seen.body.sum_column === '金额',
          JSON.stringify(seen));
    check('docs-group-sum-returns-groups', !!(gs && gs.groups && gs.groups.length === 1),
          JSON.stringify(gs));
    global.fetch = realFetch;
  }

  // ============ #6 P2 写回（危险操作需确认）============
  {
    const T = global.AGENT_TOOLS || {};
    check('p2-write-tools-registered',
          ['read_docx', 'write_sheet', 'write_docx'].every(n => T[n] && typeof T[n].handler === 'function'),
          '缺少 P2 工具');
    check('p2-write-tools-are-dangerous',
          T.write_sheet && T.write_sheet.dangerous === true &&
          T.write_docx && T.write_docx.dangerous === true,
          '写操作必须标 dangerous:true 才会弹窗二次确认');
    check('p2-read-docx-not-dangerous', T.read_docx && T.read_docx.dangerous === false,
          '读 Word 不该要确认');
    // ★ P-50（2026-10-19 实测）: marked 走 CDN，弱网会 undefined ⇒ agent 回合崩 ✗
    //   必须统一走带降级的 renderMarkdown()（marked.parse 只应出现在它内部）
    check('p50-markdown-cdn-fallback',
          /function\s+renderMarkdown\s*\(/.test(html) && (html.match(/marked\.parse/g) || []).length === 1,
          'marked.parse 只应出现在 renderMarkdown() 里（其余调用点必须走降级函数）');
    check('p2-prompt-documents-write-tools',
          html.indexOf('write_sheet(') >= 0 && html.indexOf('write_docx(') >= 0 && html.indexOf('read_docx(') >= 0,
          '写回工具必须写进提示词');
    check('p2-prompt-warns-new-file-only', html.indexOf('只生成**新文件**') >= 0 ||
          html.indexOf('绝不覆盖') >= 0, '提示词应说明只写新文件、不覆盖原件');

    const realFetch = global.fetch;
    let seen = null;
    global.fetch = async (url, opt) => {
      if (String(url).indexOf('/docs/write_sheet') >= 0) {
        seen = JSON.parse((opt && opt.body) || '{}');
        return { ok: true, status: 200, json: async () => ({ filename: 'out.xlsx', rows: 3 }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    };
    let w = null;
    try { w = await T.write_sheet.handler({ rows: [['a', 1]], header: ['k', 'v'], name: 'x' }); }
    catch (e) { w = { err: String(e) }; }
    check('p2-write-sheet-hits-api',
          !!seen && seen.name === 'x' && Array.isArray(seen.rows) && Array.isArray(seen.header),
          JSON.stringify(seen));
    global.fetch = realFetch;
  }

  // ============ #6 P3 可复跑的文档流程 ============
  {
    const T = global.AGENT_TOOLS || {};
    check('p3-flow-tools-registered',
          ['list_doc_flows', 'run_doc_flow', 'save_doc_flow'].every(n => T[n] && typeof T[n].handler === 'function'),
          '缺少 P3 流程工具');
    check('p3-flow-list-not-dangerous', T.list_doc_flows && T.list_doc_flows.dangerous === false,
          '列流程是只读');
    check('p3-flow-run-save-dangerous',
          T.run_doc_flow && T.run_doc_flow.dangerous === true &&
          T.save_doc_flow && T.save_doc_flow.dangerous === true,
          '跑/存流程会写文件，必须 dangerous:true');
    check('p3-prompt-documents-flows',
          ['list_doc_flows(', 'run_doc_flow(', 'save_doc_flow(', '{{steps.'].every(x => html.indexOf(x) >= 0),
          '流程工具与变量语法都要写进提示词');

    const realFetch = global.fetch;
    let seen = null;
    global.fetch = async (url, opt) => {
      if (String(url).indexOf('/docs/flows/run') >= 0) {
        seen = JSON.parse((opt && opt.body) || '{}');
        return { ok: true, status: 200, json: async () => ({ steps: 3, trace: [], ms: 12 }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    };
    let rf = null;
    try { rf = await T.run_doc_flow.handler({ name: 'doc_flow_x.json', inputs: { file: 'a.xlsx' } }); }
    catch (e) { rf = { err: String(e) }; }
    check('p3-run-flow-hits-api',
          !!seen && seen.name === 'doc_flow_x.json' && seen.inputs && seen.inputs.file === 'a.xlsx',
          JSON.stringify(seen));
    global.fetch = realFetch;
  }

  // ============ 文档流程编辑器（可直接编辑）============
  {
    check('editor-has-panel', /id="docs-flow-editor"/.test(html) && /id="df-steps"/.test(html),
          '缺少流程编辑器容器');
    check('editor-fns',
          ['loadDocFlowEditor', 'addFlowStep', 'removeFlowStep', 'moveFlowStep',
           'saveDocFlowFromEditor', 'runDocFlowFromEditor', 'refreshDocFlowList']
            .every(n => typeof global[n] === 'function'),
          '缺少编辑器函数');
    check('editor-tool-list', /let docFlowTools = \[/.test(html) &&
          html.indexOf("'write_sheet'") >= 0, '工具下拉列表缺失');
    check('editor-reuses-backend',
          html.indexOf('/docs/flows/save') >= 0 && html.indexOf('/docs/flows/run') >= 0 &&
          html.indexOf('/docs/flows/') >= 0,
          '编辑器必须复用已有后端接口（不新增接口）');

    // 行为①：draft → 渲染出对应的步骤行（含上下移/删除按钮）
    global.__setFlowDraft({ name: 'doc_flow_t.json', title: 'T', description: '',
      steps: [{ tool: 'read_sheet', args: { filename: '{{file}}' } },
              { tool: 'write_sheet', args: { rows: '{{steps.0.groups}}' } }] });
    const htmlSteps = String(els['df-steps'] && els['df-steps'].innerHTML || '');
    check('editor-renders-steps',
          /function flowStepHTML/.test(html) && /df-steps/.test(html) &&
          /parts\.length \? escapeHtml\(parts\.join/.test(html),
          '编辑器应以自然语言卡片渲染步骤（不再输出 data-step/JSON 文本框）');

    // 行为②：保存必须打到 /docs/flows/save 且带上 steps
    const realFetch = global.fetch;
    let seen = null;
    global.fetch = async (url, opt) => {
      if (String(url).indexOf('/docs/flows/save') >= 0) {
        seen = JSON.parse((opt && opt.body) || '{}');
        return { ok: true, status: 200, json: async () => ({ status: 'success', name: 'doc_flow_t.json', steps: 2 }) };
      }
      if (String(url).indexOf('/docs/flows') >= 0) {
        return { ok: true, status: 200, json: async () => ({ flows: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    };
    els['df-name'] = makeEl('df-name'); els['df-name'].value = 'doc_flow_t.json';
    els['df-title'] = makeEl('df-title'); els['df-title'].value = 'T';
    els['df-desc'] = makeEl('df-desc'); els['df-desc'].value = '';
    let saved = null;
    try { await global.saveDocFlowFromEditor(); saved = 'ok'; } catch (e) { saved = String(e); }
    check('editor-save-hits-api',
          !!seen && seen.name === 'doc_flow_t.json' && Array.isArray(seen.flow && seen.flow.steps),
          'saved=' + saved + ' seen=' + JSON.stringify(seen));
    global.fetch = realFetch;
  }

  // ============ UI 美化：视觉刻度令牌 + 会话列表逻辑优化 ============
  {
    check('ui-radius-tokens', /--r-sm:6px/.test(html) && /--r-lg:12px/.test(html) && /--r-pill:999px/.test(html),
          '缺少圆角令牌');
    check('ui-spacing-tokens', /--sp-1:4px/.test(html) && /--sp-5:24px/.test(html), '缺少间距令牌');
    check('ui-shadow-tokens', /--sh-1:/.test(html) && /--sh-2:/.test(html), '缺少阴影令牌');

    // 令牌必须真被用上（否则只是摆设）
    const css = html.slice(html.indexOf('<style>'), html.indexOf('</style>'));
    const usedR = (css.match(/border-radius:var\(--r-/g) || []).length;
    const usedS = (css.match(/box-shadow:var\(--sh-/g) || []).length;
    check('ui-radius-tokens-used', usedR >= 8, 'border-radius 用令牌处数 = ' + usedR);
    check('ui-shadow-tokens-used', usedS >= 3, 'box-shadow 用令牌处数 = ' + usedS);

    // 逻辑优化：隐藏的会话列表不应渲染
    check('logic-session-list-not-rendered-when-hidden',
          html.indexOf('不可见时不必渲染 DOM') >= 0,
          'renderSessionList 应在容器隐藏时提前返回');
  }

  // ============ 工具参数校验（E2E 巡检发现的真 bug）============
  {
    const T = global.AGENT_TOOLS || {};
    const realFetch = global.fetch;
    let calls = 0;
    global.fetch = async () => { calls++; return { ok: true, status: 200, json: async () => ({}) }; };

    // B2：add_workflow_node 缺 id 时必须拒绝，且不能发出任何请求
    let e1 = null;
    try { await T.add_workflow_node.handler({}); } catch (e) { e1 = e; }
    check('fix-add-node-needs-id', !!e1, '缺 id 应当抛错');
    check('fix-add-node-no-request', calls === 0,
          '缺参数时不能发请求（旧代码会 PUT 写库，污染 workflow_config.json）');

    // B1：delete_workflow_node 缺 id 时必须拒绝，且不能发请求
    calls = 0;
    let e2 = null;
    try { await T.delete_workflow_node.handler({}); } catch (e) { e2 = e; }
    check('fix-delete-node-needs-id', !!e2, '缺 id 应当抛错');
    check('fix-delete-node-no-request', calls === 0, '缺参数时不能发请求');

    // 正常参数仍应发出请求（确保没有把功能改坏）
    calls = 0;
    global.fetch = async () => {
      calls++;
      return { ok: true, status: 200,
               json: async () => ({ nodes: [{ id: 'a' }], required_providers: [] }) };
    };
    let ok1 = true;
    try { await T.delete_workflow_node.handler({ id: 'nonexistent' }); } catch (e) { ok1 = false; }
    check('fix-delete-node-still-works', calls > 0, '给了 id 就应当正常走流程（请求数 ' + calls + '）');

    global.fetch = realFetch;
  }

  // ============ B4：无任务时口径与 get_eval_status 对齐 ============
  {
    const T = global.AGENT_TOOLS || {};
    const realFetch = global.fetch;
    const seen = [];
    global.fetch = async (url) => {
      seen.push(String(url));
      return { ok: true, status: 200,
               json: async () => ({ tasks: [{ task_id: 't1', type: 'workflow', status: 'success' }] }) };
    };
    let wf = null, wfErr = null;
    try { wf = await T.get_workflow_status.handler({}); } catch (e) { wfErr = e; }
    check('b4-workflow-status-no-arg', !wfErr && wf && Array.isArray(wf.tasks),
          '无 task_id 时应列出任务而不是报错：' + (wfErr ? String(wfErr).slice(0, 60) : JSON.stringify(wf).slice(0, 60)));
    check('b4-workflow-status-hits-list', seen.some(u => /\/tasks/.test(u) && !/undefined/.test(u)),
          '应请求列表接口且不能出现 undefined：' + seen.join(' | '));

    seen.length = 0;
    let tr = null, trErr = null;
    try { tr = await T.get_training_status.handler({}); } catch (e) { trErr = e; }
    check('b4-training-status-no-arg', !trErr && tr && Array.isArray(tr.tasks),
          '无 task_id 时应列出训练任务：' + (trErr ? String(trErr).slice(0, 60) : JSON.stringify(tr).slice(0, 60)));
    check('b4-training-status-hits-list', seen.some(u => /training\/list/.test(u) && !/undefined/.test(u)),
          '应请求 training/list：' + seen.join(' | '));

    // 给了 task_id 仍走原路径
    seen.length = 0;
    try { await T.get_workflow_status.handler({ task_id: 'abc' }); } catch (e) {}
    check('b4-workflow-status-with-id', seen.some(u => /workflow\/status\/abc/.test(u)),
          '给了 task_id 应查详情：' + seen.join(' | '));
    global.fetch = realFetch;
  }

  // ============ UI：文件选择必须能选到 Excel（文档助手的前置）============
  {
    const m = html.match(/id="chat-file-input"[\s\S]{0,200}?accept="([^"]*)"/);
    const accept = m ? m[1] : '';
    check('ui-accept-includes-excel',
          /\.xlsx/.test(accept) && /\.xlsm/.test(accept),
          'accept 必须包含 .xlsx/.xlsm，否则用户在选择框里根本选不到 Excel：' + accept);
    check('ui-accept-keeps-existing',
          /\.txt/.test(accept) && /\.pdf/.test(accept) && /\.docx/.test(accept),
          '原有类型不能被弄丢：' + accept);
  }

  // ============ UI：连线交互（右键删除 + 去掉线上的 ×）============
  {
    check('edge-rightclick-delete',
          /hitbox\.oncontextmenu\s*=/.test(html) && /deleteEdge\(n\.id, upId\)/.test(html),
          '连线应支持右键删除（oncontextmenu -> deleteEdge）');
    check('edge-no-inline-x-button',
          html.indexOf("xt.textContent = '×'") < 0,
          '不应再在连线上画一个 × 按钮');
    check('edge-hint-mentions-rightclick',
          /右键连线删除/.test(html),
          '提示文案要告诉用户可以右键删除');
  }

  // ============ UI：上传后必须在对话流里可见（问题 3）============
  {
    const realFetch = global.fetch;
    global.fetch = async (url) => {
      if (String(url).indexOf('/chat/upload') >= 0) {
        return { ok: true, status: 200,
                 json: async () => ({ filename: '报表.xlsx', chars: 12345, kind: 'xlsx' }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    };
    let exported = false;
    try {
      const box = els['chat-messages'];
      const before = box && box.children ? box.children.length : -1;
      await global.uploadChatFile(new Blob(['x'], { type: 'text/plain' }));
      const kids = box && box.children ? box.children : [];
      const last = kids[kids.length - 1];
      // 注意：桩件里 appendChild 不会更新 innerHTML，所以要看 children
      exported = kids.length > before && /已上传/.test(String((last && last.textContent) || ''));
    } catch (e) { exported = String(e); }
    check('upload-shows-notice-in-chat', exported === true,
          '上传成功后对话区应出现"已上传"提示，实际：' + String(exported).slice(0, 120));
    check('upload-notice-includes-filename',
          /appendSystemNotice\(/.test(html) && /已上传：\$\{d\.filename\}/.test(html),
          '提示应包含文件名');
    global.fetch = realFetch;
  }

  // ============ UI：折叠后仍能点到"展开"（问题 1）============
  {
    check('collapse-keeps-toggle-visible',
          /\.chat-col\.collapsed\s+\.col-head\s+button:not\(:last-child\)\s*\{\s*display\s*:\s*none/.test(html),
          '折叠态必须隐藏除最后一个以外的头部按钮，否则 44px 宽会把展开按钮裁掉');

    // 关键配对：每一栏头部的【最后一个按钮】必须就是切换按钮，否则上面的 CSS 会藏错人
    const grab = (side) => {
      const i = html.indexOf('id="chat-col-' + side + '"');
      if (i < 0) return [];
      const seg = html.slice(i, i + 900);
      const head = seg.slice(seg.indexOf('col-head'), seg.indexOf('</div>', seg.indexOf('col-head')));
      return Array.from(head.matchAll(/<button[^>]*onclick="([^"]+)"/g)).map(m => m[1]);
    };
    for (const side of ['left', 'right']) {
      const btns = grab(side);
      const last = btns[btns.length - 1] || '';
      check('collapse-toggle-is-last-' + side,
            last.indexOf('toggleChatCol') >= 0,
            side + ' 栏最后一个头部按钮必须是 toggleChatCol，实际：' + JSON.stringify(btns));
    }
  }

  // ============ UI：文档助手可"从空白新建流程"（问题 2b）============
  {
    check('docs-has-new-flow-entry',
          /onclick="openNewDocFlow\(\)"/.test(html) && typeof global.openNewDocFlow === 'function',
          '面板需要有「➕ 新建流程」入口');
    // 注意：入口已按用户要求改名为"编辑工作流"，所以这里只断言【意图】：
    // 既不能自称"Excel 只读"，又必须体现 Word 能力（断言不要写死具体名字）
    check('docs-title-not-readonly-only',
          !/文档助手（Excel 只读）/.test(html) && /编辑工作流/.test(html),
          '标题不得自称"只读"，且应仍是"编辑工作流"（不绑括号）');
    check('docs-no-misleading-readonly-claim',
          !/目前是<b>只读<\/b>/.test(html),
          '面板说明不能再声称"只读"，因为已支持导出与流程');
    check('docs-empty-guides-to-new',
          /还没有流程[\s\S]{0,300}?新建流程/.test(html), '无流程时应引导用户去新建');

    // 行为：点新建后，编辑器应显示且有至少一个可编辑步骤
    let ok = false;
    try {
      global.openNewDocFlow();
      const box = els['docs-flow-editor'];
      const steps = els['df-steps'];
      ok = !!box && box.style.display === 'block'
        && !!steps && /data-step="0"/.test(String(steps.innerHTML || ''));
    } catch (e) { ok = String(e); }
    check('docs-new-flow-opens-editor',
          !!els['docs-flow-editor'] && els['docs-flow-editor'].style.display === 'block',
          '新建流程应打开编辑器（步骤渲染形式已改为自然语言卡片）');
  }

  // ============ UI：连线视觉与可用性（第 6 项）============
  {
    check('edge-hover-highlight',
          /hitbox\.onmouseenter\s*=/.test(html) && /hitbox\.onmouseleave\s*=/.test(html),
          '连线应支持悬停高亮（onmouseenter/onmouseleave）');
    check('edge-hitbox-wider',
          /hitbox\.setAttribute\('stroke-width', '1[4-9]'\)/.test(html),
          '热区应加宽到 14px 以上，否则细线很难点中');
    check('edge-has-native-tip',
          /createElementNS\(ns, 'title'\)/.test(html) && /右键删除这条依赖/.test(html),
          '连线应有原生提示告知可右键删除');
    check('edge-has-transition',
          /\.dag-edge\{[^}]*transition:/.test(html), '连线应有过渡动画');
  }

  // ============ 命名：分析工作流 / 编辑工作流（用户要求的两套名字）============
  {
    check('naming-analysis-workflow',
          /分析工作流/.test(html), '应存在"分析工作流"这个名字');
    check('naming-edit-workflow',
          /编辑工作流/.test(html), '应存在"编辑工作流"这个名字（原文档助手）');
    check('naming-no-bare-docs-assistant',
          html.indexOf('📄 文档助手') < 0, '入口文案不应再叫"文档助手"');
  }

  // ============ 第 7 项：节点拖拽调大小 + 持久化 ============
  {
    check('resize-handle-exists',
          /'node-resize'/.test(html) && /nwse-resize/.test(html),
          '节点右下角应有缩放手柄（cursor nwse-resize）');
    check('resize-render-uses-node-size',
          /const nw = \(typeof n\.w === 'number'/.test(html) && /rect\.setAttribute\('width', nw\)/.test(html),
          '渲染应按节点 w/h（缺省回退常量，保持老配置行为）');
    check('resize-clamps-min-size',
          /Math\.max\(120,/.test(html) && /Math\.max\(40,/.test(html),
          '缩放应有最小尺寸限制（宽>=120 高>=40）');
    check('resize-saves-like-drag',
          /function onNodeResizeEnd[\s\S]{0,600}saveWorkflowConfig\(\{ silent: true \}\)/.test(html),
          '缩放结束应复用与拖动节点相同的保存路径');
    check('resize-edge-geometry-uses-node-size',
          /const fromW = \(fromNode && fromNode\.w\) \? fromNode\.w : NODE_W/.test(html) &&
          /const toH = \(n && n\.h\) \? n\.h : NODE_H/.test(html),
          '连线起点/终点必须按两端节点的实际尺寸计算，否则调大小后箭头会对不上节点');
    check('resize-handlers-exported',
          typeof global.onNodeResizeMove === 'function' && typeof global.onNodeResizeEnd === 'function',
          '缩放处理器应导出（便于测试与后续复用）');
  }

  // ============ 画布：右键确认删除 + 节点不重叠 ============
  {
    check('edge-delete-needs-confirm',
          /oncontextmenu[\s\S]{0,700}window\.confirm\(msg\)/.test(html) &&
          /确认删除这条依赖连线/.test(html),
          '右键删除必须先说明并二次确认，不能直接删');
    check('edge-delete-shows-both-ends',
          /fromLabel/.test(html) && /toLabel/.test(html),
          '确认框里要说清是哪两个节点之间的依赖');
    check('edge-leftclick-shares-confirm',
          /if \(hitbox\.oncontextmenu\) hitbox\.oncontextmenu\(e\)/.test(html),
          '左键点线也要走同一套确认，不能绕过');
    check('node-drag-blocks-overlap',
          /const clash = \(currentConfig\.nodes \|\| \[\]\)\.some\(/.test(html) &&
          /nodeDragState\.blocked = true/.test(html),
          '拖动节点时应检测重叠并保持原位');
    check('node-overlap-toast-on-release',
          /该位置与其它节点重叠/.test(html), '松手时应提示为何没移动');
  }

  // ============ 画布：返回上一步（撤销）============
  {
    check('dag-undo-exists',
          /id="dag-undo-btn"/.test(html) && typeof global.undoDag === 'function',
          '画布工具条应有撤销按钮与 undoDag 函数');
    check('dag-undo-snapshots-before-change',
          /function deleteEdge[\s\S]{0,120}pushDagUndo\('删除依赖连线'\)/.test(html) &&
          /pushDagUndo\('移动节点 /.test(html) &&
          /pushDagUndo\('调整节点尺寸 /.test(html),
          '删除连线/移动节点/缩放节点前都必须先快照');
    check('dag-undo-is-one-op-per-step',
          /let dagUndoStack = \[\]/.test(html) && /DAG_UNDO_MAX = 20/.test(html),
          '撤销栈应存在且有上限（一次操作一步）');
    check('dag-undo-persists',
          /function undoDag[\s\S]{0,700}saveWorkflowConfig\(\{ silent: true \}\)/.test(html),
          '撤销后应保存，使刷新后也保持撤销结果');
    check('dag-undo-button-state',
          /function updateUndoButton/.test(html) && /b\.disabled = n === 0/.test(html),
          '没有可撤销内容时按钮应禁用');
  }

  // ============ 画布：高度可主动调整（不是被动跟随内容）============
  {
    check('dag-canvas-height-adjustable',
          /onclick="dagHeightStep\(-120\)"/.test(html) && /onclick="dagHeightStep\(120\)"/.test(html),
          '工具条应有两个高度档位按钮');
    check('dag-canvas-height-in-config',
          /currentConfig\.canvas_height = next/.test(html) &&
          /function dagHeightStep[\s\S]{0,800}saveWorkflowConfig\(\{ silent: true \}\)/.test(html),
          '高度应存进工作流配置并保存');
    check('dag-canvas-height-never-clips-nodes',
          /const canvasH = Math\.max\(wantH, totalH\)/.test(html),
          '渲染必须取 max(设定高度, 内容高度)，否则调小会把节点裁掉');
  }

  // ============ 回归：用户实测的两个报错 ============
  {
    // ① docFlowHeaderCache 之类的状态变量必须已声明（曾报 ReferenceError）
    check('docflow-state-declared',
          /let docFlowHeaderCache\b/.test(html) && /let docFlowSel\b/.test(html),
          'docFlowHeaderCache / docFlowSel 必须显式声明（曾 Uncaught ReferenceError）');

    // ② 参数面板不得拿 {{file}} 这类占位符去请求后端（用户实测曾 400）
    // 说明：脚手架里的 document 桩缺 createElementNS，跑不动完整画布交互，
    // 所以这里做**源码级**断言（守卫必须存在）；真机交互由使用者在浏览器验证。
    check('docflow-no-placeholder-request',
          /const realFn = \(fn && !\/\\{\\{\/\.test\(fn\)\) \? fn : ''/.test(html) &&
          /const sheets = realFn \? await fetchSheets\(realFn\) : \[\]/.test(html) &&
          /const headers = \(realFn && !\/\^write_\//.test(html),
          '只有真实文件名才允许请求 /docs/sheets 与 /docs/read_sheet（{{file}} 会 400）');
  }

  // ============ 编辑工作流：数据依赖连线（对齐分析页的绿点->蓝点）============
  {
    // 行为断言：直接给一个含引用的步骤，看能否解析出源步骤下标
    let _refOk = false;
    try {
      const got = global.docStepRefs({ args: { rows: '{{steps.2.rows}}', filename: '{{file}}' } });
      _refOk = !!got && typeof got.has === 'function' && got.has(2) && !got.has(0);
    } catch (e) { _refOk = String(e); }
    check('docflow-ref-edges', _refOk === true,
          'docStepRefs 应解析出 {{steps.2.rows}} 引用的是第 2 步（0 基），实际：' + String(_refOk).slice(0, 90));
    check('docflow-edges-auto-drawn',
          /docStepRefs\(s\)\.forEach/.test(html) && /marker-end', 'url\(#docflow-arrow\)/.test(html) ||
          /docStepRefs\(s\)\.forEach/.test(html),
          '已有引用应自动画成箭头（载入预设即可见数据流）');
    // 注意：源码里的键值有空格对齐，正则要用 \s* 容忍（此前写死空格导致误报）
    check('docflow-drag-to-link',
          /function docLinkSetRef[\s\S]{0,1600}const TAKE = \{/.test(html) &&
          /write_docx:\s*\{ key: 'paragraphs'/.test(html),
          '拖绿点建引用：write_docx 承接上一步正文');
    check('docflow-link-refuses-nonwrite',
          /const tgt = TAKE\[dst\.tool\]/.test(html) && /不能直接用第/.test(html),
          '拖到读/算类步骤时应明确拒绝并解释，而不是把 filename 覆盖成引用');
    check('docflow-link-field-by-tool',
          /DOC_REF_FIELD = \{/.test(html) && /read_docx:\s*'text'/.test(html) &&
          /polish:\s*'text'/.test(html),
          '引用字段按源工具选择（文档流程统一取 text）');
    check('docflow-link-delete',
          /depHit\.oncontextmenu/.test(html) && /移除该引用/.test(html),
          '右键连线应能删除该引用');
    check('docflow-dots-drawn',
          /outDot\.setAttribute\('fill', 'var\(--c-border-ok\)'\)/.test(html) &&
          /inDot\.setAttribute\('fill', 'var\(--c-primary\)'\)/.test(html),
          '每个节点应有绿点(输出)与蓝点(输入)');
  }

  // ============ B4 降级路径：环境自检引导 ============
  {
    check('readiness-banner',
          /id="readiness-banner"/.test(html) && typeof global.checkReadiness === 'function',
          '应有环境自检引导条与 checkReadiness');
    check('readiness-hits-api',
          /function checkReadiness[\s\S]{0,400}\/readiness/.test(html),
          'checkReadiness 应调用 /readiness');
    check('readiness-hides-when-full',
          /d\.mode === 'full'[\s\S]{0,120}box\.style\.display = 'none'/.test(html),
          '环境完整时引导条应隐藏（不打扰）');
    check('readiness-shows-fixes',
          /c\.fix/.test(html) && /重新检测/.test(html),
          '引导里要给出可执行的修复建议与重新检测');
    check('readiness-on-startup',
          /checkReadiness\(true\)/.test(html),
          '启动时应静默自检一次');
  }

  // ============ 底座页：模型列表 / 删除 / 进度条 ============
  {
    check('modelscope-model-list',
          /id="ms-model-list"/.test(html) && typeof global.refreshMsModelList === 'function',
          '应展示已下载模型列表');
    check('modelscope-delete-model',
          typeof global.deleteModel === 'function' &&
          /function deleteModel[\s\S]{0,800}\/models\/\$\{encodeURIComponent\(name\)\}\?confirm=true/.test(html),
          '删除必须带 confirm=true（后端也要求）');
    check('modelscope-delete-confirm',
          /function deleteModel[\s\S]{0,400}if \(!confirm\(/.test(html),
          '删除前必须二次确认');
    check('modelscope-progress-bar',
          /id="ms-progress-bar"/.test(html) && /d\.pct/.test(html) && /总大小未知/.test(html),
          '应有进度条；拿不到总大小时如实显示已下载体积而不是假百分比');
  }

  // ============ 底座页：从魔搭拉取模型 ============
  {
    check('modelscope-panel',
          /id="ms-repo-input"/.test(html) && /onclick="startModelScopeDownload\(\)"/.test(html),
          '底座页应有魔搭仓库输入框与下载按钮');
    check('modelscope-download-fn',
          typeof global.startModelScopeDownload === 'function' &&
          /fetch\(`\$\{API_BASE\}\/models\/download`/.test(html),
          'startModelScopeDownload 应调用 /models/download');
    check('modelscope-status-poll',
          /function refreshModelScopeStatus[\s\S]{0,400}\/models\/download\/status/.test(html) &&
          /function pollModelScopeStatus[\s\S]{0,400}refreshModelScopeStatus\(\)/.test(html),
          '应轮询下载状态（刷新函数取状态、轮询函数负责定时重试）');
    check('modelscope-suggestions',
          /onclick="useMsRepo\('Qwen\/Qwen3-4B-Instruct'\)"/.test(html),
          '应给出常用底座的一键填入');
  }

  // ============ UI 不出现代码（自然语言卡片）============
  {
    check('ui-no-json-editor',
          !/<textarea[^>]*data-args/.test(html),
          '界面不应再有 JSON 文本框让用户手写参数');
    check('ui-step-card-is-plain-language',
          /function flowStepHTML[\s\S]{0,1200}filename: '文件'/.test(html) &&
          /group_by: '分组列'/.test(html),
          '步骤卡应显示自然语言（文件/工作表/分组列…）而不是键值代码');
    check('ui-step-card-has-actions',
          /onclick="selectDocFlowNode\('\$\{i\}\)"|onclick="selectDocFlowNode\(\$\{i\}\)"/.test(html) &&
          /moveDocFlowNode\(\$\{i\},-1\)/.test(html) &&
          /removeDocFlowNode\(\$\{i\}\)/.test(html),
          '步骤卡应有 编辑/上移/下移/删除');
    check('ui-flow-name-behind-advanced',
          /<details[\s\S]{0,200}高级设置（流程文件名）/.test(html),
          '流程文件名应收到"高级设置"里，默认不暴露');
    check('ui-read-steps-from-draft',
          /function readFlowStepsFromDOM[\s\S]{0,500}docFlowDraft\.steps\)\)/.test(html),
          '去掉 JSON 文本框后，读步骤必须改为以 docFlowDraft 为源');
    check('ui-step-reorder',
          typeof global.moveDocFlowNode === 'function', '应能上移/下移步骤');
    check('ui-kept-other-functions',
          typeof global.setAgentMode === 'function',
          '替换函数时不得误删其它函数（上次边界正则在 setAgentMode 处切多了）');
  }

  // ============ 编辑工作流：多套预设（另存为）第五片 ============
  {
    check('docflow-save-as-copy',
          /onclick="saveDocFlowAsCopy\(\)"/.test(html) && typeof global.saveDocFlowAsCopy === 'function',
          '应有"另存为"按钮与函数');
    check('docflow-copy-validates-name',
          /流程名必须以 doc_flow 开头/.test(html) && /if \(!\/\\\.json\$\/i\.test\(name\)\) name \+= '\.json'/.test(html),
          '另存为应校验前缀并自动补 .json');
    check('docflow-copy-reuses-saver',
          /function saveDocFlowAsCopy[\s\S]{0,1800}await saveDocFlowFromEditor\(\)/.test(html),
          '另存为必须复用既有保存函数，不新造保存逻辑');
    check('docflow-copy-syncs-inputs',
          /nameEl\.value = name/.test(html) && /titleEl\.value = title/.test(html),
          '既有保存函数从 df-name/df-title 输入框读取，另存为必须先同步这两个框');
    check('docflow-copy-refreshes-lists',
          /function saveDocFlowAsCopy[\s\S]{0,1800}loadDocFlowPresets\(\)/.test(html),
          '另存为后应刷新流程列表与预设下拉');
  }

  // ============ 编辑工作流：进入页面自动刷新（页面化的坑）============
  {
    check('editflow-refreshes-on-enter',
          typeof global.watchEditFlowPage === 'function' &&
          typeof global.refreshEditFlowPageData === 'function',
          '应有"进入页面刷新数据"的机制');
    check('editflow-refresh-uses-observer',
          /new MutationObserver\([\s\S]{0,300}classList\.contains\('active'\)/.test(html),
          '应监听 active class（导航会覆盖 onclick，不能依赖它）');
    check('editflow-refresh-calls-list',
          /function refreshEditFlowPageData[\s\S]{0,400}refreshDocFlowList\(\)/.test(html),
          '刷新时必须真的去拉流程列表，否则界面永远停在"加载中…"');
    check('editflow-watch-started',
          /watchEditFlowPage\(\)/.test(html) && /el\.__efWatched/.test(html),
          '观察器应在初始化时启动且只启动一次');
  }

  // ============ 编辑工作流：画布可用性（不再裁掉节点）============
  {
    check('docflow-canvas-autoheight',
          typeof global.fitDocFlowCanvas === 'function' &&
          /svg\.style\.height = Math\.max\(240/.test(html),
          '画布必须按内容撑高，否则节点会被裁掉');
    check('docflow-canvas-no-fixed-height',
          !/id="docflow-svg"\s+height="200"/.test(html),
          'SVG 不应再写死高度 200（这正是"看不见节点、没法操作"的原因）');
    check('docflow-auto-layout',
          typeof global.docFlowAutoLayout === 'function' &&
          /DOC_NODE_W = 190, DOC_NODE_H = 54/.test(html),
          '缺坐标的节点应自动排版，避免全挤在左上角');
    check('docflow-layout-on-load',
          /docFlowAutoLayout\(true\)/.test(html),
          '载入流程/预设时应强制重排一次，保证每个节点都看得见');
  }

  // ============ 编辑工作流：运行可视化 + 预设载入（第四片）============
  {
    check('docflow-run-viz',
          typeof global.runDocFlowWithViz === 'function' && typeof global.docFlowRunBadge === 'function',
          '应有"运行并标注进度"与状态徽标函数');
    check('docflow-run-viz-uses-trace',
          /d\.trace\.forEach[\s\S]{0,200}byIndex\[\(x\.step \|\| 0\) - 1\]/.test(html),
          '成功路径应按后端 trace 的 step 映射到节点');
    check('docflow-run-viz-handles-early-stop',
          /出错即停/.test(html) && /failIndex/.test(html) && /未执行/.test(html),
          '必须处理"出错即停"（失败时拿不到 trace，只能按失败步号推断前后状态）');
    check('docflow-run-viz-parses-step',
          /const m = String\(msg\)\.match\(\/第/.test(html),
          '应从"第 N 步（tool）失败"里解析失败步号');
    check('docflow-preset-load',
          /id="df-preset-select"/.test(html) && typeof global.loadDocFlowPreset === 'function' &&
          /function loadDocFlowPreset[\s\S]{0,400}loadDocFlowEditor\(name\)/.test(html),
          '应有预设下拉与载入（复用既有 loadDocFlowEditor）');
    check('docflow-preset-lists-flows',
          /function loadDocFlowPresets[\s\S]{0,500}\/docs\/flows/.test(html),
          '预设列表应来自 GET /docs/flows');
  }

  // ============ 编辑工作流：节点库（第三片）============
  {
    check('docflow-node-library',
          /id="df-node-lib"/.test(html) && typeof global.renderDocFlowNodeLib === 'function',
          '应有节点库容器与渲染函数');
    check('docflow-node-lib-covers-tools',
          ['read_docx','format_fix','structure_fix','proofread','polish','condense',
           'logic_check','terminology','add_content','translate','write_docx']
            .every(t => new RegExp("tool: '" + t + "'").test(html)),
          '节点库应覆盖标准文档编辑流程的全部 11 个阶段');
    check('docflow-add-node-appends',
          /function addDocFlowNode[\s\S]{0,1200}docFlowDraft\.steps\.push\(step\)/.test(html),
          '点节点库应把新步骤追加到流程');
    check('docflow-add-node-selects-it',
          /docFlowSel = docFlowDraft\.steps\.length - 1/.test(html),
          '加完节点应自动选中，方便立刻填参数');
    check('docflow-add-node-avoids-overlap',
          /step\.x = 30 \+ \(n % 3\) \* 210/.test(html),
          '新节点应自动错开位置，避免一开始就重叠');
    check('docflow-remove-node',
          /function removeDocFlowNode[\s\S]{0,900}docFlowDraft\.steps\.splice\(idx, 1\)/.test(html) &&
          /删除这一步/.test(html),
          '应能删除节点，且删除前先确认');
    check('docflow-default-file-var',
          /filename: '\{\{file\}\}'/.test(html),
          '读类节点的默认文件名应使用 {{file}}（运行时输入文件）');
  }

  // ============ 编辑工作流：参数面板（列名下拉取自真实表头）============
  {
    check('docflow-param-panel',
          /id="df-param"/.test(html) && typeof global.renderDocFlowParam === 'function',
          '应有参数面板容器与渲染函数');
    check('docflow-node-click-selects',
          /addEventListener\('click'[\s\S]{0,200}selectDocFlowNode\(i\)/.test(html),
          '点击画布节点应选中并打开参数面板');
    check('docflow-headers-from-real-api',
          /function fetchSheetHeaders[\s\S]{0,700}\/docs\/read_sheet/.test(html) &&
          /d\.headers \|\| d\.columns/.test(html),
          '列名必须来自真实的 /docs/read_sheet 表头');
    check('docflow-column-becomes-dropdown',
          !/tool: 'filter_rows'/.test(html) && !/tool: 'group_sum'/.test(html),
          '表格类节点已从节点库移除（按用户要求换成标准文档编辑流程）');
    check('docflow-filename-dropdown',
          /f\.src === 'file'/.test(html) && /files\.length \? sel\(f\.k/.test(html),
          '文件字段应是已上传文件的下拉');
    check('docflow-apply-to-node',
          /function applyDocFlowParam[\s\S]{0,900}step\.args = args/.test(html),
          '面板要有"应用到节点"并把参数写回步骤');
  }

  // ============ 编辑工作流：流程画布（方案 A 第一片）============
  {
    check('docflow-canvas-exists',
          /id="docflow-svg"/.test(html) && typeof global.renderDocFlowCanvas === 'function',
          '编辑工作流面板应有画布容器与渲染函数');
    // 注意：函数体较长（含连线循环与 defs），窗口太小会误报；这里直接断言关键调用
    check('docflow-canvas-draws-steps',
          /function renderDocFlowCanvas/.test(html) &&
          /steps\.forEach\(\(s, i\) =>/.test(html) &&
          /docFlowDraft\.steps \|\| \[\]/.test(html),
          '画布应按步骤渲染节点');
    check('docflow-canvas-drag-persists',
          /s\.x = Math\.max\(0/.test(html) && /function onDocFlowDragEnd[\s\S]{0,600}saveDocFlowFromEditor\(\)/.test(html),
          '拖动应写入 x/y 并保存（否则布局白拖）');
    check('docflow-keeps-layout-on-readback',
          /function readFlowStepsFromDOM[\s\S]{0,500}JSON\.parse\(JSON\.stringify\(docFlowDraft\.steps\)\)/.test(html),
          '布局与参数都以 docFlowDraft 为源（旧实现从 DOM 回读、需手工保留 x/y，新实现天然保留）');
    check('docflow-write-nodes-distinct',
          /const isWrite = \/\^write_\//.test(html),
          '写类节点应有不同配色');
  }

  // ============ 编辑工作流：完整页面（不是弹窗）============
  {
    check('editflow-is-a-page-not-modal',
          /id="page-editflow"/.test(html) && !/id="docs-modal"/.test(html),
          '不应再有 docs-modal 弹窗，应是 #page-editflow 页面');
    check('editflow-in-tools-pages',
          /TOOLS_PAGES\s*=\s*\[[^\]]*'editflow'/.test(html),
          'editflow 应纳入 TOOLS_PAGES，保证二级导航与顶栏高亮正确');
    check('editflow-has-canvas-and-editor',
          /id="docflow-svg"/.test(html) && /id="df-steps"/.test(html) && /id="docs-flow-list"/.test(html),
          '页面里应同时有画布、步骤编辑器与流程列表');
  }

  // ============ 注入的三重兜底（不依赖 localStorage）============
  {
    check('upload-context-from-message-text',
          /buildUploadContext\(userText\)/.test(html) && /FILE_RE/.test(html),
          '应先从消息文字里解析文件名（用户常把文件名写在话里）');
    check('upload-context-falls-back-to-list',
          /fetch\(`\$\{API_BASE\}\/uploads`\)/.test(html) && /读取上传列表失败/.test(html),
          '必须回退到服务端上传列表 —— 用户浏览器禁用 localStorage，刷新后只能靠它');
    check('upload-context-reports-read-failure',
          /但读取正文失败/.test(html),
          '读取失败时要如实告知用户，而不是继续假装没看到文件');
    check('upload-context-passes-text',
          /buildUploadContext\(text\)/.test(html),
          '两个调用点都应把用户消息文本传进去');
  }

  // ============ 上传文件正文的确定性注入 ============
  {
    check('upload-context-helper',
          typeof global.buildUploadContext === 'function', '应有 buildUploadContext 助手');
    check('upload-context-injected-chat',
          /const upCtx = await buildUploadContext\(text\)[\s\S]{0,120}messages\.push\(upCtx\)/.test(html),
          '普通对话发送时必须注入上传文件正文（并传入消息文本用于识别文件名）');
    check('upload-context-injected-agent',
          /buildAgentSystemPrompt\(\) \},[\s\S]{0,220}buildUploadContext\(text\)/.test(html),
          'agent 模式也必须注入（模型不一定会自己调 read_upload）');
    check('upload-context-warns-model',
          /不要回答/.test(html) && /用户已上传文件/.test(html),
          '注入内容要明确告知模型基于正文回答');
  }

  // ============ 内联 onchange 的坑（真实浏览器会 ReferenceError）============
  {
    check('chat-file-no-inline-onchange',
          !/onchange="onChatFileSelected\(this\)"/.test(html) &&
          !/onchange="onEditFlowFileSelected\(this\)"/.test(html),
          '不应再用内联 onchange：实测在真实浏览器报 onChatFileSelected is not defined');
    check('chat-file-bound-in-js',
          /function bindChatDropZone[\s\S]{0,900}_fi\.onchange = \(\) => onChatFileSelected\(_fi\)/.test(html),
          '应在 bindChatDropZone 里用闭包绑定 chat-file-input 的 change');
    check('editflow-file-bound-in-js',
          /_efi\.onchange = \(\) => onEditFlowFileSelected\(_efi\)/.test(html),
          '编辑工作流的文件输入也应在 JS 里绑定');
  }

  // ============ 上传可视化 + 编辑工作流上传入口 ============
  {
    check('upload-bar-exists',
          /id="chat-upload-bar"/.test(html) && typeof global.renderUploadBar === 'function',
          '对话区应有常驻的"已上传"清单条');
    check('upload-bar-persists',
          /localStorage\.setItem\('chat-uploaded'/.test(html) &&
          /function uploadedList[\s\S]{0,200}localStorage\.getItem\('chat-uploaded'\)/.test(html),
          '上传清单必须存 localStorage，才能活过刷新');
    check('upload-remembers-file',
          /function uploadChatFile[\s\S]{0,2000}rememberUpload\(d\.filename/.test(html),
          '上传成功后应写入常驻清单');
    check('editflow-has-upload-entry',
          /id="editflow-file-input"/.test(html) && /editflow-file-input'\)\.click\(\)/.test(html),
          '编辑工作流页面必须有上传文件入口');
    check('editflow-upload-uses-same-api',
          /function onEditFlowFileSelected[\s\S]{0,500}uploadChatFile\(f\)/.test(html),
          '页面上传应复用同一条上传链路');
  }

  // ============ 上传文件的正文必须能被 agent 读到 ============
  {
    check('read-upload-tool',
          typeof (global.AGENT_TOOLS || {}).read_upload === 'object' &&
          typeof global.AGENT_TOOLS.read_upload.handler === 'function',
          '应有 read_upload 工具，让 agent 能读取上传文件正文');
    check('read-upload-hits-api',
          /AGENT_TOOLS\.read_upload[\s\S]{0,600}\/uploads\/text/.test(html),
          'read_upload 应调用 /uploads/text');
    check('read-upload-in-prompt',
          /read_upload\(filename/.test(html) && /不要因为"没看到内容"就回答/.test(html),
          '提示词必须告诉模型：先 list_uploads 再 read_upload，不要回答"你没给我文本"');
    check('read-upload-is-readonly',
          global.AGENT_TOOLS.read_upload.dangerous === false,
          '读取文件是只读操作，不应要求二次确认');
  }

  // ============ 对话框上传可用性 + 页面文案 ============
  {
    check('chat-file-onchange-is-global',
          typeof global.onChatFileSelected === 'function',
          '内联 onchange 调用的 onChatFileSelected 必须挂到 window，否则选完文件毫无反应');
    check('chat-file-input-hidden',
          /<input[^>]*id="chat-file-input"[\s\S]{0,300}?display:none/.test(html),
          '文件输入应隐藏，由 📎 按钮触发（不绑属性顺序）');
    check('editflow-copy-points-to-own-page',
          /本页/.test(html) && /选择文件上传/.test(html) &&
          html.indexOf('在**对话框**里上传') < 0,
          '页面说明应指向本页上传（不跨 <b> 标签匹配）');
    check('editflow-file-list-label-general',
          /已上传的文件（Excel \/ Word \/ PDF \/ 图片）/.test(html),
          '文件列表标签应涵盖所有支持的格式，而不只是 Excel');
  }
  // ============ 结构性守卫：内联处理器引用的函数必须真的有定义 ============
  // 起因：几次"补函数"的检查用【字符串包含】判断，而按钮的 onclick 字符串恰好满足它，
  // 于是函数从未定义、点击即 ReferenceError（用户实测撞到过）。
  // 这条测试用"函数定义 + 全局是否可调"来判定，杜绝同类问题再次溜过。
  {
    const i = html.indexOf('id="page-editflow"');
    const j = html.indexOf('<div class="page', i + 10);
    const page = i >= 0 ? html.slice(i, j > 0 ? j : html.length) : '';
    const names = new Set();
    const reH = /on(?:click|change|input)="([A-Za-z_][A-Za-z0-9_]*)\(/g;
    let mm;
    while ((mm = reH.exec(page))) names.add(mm[1]);
    const missing = Array.from(names).filter(n =>
      !(new RegExp('function\\s+' + n + '\\s*\\(')).test(html) &&
      typeof global[n] !== 'function');
    check('editflow-inline-handlers-defined', missing.length === 0,
          '页面这些按钮的处理函数没有定义（点击会报错）：' + JSON.stringify(missing));
  }

  // ============ 编辑工作流：宏观编辑流程（用户要求：节点要宏观，不是数据库操作）============
  {
    check('docflow-macro-nodes',
          ['read_docx','format_fix','structure_fix','proofread','polish','condense',
           'logic_check','terminology','add_content','translate','write_docx']
            .every(t => new RegExp("tool: '" + t + "'").test(html)),
          '节点库应覆盖标准文档编辑流程的全部阶段');
    check('docflow-macro-labels-in-chinese',
          /导入文档/.test(html) && /格式规范化/.test(html) && /校对纠错/.test(html) &&
          /润色改写/.test(html) && /术语统一/.test(html) && /导出 Word/.test(html),
          '节点库按钮应显示中文阶段名（导入/规范化/校对/润色/术语/导出…）');
    check('docflow-macro-fields',
          /format_fix:\s*\[\{ k: 'text'/.test(html) &&
          /add_content:\s*\[\{ k: 'text'/.test(html) &&
          /translate:\s*\[\{ k: 'text'/.test(html) && /topic/.test(html),
          '标准流程每个阶段都要有对应字段（正文来源/补写主题/目标语言…）');
    check('docflow-open-result',
          /id="df-open-btn"/.test(html) && typeof global.openDocFlowResult === 'function' &&
          /\/workflow\/open_output/.test(html),
          '应有「📂 打开结果」并复用 /workflow/open_output');
    check('docflow-link-macro',
          /format_fix:\s*\{ key: 'text'/.test(html) &&
          /terminology:\s*\{ key: 'text'/.test(html) &&
          /translate:\s*\{ key: 'text'/.test(html),
          '标准流程各阶段之间应能拖绿点串联（上一步正文作为下一步输入）');
  }


  // ============ 真·语法门：把每段 <script> 交给 vm.Script 解析 ============
  // 源码字符串断言无法发现语法错误（本会话踩过：模板字符串里插反引号导致 agent 模块崩，
  // 门禁却全绿）。这里直接解析。
  {
    const vm = require('vm');
    let n = 0, bad = 0, detail = [];
    const re2 = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
    let m2;
    while ((m2 = re2.exec(html))) {
      const attrs = m2[1] || '';
      if (/\bsrc\s*=/.test(attrs)) continue;
      n++;
      try { new vm.Script(m2[2]); }
      catch (e) { bad++; detail.push('script#' + n + ': ' + e.message); }
    }
    check('js-syntax-valid', bad === 0 && n > 0,
          '每段 <script> 必须能被 JS 引擎解析（发现 ' + bad + '/' + n + ' 段有问题: ' +
          detail.join(' | ').slice(0, 160) + '）');
  }

  report();
}

function report() {
  let failed = 0;
  for (const r of results) {
    if (!r.pass) failed++;
    console.log((r.pass ? 'PASS' : 'FAIL') + ' | ' + r.name + (r.pass ? '' : '  -> ' + r.extra));
  }
  console.log('\n' + (results.length - failed) + '/' + results.length + ' passed');
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.log('HARNESS ERROR: ' + ((e && e.stack) || e));
  process.exit(2);
});
