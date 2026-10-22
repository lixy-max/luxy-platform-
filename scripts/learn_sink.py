#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段3：把一次"本地失败 → 大模型接手"的会话，变成知识库条目。

链路（P-1 修复后的真实链路 ✓）:
   会话轨迹 → 【清洗】 → 【自产条目】 → 【追加落盘（保留最近 N 条）】 → 【入库】

P-1 修复要点（2026-10-15 实测 ✗ → 已修 ✓）:
  旧实现把 handoff 的【单步轨迹】喂给 reflect_distill.distill() ✗
  （steps=[{tool_calls:[{tool: <模型名>, result: <正文>}]}] · tools_used=[<模型名>]）
  而它期待【agent 多步轨迹】形状（真工具调用 + 步数 + 成败 ✓）
  ⇒ 形状不匹配 ⇒ 每次都产出 641 字符的「还没有足够轨迹」空模板 ⇒
    守卫拦住了污染 ✓ 但也因此永远不入库 ✗ 知识库永远长不大 ✗
  ⇒ 现在 learn_sink **自己产条目**（任务 → 结论/要点，≤400 字/条 ✓）
     reflect_distill 保持原用途（服务 agent 多步轨迹 ✓）—— 两条路互补，互不干扰 ✓

P-2 修复要点（清洗按【内容】判碎片，不按【长度】✗）:
  实测: 该用户的真实口气就是短句（「把结果导出来给我」8 字 /「这个不对，重做」7 字 ✗）
  旧规则 25 字一刀切 ⇒ 23 条真实原话只过 10 条 ✗ 与学习目的直接冲突 ✗

为什么把清洗做成【独立一步】:
  实测教训: 原始轨迹里有失败、碎片（任务文件被拆碎）、几十步的长探索 ✗
  直接入库只会污染检索 ✓ 必须先丢干净 ✓ 而且要能看到"丢了什么、为什么"✓
