/* 反射学习页的脚本（外置 ✓）
 *
 * 为什么外置而不是内联在 index3.html：
 *   前端测试台会把 index3.html 里的 <script> 抽出来在 node vm 里执行 ✓
 *   内联的话我这段会在 vm 里跑 ✗ 实测直接把测试弄挂（HARNESS ERROR: formatApiError is not a function ✗，
 *   还原后 370/370 ✓ 因果确认 ✓）
 *   放外部、并用 <script src> 引用 → 抽取器拿不到内联内容 ✓ 零干扰 ✓
 *
 * 另外：整段包 try/catch ✓，且只在页面里真的有 #rf-status 时才自动刷新 ✓
 * （在 vm/测试环境里什么都不会发生 ✓）
 */
(function () {
  try {
    var RF_JOB = null, RF_TIMER = null;

    // 批次进度（读 /reflect/status 时一并拿 ✓ 让状态卡显示"跑到第几条"）
    var RF_PROGRESS = '';

    function base() {
      return (typeof API_BASE !== 'undefined' && API_BASE) ? API_BASE : '';
    }
    function api(p, body) {
      return fetch(base() + p, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body)
      }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; },
                           function () { return { ok: r.ok, d: {} }; });
      });
    }
    function log(s) {
      var el = document.getElementById('rf-log');
      if (el) { el.textContent += '\n' + s; el.scrollTop = el.scrollHeight; }
    }

    window.rfStatus = function () {
      api('/reflect/status').then(function (r) {
        RF_PROGRESS = (r.d && r.d.batch) ? ('<b>' + r.d.batch.done + '/' + r.d.batch.total +
          '</b> 条已完成' + (r.d.batch.running ? '（批次运行中 ✓ pid ' + r.d.batch.pid + '）' : '（批次未在运行）') + '<br>') : '';
        var d = r.d || {}, el = document.getElementById('rf-status');
        if (!el) return;
        var dis = d.distilled || {};
        el.innerHTML = (typeof RF_PROGRESS === 'undefined' ? '' : RF_PROGRESS) + '轨迹 <b>' + (d.trajectories || 0) + '</b> 条 · 升级记录 <b>' +
          (d.escalations || 0) + '</b> 条 · 蒸馏文件：' +
          (dis.exists ? '有（' + dis.chars + ' 字符，' + String(dis.mtime || '').slice(0, 19) + '）'
                      : '还没有 ✗') +
          '<br>脚本：' + Object.keys(d.scripts || {}).map(function (k) {
            return k + (d.scripts[k] ? ' ✓' : ' ✗');
          }).join('　') +
          ((d.jobs && d.jobs.length) ? ('<br>最近任务：' + d.jobs.map(function (j) {
            return j.id + '=' + j.state; }).join('　')) : '');
      });
    };

    window.rfRun = function () {
      var ta = document.getElementById('rf-tasks');
      var txt = ((ta && ta.value) || '').trim();
      if (!txt) { alert('先写几条任务'); return; }
      api('/reflect/run', { tasks: txt.split('\n').filter(function (x) { return x.trim(); }) })
        .then(function (r) { RF_JOB = r.d.job_id; log('▶ 已提交任务 ' + RF_JOB); rfPoll(); });
    };

    window.rfEscalate = function (dry) {
      api('/reflect/escalate', { dry_run: !!dry }).then(function (r) {
        RF_JOB = r.d.job_id;
        log((dry ? '🔍 判定' : '🚀 判定并升级') + ' ' + RF_JOB);
        rfPoll();
      });
    };

    window.rfDistill = function () {
      api('/reflect/distill', {}).then(function (r) {
        log('🧪 蒸馏: ' + String((r.d || {}).out || '').slice(0, 400));
        window.rfStatus();
      });
    };

    window.rfIngest = function () {
      api('/reflect/ingest', {}).then(function (r) {
        log('📥 入库: ' + JSON.stringify(r.d).slice(0, 300));
        window.rfStatus();
      });
    };

    function rfPoll() {
      if (RF_TIMER) { clearInterval(RF_TIMER); }
      var seen = 0;
      RF_TIMER = setInterval(function () {
        if (!RF_JOB) return;
        api('/reflect/job/' + RF_JOB).then(function (r) {
          var d = r.d || {}, tail = d.log_tail || [], i;
          for (i = seen; i < tail.length; i++) { log('  | ' + tail[i]); }
          seen = tail.length;
          if (d.state === 'done' || d.state === 'failed') {
            clearInterval(RF_TIMER); RF_TIMER = null;
            log('■ 任务 ' + RF_JOB + ' 结束：' + d.state);
            window.rfStatus();
          }
        });
      }, 3000);
    }

    // 只在真的有这个页面时才自动刷新 ✓（vm/测试环境里没有 → 什么都不做 ✓）
    // 2026-10-11: 改成【每 5 秒】轮询 —— 实测只刷一次会让人以为"没开始"✗
    //（批次每条 ~61 秒 ✓ 不自动刷新就一直显示旧数字 ✓）
    function armAutoRefresh() {
      try {
        if (!document.getElementById('rf-status')) { return; }
        window.rfStatus();
        var label = document.getElementById('rf-auto');
        if (label) {
          var left = 5;
          setInterval(function () {
            left -= 1;
            if (left <= 0) { window.rfStatus(); left = 5; }
            if (label) { label.textContent = '（每 5 秒自动刷新，' + left + 's）'; }
          }, 1000);
        } else {
          setInterval(function () { window.rfStatus(); }, 5000);
        }
      } catch (e) {}
    }
    setTimeout(armAutoRefresh, 1500);
  } catch (e) {
    // 测试台/非页面环境：静默 ✓ 绝不影响别的脚本 ✓
  }
})();