"""
from __future__ import annotations

import json
import re
import sys
import time
import pathlib
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEARN_DIR = ROOT / 'data' / 'learning'
TRAJ_DIR = LEARN_DIR / 'trajectories'
CLEAN_DIR = LEARN_DIR / 'cleaned'

# 自产条目: 每个 kb 只保留最近 N 条（避免 learned_<kb>.md 无限增长 ✗）
ENTRY_KEEP = 30
# 条目硬上限: ≤400 字/条 ✓（P-1 要求）
MAX_ENTRY_CHARS = 400
MAX_TASK_CHARS = 60
MAX_ANSWER_CHARS = 300


def _abs_path_re():
    """要去掉的"本机绝对路径"（你选的 1a: 只去绝对路径 ✓ 不动其它内容）。"""
    return re.compile(r'(/home/[A-Za-z0-9_./-]+|[A-Za-z]:\\\\[^\s"\']+|/mnt/[a-z]/[^\s"\']+)')


def _scrub(text: str) -> str:
    return _abs_path_re().sub('<路径>', str(text or ''))


# ★ 2026-10-15 新增: 提炼出【空模板】的判据
#   实测: 只有 1 条轨迹（且形状不符）时，reflect_distill 会产出「还没有足够轨迹」的模板 ✗
#   这种内容入库 = 往知识库丢垃圾（用户明确要求核对入库数据 ✓）⇒ 必须拦住 ✓
_EMPTY_DISTILL_MARKERS = ('还没有足够轨迹', '还没有报错记录', '从真实轨迹蒸馏出的经验')


def _table_data_rows(text: str) -> int:
    """数【真正的数据行】: 必须出现在 Markdown 表格分隔行（|---|）之后 ✓

    踩过的坑（2026-10-15 实测 ✗）: 空模板里有一行表头 `| 工具 | 成功 | 失败 |`，
    旧判据把它当成"有数据" ⇒ 守卫失效 ⇒ 空模板照样入库 ✗
    """
    rows, seen_sep = 0, False
    for raw in str(text or '').splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if ln.startswith('|') and set(ln) <= set('|-: '):
            seen_sep = True          # 分隔行: |---|---|
            continue
        if ln.startswith('|'):
            if seen_sep:
                rows += 1            # 分隔行之后的表格行 = 真数据 ✓
        else:
            seen_sep = False         # 离开表格 → 重置 ✓
    return rows


def is_empty_distill(md: str) -> bool:
    """判据: 命中 ≥2 个空模板标记，且没有真正的表格数据行 ✓。

    P-1 修复后这个守卫有【两个用途】✓:
      ① 拦住 reflect_distill 的空模板入库（旧用途 ✓ 保留 ✓）
      ② 判断 learn_sink 自产的条目是不是空的（新用途 ✓）
    """
    t = str(md or '')
    hits = sum(1 for m in _EMPTY_DISTILL_MARKERS if m in t)
    return hits >= 2 and _table_data_rows(t) == 0


# ============ P-2: 判"碎片"按【内容】不按【长度】 ============
#   · 含意图词 ⇒ 保留（再短也留 ✓）
#   · 含中文 ⇒ 保留（用户短口气本身就有信息 ✓）
#   · 纯符号 / 单字符无上下文 ⇒ 丢（真垃圾 ✓）
#   · 其余（纯 ASCII 且短）⇒ 按 drop_short_task 阈值兜底丢 ✓
INTENT_WORDS = (
    # 动作
    '加', '改', '删', '跑', '导', '查', '看', '给', '写', '训练', '入库', '重建',
    '挂', '卸', '切换', '统一', '做', '重做', '起', '启', '停', '开', '关', '换',
    '取', '发', '列', '找', '修', '测', '试', '生成', '部署', '启动', '导出', '查询',
    # 指代（用户常用"那个/刚才/最新"指上一轮 ✓）
    '那个', '这个', '刚才', '最新', '上次', '之前', '第一', '第', '条',
    # 状态/反馈（短口气的告知/负反馈也是有效轨迹 ✓）
    '没', '失败', '报错', '错误', '崩', '完', '结果', '状态', '不对', '可以', '行',
)

_NON_CONTENT_RE = re.compile(r'[\W_]+', re.UNICODE)
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')


def content_chars(task: str) -> str:
    """去掉空白与标点后剩下的"实质字符"（中文/字母/数字 ✓）。"""
    return _NON_CONTENT_RE.sub('', str(task or ''))


def has_intent(task: str) -> bool:
    t = str(task or '')
    return any(w in t for w in INTENT_WORDS)


def is_fragment(task: str, minlen: int = 8) -> tuple:
    """判"碎片": 返回 (是碎片?, 原因)。★ 判据是【内容】不是【长度】✓"""
    t = str(task or '').strip()
    if not t:
        return True, '空任务'
    if not content_chars(t):
        return True, '纯符号/无内容'
    if has_intent(t):
        return False, None                      # 短但有意图 ⇒ 保留 ✓
    if _CJK_RE.search(t):
        return False, None                      # 中文短句 = 用户口气 ✓ 有信息 ✓
    if len(t) < int(minlen):
        return True, f'任务过短（{len(t)} < {minlen}）且无意图词 —— 多半是任务被拆碎的片段'
    return False, None


# ============ P-9: 解析云端给的【动作计划】 ============
_PLAN_RE = re.compile(r'^\s*PLAN\s*[:：]\s*\[\s*(.*?)\s*\]\s*$', re.I)
_QUOTED_RE = re.compile(r'["\'“”‘’`]([^"\'“”‘’`]+)["\'“”‘’`]')


def parse_planned_tools(final: str) -> tuple:
    """P-9: 从 final 开头解析一行 `PLAN: ["a","b"]` ⇒ (工具列表, 剥掉该行的正文)。

    容错: 中/英文冒号 · 单/双/中文引号 · 多余空格 · 完全无引号（逗号/空格分隔）✓
    只认【第一个非空行】✗ 不认正文中间的同名行（否则会误剥正文 ✓）
    """
    text = str(final or '')
    lines = text.splitlines()
    idx, m = None, None
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        mm = _PLAN_RE.match(ln)
        if mm:
            idx, m = i, mm
        break                                    # 只看第一个非空行 ✓
    if idx is None or m is None:
        return [], text
    raw = m.group(1)
    tools = _QUOTED_RE.findall(raw)
    if not tools:
        tools = [x.strip() for x in re.split(r'[,，\s]+', raw) if x.strip()]
    tools = [t.strip() for t in tools if t.strip()]
    body = '\n'.join(lines[:idx] + lines[idx + 1:]).strip('\n')
    return tools, body


# ============ P-1: 自产【任务 → 结论/要点】短条目 ============
_ACTION_HINTS = ('先', '再', '然后', '用', '调用', '执行', '设置', '打开', '运行',
                 '检查', '确认', '需要', '必须', '不要', '注意', '应', '可以', '把')


def _one_line(text, limit=None) -> str:
    s = re.sub(r'\s+', ' ', str(text or '')).strip()
    if limit and len(s) > int(limit):
        s = s[:int(limit)].rstrip() + '…'
    return s


def _sentences(text) -> list:
    out = []
    for raw in str(text or '').splitlines():
        ln = re.sub(r'^[-*#>\s]+', '', raw.strip())
        ln = re.sub(r'^\d+[.、)]\s*', '', ln)
        if not ln:
            continue
        for part in re.split(r'(?<=[。！？!?；;])', ln):
            p = part.strip()
            if p:
                out.append(p)
    return out


def _points(final, limit: int = 3) -> list:
    """从 final 里抽 1~3 条【可照做】的句子 ✓（优先含动作词的句子 ✓）。"""
    sents = [s for s in _sentences(final) if 6 <= len(s) <= 80]
    picked = [s for s in sents if any(h in s for h in _ACTION_HINTS)]
    for s in sents:
        if len(picked) >= limit:
            break
        if s not in picked:
            picked.append(s)
    return picked[:limit]


def _answer(final, limit: int = MAX_ANSWER_CHARS) -> str:
    """final 摘要 ≤ limit 字（按句截，尽量不截半句 ✓）。"""
    buf, n = [], 0
    for s in _sentences(final):
        if n + len(s) > limit and buf:
            break
        buf.append(s)
        n += len(s)
        if n >= limit:
            break
    ans = ' '.join(buf) if buf else _one_line(final, limit)
    if len(ans) > limit:
        ans = ans[:limit].rstrip() + '…'
    return ans


def build_entry(traj: dict) -> str:
    """P-1: 直接从一条会话轨迹自产【任务 → 结论/要点】短条目（≤400 字/条 ✓）。

    结构:
      ## 问：<用户任务原话（≤60 字）>
      答：<final 摘要（≤300 字）>
      要点：<从 final 里抽 1~3 条可照做的句子>
      工具计划：<planned_tools，有则 a → b，无则 "无">        ← P-9 ✓

    返回空字符串 = 这条轨迹没有可提炼的正文（调用方据此跳过入库 ✓）。
    """
    task = _one_line(traj.get('task'), MAX_TASK_CHARS)
    final = str(traj.get('final') or '')
    tools = [str(t).strip() for t in (traj.get('planned_tools') or []) if str(t).strip()]
    head = f'## 问：{task}'
    plan = '工具计划：' + (' → '.join(tools) if tools else '无')

    answer = _answer(final, MAX_ANSWER_CHARS)
    pts = _points(final, 3)
    if not content_chars(answer) and not pts:
        return ''                                # 没有正文 ⇒ 条目为空 ✓ 不入库 ✓
    pts_line = ('要点：' + ' / '.join(pts)) if pts else ''

    entry = '\n'.join(x for x in [head, f'答：{answer}', pts_line, plan] if x)
    if len(entry) > MAX_ENTRY_CHARS:             # 硬上限: 先收答 ✓
        fixed = len(head) + len(plan) + len(pts_line) + 3
        room = max(40, MAX_ENTRY_CHARS - fixed)
        answer = _answer(final, min(MAX_ANSWER_CHARS, room))
        entry = '\n'.join(x for x in [head, f'答：{answer}', pts_line, plan] if x)
    if len(entry) > MAX_ENTRY_CHARS:             # 再收到只留 1 条要点 ✓
        pts_line = ('要点：' + pts[0]) if pts else ''
        entry = '\n'.join(x for x in [head, f'答：{answer}', pts_line, plan] if x)
    if len(entry) > MAX_ENTRY_CHARS:             # 最后硬截断（兜底 ✓）
        entry = entry[:MAX_ENTRY_CHARS].rstrip()
    return entry


# ---- 落盘: 追加 + 只留最近 N 条 ✓
_ENTRY_SPLIT_RE = re.compile(r'(?m)^(?=##\s*问：)')


# ★ P-23（2026-10-18）: 入库前质量闸门
#   实测（kb-curator 对 test_use_agent 30 条逐条判读）:
#     免责开场 66.7% ✗ · 工具计划=无 93.3% ✗ · 重复 60% ✗
#   ⇒ 语料主体是"我没有工具所以做不到" ⇒ 被检索进上下文反而有害 ✗
#   规则: 免责模板 且 无工具计划 ⇒ 只落盘做证据，不进知识库 ✓
_DISCLAIMER_RE = re.compile(
    r'(我无法|无法直接|无法为您|我没有(可用的)?工具|未连接|没有连接到|'
    r'请(先)?(补充|提供|告诉|明确|确认)|抱歉[，,]?\s*我|作为(一个)?(AI|语言模型))')


_SECRET_PATTERNS = [
    # ★ P-51（2026-10-19 安全事件）: 真实密码经聊天进了学习链 → 5 处持久化存储 ✗✗
    #   ⇒ 入库前【强制脱敏】✓ 宁可错杀不可漏 ✗
    (re.compile(r'(?i)(密码|口令|password|passwd|pwd)\s*[:：=]?\s*[^\s，。；,;]{4,64}'), r'\1: [REDACTED]'),
    (re.compile(r'(?i)(账号|账户|用户名|username|account)\s*[:：=]?\s*[^\s，。；,;]{2,64}'), r'\1: [REDACTED]'),
    (re.compile(r'(?i)\b(sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{16,})\b'), '[REDACTED_TOKEN]'),
    (re.compile(r'\b1[3-9]\d{9}\b'), '[REDACTED_PHONE]'),
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'), '[REDACTED_EMAIL]'),
]


def redact_secrets(text: str) -> tuple:
    """★ 入库前脱敏（P-51 ✓）· 返回 (脱敏后文本, 命中数)"""
    out = str(text or '')
    n = 0
    for pat, rep in _SECRET_PATTERNS:
        out, k = pat.subn(rep, out)
        n += k
    return out, n


def quality_gate(entry: str, planned_tools) -> tuple:
    """返回 (是否入库, 原因)。保守 ✓ 宁可少入也不污染知识库 ✓"""
    text = str(entry or '')
    tools = [t for t in (planned_tools or []) if t]
    body = text.split('工具计划', 1)[0]
    ans = (body.split('答：', 1)[-1] if '答：' in body else body).strip()
    hits = len(_DISCLAIMER_RE.findall(ans))
    if tools:
        return True, 'ok（有工具计划 ✓）'
    if hits >= 1:
        return False, f'免责模板（命中 {hits} 处）且无工具计划 ⇒ 跳过入库'
    if len(ans) < 40:
        return False, f'答案过短（{len(ans)} 字）且无工具计划 ⇒ 跳过入库'
    return True, 'ok'


def _load_blocks(path) -> list:
    """读文件里已有的【块】（按自产条目头 `## 问：` 切分 ✓）。

    ★ 2026-10-15 迁移坑: 旧版本往同一个文件写过 reflect_distill 的 641 字符空模板 ✗
      如果原样保留，下一次写回就会把空模板带回去 ⇒ 入库污染 ✗ ⇒ 空模板一律丢 ✓
    ★ 同文件还有【别家生产者】: `workflow_learn.py` 也写 `data/learning/learned_<target>.md`
      （格式是 `# 工作流学习：…` 用 `---` 分节 ✗ 不是 `## 问：`）⇒ 那种块必须原样保留 ✓
    """
    try:
        text = Path(path).read_text(encoding='utf-8')
    except Exception:
        return []
    out = []
    for part in _ENTRY_SPLIT_RE.split(text):
        p = part.strip()
        if not p or is_empty_distill(p):     # 空模板（旧 reflect_distill 残留）⇒ 丢 ✗
            continue
        if p not in out:
            out.append(p)
    return out


def load_entries(path) -> list:
    """只取【learn_sink 自产条目】（以 `## 问：` 开头 ✓）—— 用于条数上限/自测 ✓。"""
    return [b for b in _load_blocks(path) if b.startswith('## 问：')]


def _question_of(block: str) -> str:
    """取条目的问句（`## 问：xxx` 那一行 ✓ 用于按问题去重 ✓）"""
    for ln in str(block or '').splitlines():
        ln = ln.strip()
        if ln.startswith('## 问：'):
            return ln[5:].strip()[:120]
    return ''


def append_entry(path, entry: str, keep: int = ENTRY_KEEP) -> int:
    """追加一条自产条目并保留最近 keep 条 ✓。返回文件里实际保留的自产条数 ✓。

    别家生产者（workflow_learn）的块原样保留 ✗ 不动 ✓（同一个文件名，不能互相删 ✗）
    """
    entry = str(entry or '').strip()
    if not entry:
        return 0
    blocks = _load_blocks(path)
    foreign = [b for b in blocks if not b.startswith('## 问：')]
    # ★ P-23: 按【问句】去重 ✓（同一问题只留最新一条 ✗ 不堆积 ✗ 实测 60% 重复 ✗）
    _q = _question_of(entry)
    mine = [b for b in blocks
            if b.startswith('## 问：') and b != entry and _question_of(b) != _q]
    mine.append(entry)
    mine = mine[-max(1, int(keep)):]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('\n\n'.join(foreign + mine) + '\n', encoding='utf-8')
    return len(mine)


def clean_one(traj: dict, rules: dict) -> tuple:
    """按规则清洗一条轨迹。返回 (保留?, 清洗后的轨迹, 丢弃原因)。"""
    if rules.get('drop_incomplete', True):
        r = traj.get('reason')
        if r != 'completed':
            # 注意: 接手成功的会话，reason 由上层填 completed ✓ 这里只拦真正没结果的
            if not traj.get('final'):
                return False, None, f'未完成（reason={r}）'
    task = str(traj.get('task') or '').strip()
    # ★ P-2: 阈值 25 → 8（兜底 ✓）；判"碎片"的【主判据是内容】✓ 见 is_fragment ✓
    minlen = int(rules.get('drop_short_task', 8) or 8)
    frag, why = is_fragment(task, minlen)
    if frag:
        return False, None, why
    # ★ 2026-10-12 实测修正: 【长探索不在这里丢】✗
    #   现象: 50 条复杂任务里 31 条因"步数>12"被丢 → 越复杂越洗不出东西 ✗ 与目的冲突 ✓
    #   正确分工: 清洗只去真垃圾 ✓；"能不能照抄"由提炼阶段决定 ✓
    #     （reflect_distill 内部已有 ≤12 步的序列过滤 ✓ 长探索照样进统计表 ✓）
    #   这里只记一个标记，交给提炼去分辨 ✓
    steps = traj.get('tools_used') or []
    long_explore = len(steps) > int(rules.get('drop_long_steps', 12) or 12)
    if not traj.get('final'):
        return False, None, '没有产出内容'
    out = dict(traj)
    out['task'] = _scrub(task)
    # P-9: 先剥掉 PLAN 行，正文才干净 ✓（planned_tools 存进轨迹字段 ✓）
    tools, body = parse_planned_tools(_scrub(traj.get('final')))
    prev = traj.get('planned_tools')
    if not tools and isinstance(prev, list):
        tools = [str(x).strip() for x in prev if str(x).strip()]
    out['planned_tools'] = tools
    out['final'] = body
    # steps 里的参数与结果也脱敏 ✓
    # ★ 2026-10-12 实测修正: 不能 json.dumps → 替换 → json.loads ✗
    #   路径里的引号/反斜杠会破坏转义 → JSONDecodeError ✗ 整个清洗挂掉 ✓
    #   改为【在原对象上递归替换字符串】✓ 结构不动 ✓
    def _scrub_any(v):
        if isinstance(v, str):
            return _scrub(v)
        if isinstance(v, dict):
            return {k: _scrub_any(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_scrub_any(x) for x in v]
        return v

    new_steps = []
    for s in (traj.get('steps') or []):
        s2 = dict(s)
        for c in (s2.get('tool_calls') or []):
            c['input'] = _scrub_any(c.get('input'))
            if c.get('result') is not None:
                c['result'] = _scrub(c['result'])
        new_steps.append(s2)
    out['steps'] = new_steps
    out['_long_explore'] = long_explore   # 供提炼阶段分辨 ✓ 清洗不丢 ✓
    return True, out, None


def sink_session(traj: dict, cfg: dict) -> dict:
    """把一条会话轨迹走完 清洗 → 自产条目 → 落盘 → 入库。返回统计（供前端/日志显示 ✓）。"""
    # ★ T-1（2026-10-21）: 新路径 —— 轨迹 → 抽象做法（AWM 风格 ✓）
    #   旧路径（build_entry 出"问/答"原文 ✗）保留兼容 ✓
    if cfg.get('mode') in ('skills', 'skill') or cfg.get('extract_skills'):
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from skills_extractor import extract_skill, is_success_trajectory, skill_to_markdown
            ok, why = is_success_trajectory(traj)
            if not ok:
                return {'ok': False, 'dropped': True, 'reason': '入口闸门: ' + why}
            sk = extract_skill(traj, use_llm=bool(cfg.get('use_llm', True)))
            if sk is None:
                return {'ok': False, 'dropped': True, 'reason': 'extract_skill 返回 None'}
            if '_error' in sk:
                return {'ok': False, 'dropped': True, 'reason': '做法不合格: ' + str(sk['_error'])}
            # 写入 skills.jsonl ✓
            import json as _json
            sp = Path(cfg.get('skills_file') or 'data/learning/skills/skills.jsonl')
            if not sp.is_absolute():
                sp = Path(__file__).resolve().parent.parent / sp
            sp.parent.mkdir(parents=True, exist_ok=True)
            with sp.open('a', encoding='utf-8') as _f:
                _f.write(_json.dumps(sk, ensure_ascii=False) + '\n')
            return {'ok': True, 'mode': 'skills', 'skill': sk,
                    'markdown': skill_to_markdown(sk)}
        except Exception as e:
            return {'ok': False, 'dropped': True, 'reason': 'skills 模式异常: %s' % e}

    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    TRAJ_DIR.mkdir(parents=True, exist_ok=True)
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    rules = (cfg or {}).get('clean') or {}
    kb_name = (cfg or {}).get('kb_name') or 'learned'
    # ★ 多库挂载（2026-10-21）: kb_names 优先 ✓ 空则退回单库 ✓
    _kb_names = [str(x).strip() for x in ((cfg or {}).get('kb_names') or []) if str(x).strip()]
    if not _kb_names:
        _kb_names = [kb_name]
    mode = (cfg or {}).get('kb_mode') or 'new'
    stats = {'kept': 0, 'dropped': [], 'entry_chars': 0, 'distilled_chars': 0,
             'entries_kept': 0, 'planned_tools': [], 'md_path': None,
             'kb': None, 'kb_mode': None, 'ingested': None}

    raw = TRAJ_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{(traj.get('session_id') or 'na')[:12]}.jsonl"
    raw.write_text(json.dumps(traj, ensure_ascii=False) + "\n", encoding="utf-8")

    keep, cleaned, why = clean_one(traj, rules)
    if not keep:
        stats['dropped'].append(why)
        return stats                      # 不提炼、不入库 ✓（并如实说明为什么丢 ✓）
    stats['kept'] = 1
    stats['planned_tools'] = list(cleaned.get('planned_tools') or [])
    cf = CLEAN_DIR / raw.name
    cf.write_text(json.dumps(cleaned, ensure_ascii=False) + "\n", encoding="utf-8")

    # ---- 提炼（P-1 修复 ✓）: 自己产条目 ✗ 不再依赖 reflect_distill 的多步轨迹形状 ✓
    entry = build_entry(cleaned)
    stats['entry_chars'] = len(entry)
    stats['distilled_chars'] = len(entry)   # 兼容旧日志字段名（前端/日志仍在读 ✓）

    # ★ P-23: 入库前质量闸门（免责模板 + 无工具计划 ⇒ 只落盘不进库 ✓）
    try:
        _ok, _why = quality_gate(entry, stats.get('planned_tools'))
    except Exception as _qg:
        _ok, _why = True, f'quality_gate 异常忽略: {_qg}'
    stats['quality_gate'] = _why
    if not _ok:
        md_path = LEARN_DIR / f"learned_{kb_name}.md"
        try:
            stats['entries_kept'] = append_entry(md_path, entry)
            stats['md_path'] = str(md_path)
        except Exception:
            pass
        stats['skipped_ingest'] = True
        stats['dropped'].append('质量闸门: ' + _why)
        return stats

    # 空条目不入库（否则知识库只多一份没用的文档 ✗）
    if not entry.strip() or is_empty_distill(entry):
        stats['dropped'].append('自产条目为空（轨迹里没有可提炼的结论）→ 已跳过入库')
        stats['skipped_ingest'] = True
        return stats

    # ---- 落盘: 追加到 learned_<kb>.md，只保留最近 N 条 ✓
    md_path = LEARN_DIR / f"learned_{kb_name}.md"
    stats['entries_kept'] = append_entry(md_path, entry)
    stats['md_path'] = str(md_path)

    # ---- 入库（新建 or 已有 ✓ 幂等同名替换 ✓）
    sys.path.insert(0, str(ROOT))
    import kb as kbmod                                # noqa
    # ★ 多库挂载（2026-10-21）: **循环入库到多个库** ✓
    _ingested = {}
    _errs = {}
    for _nm in _kb_names:
        try:
            if mode == 'new':
                try:
                    try:
                        kbmod.create_kb(_nm,
                                        description='学习增强：从真实调用轨迹提炼出的经验（自动生成 ✓）',
                                        tier='heavy')
                    except TypeError:
                        kbmod.create_kb(_nm,
                                        description='学习增强：从真实调用轨迹提炼出的经验（自动生成 ✓）')
                except ValueError:
                    pass                              # 已存在 → 直接追加 ✓
            _info = kbmod.add_documents_to_kb(_nm, [str(md_path)])
            _ingested[_nm] = _info
        except Exception as e:
            _errs[_nm] = f'{type(e).__name__}: {e}'
    stats['kb'] = _kb_names[0] if len(_kb_names) == 1 else _kb_names
    stats['kb_mode'] = mode
    stats['kb_names'] = _kb_names
    stats['ingested'] = _ingested if len(_kb_names) > 1 else (
        _ingested.get(_kb_names[0]) if _ingested else {'error': str(_errs)})
    if _errs:
        stats['ingest_errors'] = _errs
    return stats
