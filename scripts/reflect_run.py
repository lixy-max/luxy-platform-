#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reflect_run.py —— 反射学习闭环的「升级器 + 轨迹采集器」。

它做的事（对应 v-reflect 的第 2 步）：
    给一个任务 → 调 `dsh --profile headless --json` → 让 DSH 用它手里的工具
    （本项目已把工作台能力做成 DSH 插件，53 个工具）真实操作工作台
    → 把事件流解析成一条结构化轨迹 → 追加进 trajectories.jsonl

为什么要这么做（而不是在工作台里直接调云端 API）：
    · DSH 的 headless 是**非交互入口**（`--json` 输出 NDJSON 事件流）✓
    · 它跑在 Windows（node），工作台跑在 WSL → 用 WSL 的 Windows 互操作直调 node.exe ✓
    · 事件流里每条工具调用都带【精确参数 + 结果 + 每步 token】→ 天然的轨迹数据 ✓

实测踩过的三个坑（都已在代码里处理）：
    1) 事件流【不是严格 NDJSON】✗ —— tool_result 里含裸换行，一个 JSON 对象会跨多行
       → 必须「累积到 json.loads 成功」再切，不能按行切
    2) stdout 混着 stderr 诊断（含插件激活警告）✗ → 解析时要能跳过非 JSON 文本
    3) 编码: PowerShell 重定向默认 UTF-16 ✗ → 解码时 utf-8 / utf-16 都要试

用法：
    python scripts/reflect_run.py --task "用 paper_lora_kb_list 列出知识库"
    python scripts/reflect_run.py --task-file tasks.txt --limit 3
    python scripts/reflect_run.py --list
环境变量（路径因机而异，都可覆盖）：
    DSH_NODE    默认 /mnt/c/Program Files/nodejs/node.exe
    DSH_BIN     默认 npx 缓存里的 @deepseek-ai/dsh/lib/bin.js
    DSH_PROFILE 默认 headless
    DSH_CWD     子进程工作目录（默认项目根；headless 会在里面读文件，选个正经目录 ✓）
    TRAJ_DIR    轨迹输出目录（默认 <项目>/data/trajectories）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# node.exe 用 WSL 的 /mnt/c 形式可以【直接执行】✓
# ★ 修 BUG（2026-10-21）: WSL 不能执行 Windows 的 node.exe ✗
#   OSError [Errno 8] Exec format error ✗ ⇒ /reflect/run 从来跑不通 ✗
#   改用 **WSL 侧 Linux node** ✓（bin.js 在 Windows 侧但 WSL node 能读 ✓ 已实测 ✓）
def _default_node() -> str:
    import shutil as _sh
    # ① 环境变量优先 ✓
    env = __import__('os').environ.get("DSH_NODE")
    if env:
        return env
    # ② 本地安装 ✓
    cand = __import__('pathlib').Path(os.environ.get("NODE_BIN") or (__import__('pathlib').Path.home() / ".local/bin/node"))
    if cand.exists():
        return str(cand)
    cand2 = __import__('pathlib').Path(os.environ.get("NODE_BIN") or (__import__('pathlib').Path.home() / "node-v20.18.0-linux-x64/bin/node"))
    if cand2.exists():
        return str(cand2)
    # ③ PATH 里的 ✓
    w = _sh.which("node")
    if w:
        return w
    # ④ 兜底 Windows（只在从 Windows 侧调用时有用 ⚠）
    return "/mnt/c/Program Files/nodejs/node.exe"


DEFAULT_NODE = _default_node()
# 脚本本身用 WSL 形式保存（好做存在性检查 ✓），传参给 node.exe 时再 wslpath -w 转 Windows 形式 ✓
# （实测：直接传 /mnt/c/... 作为【参数】会得到 "Cannot find module 'E:\mnt\c\...'" ✗）
DEFAULT_BIN = ("/mnt/c/Users/A/AppData/Local/npm-cache/_npx/"
               "1e7f6d9597241db0/node_modules/@deepseek-ai/dsh/lib/bin.js")
DEFAULT_TRAJ_DIR = ROOT / "data" / "trajectories"
# Windows 侧的真实目录（node.exe 是 Windows 进程，cwd 用它最稳 ✓）
# ⚠️ 必须用 WSL 形式 ✗ 不能写 "E:\\WSL"：
#   · WSL 里的 Python 执行 subprocess(cwd="E:\\WSL") 会直接 FileNotFoundError ✗
#   · 而 /mnt/e/WSL 会被 WSL 互操作翻译成 E:\WSL ✓ —— 对 node.exe 而言是真实 Windows 目录 ✓
#   （也不要写 WSL 项目路径 ✗：那会被翻成 \\wsl.localhost\… ，pwsh 做 ACL 检查会失败 6 次 ✗）
DEFAULT_CWD = os.environ.get("REFLECT_CWD", "/mnt/e/WSL")


def decode_any(raw: bytes) -> str:
    """按可能性依次试编码（PowerShell 重定向常给 UTF-16）。"""
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            t = raw.decode(enc)
        except Exception:
            continue
        if enc == "utf-16" and "\x00" in t[:200]:
            continue
        return t
    return raw.decode("utf-8", errors="replace")


def parse_events(text: str):
    """把「事件流 + 诊断文本」混在一起的输出切成事件对象。

    返回 (events, diagnostics)。跨行对象靠累积重试处理（坑 1）。
    """
    dec = json.JSONDecoder()
    events, diags, buf, fails = [], [], "", 0
    for line in text.splitlines():
        buf = (buf + "\n" + line) if buf else line
        s = buf.strip()
        if not s:
            buf = ""
            continue
        if not s.startswith("{"):
            diags.append(s[:300])
            buf, fails = "", 0
            continue
        try:
            obj, _ = dec.raw_decode(s)
        except Exception:
            fails += 1
            if fails > 60:                      # 累积太久 → 判定不是 JSON
                diags.append(s[:300])
                buf, fails = "", 0
            continue
        events.append(obj)
        buf, fails = "", 0
    return events, diags


def build_trajectory(task: str, events: list, diags: list, meta: dict) -> dict:
    """把事件流压成一条轨迹：每步的 thinking/text/tool_call(含结果)/usage。"""
    sess = next((e for e in events if e.get("type") == "session"), {})
    open_calls, steps, cur = {}, [], None
    usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0}
    for e in events:
        t = e.get("type")
        if t == "status" and e.get("phase") == "step_start":
            cur = {"turn": e.get("turn"), "step": e.get("step"), "thinking": "", "text": "",
                   "tool_calls": [], "usage": None}
            steps.append(cur)
        elif t == "thinking" and cur is not None:
            cur["thinking"] += str(e.get("text") or "")
        elif t == "text" and cur is not None:
            cur["text"] += str(e.get("text") or "")
        elif t == "tool_call":
            c = {"callId": e.get("callId"), "tool": e.get("tool"), "input": e.get("input"),
                 "status": None, "result": None}
            if cur is not None:
                cur["tool_calls"].append(c)
            open_calls[c["callId"]] = c
        elif t == "tool_result":
            c = open_calls.get(e.get("callId"))
            if c is not None:
                c["status"] = e.get("status")
                c["result"] = e.get("result")
        elif t == "status" and e.get("phase") == "step_end":
            u = e.get("usage") or {}
            if cur is not None:
                cur["usage"] = u
            for k in usage:
                usage[k] += int(u.get(k) or 0)
    end = next((e for e in reversed(events)
                if e.get("type") == "status" and e.get("phase") == "turn_end"), {})
    final = next((e for e in reversed(events) if e.get("type") == "final"), {})
    # ★ P-13（2026-10-15）: 超时/中断时【打捞答案】
    #   实测: 64 例 rc=-9 里 32 例 final 为空 ⇒ 答案被丢 ✗
    #   原因: --json 是 NDJSON 流 ✓，超时前可能还没发出 final 事件 ✗
    #        而中间的 text 事件里其实已经有内容 ✓ ⇒ 捞回来 ✓（只加不删 ✓ 有 final 时行为不变 ✓）
    final_text = str(final.get("text") or "")
    salvaged = False
    if not final_text:
        cand = [str(e.get("text") or "") for e in events
                if e.get("type") == "text" and e.get("text")]
        if not cand:
            cand = [str(st.get("text") or "") for st in steps if st.get("text")]
        text = (cand[-1] if cand else "").strip()
        if not text and steps:
            text = "".join(str(st.get("text") or "") for st in steps[-1:]).strip()
        if text:
            final_text = text
            salvaged = True
            diags.append(f"final 事件缺失（超时/中断）⇒ 已从中间事件打捞 {len(text)} 字符 ✓")
    err = ((end.get("reason") or {}).get("error") or {})
    tools_used = [c["tool"] for s in steps for c in s["tool_calls"]]
    tool_errors = [c["tool"] for s in steps for c in s["tool_calls"]
                   if c["status"] and c["status"] != "completed"]
    return {
        "task": task,
        "session_id": sess.get("sessionId"),
        "reason": (end.get("reason") or {}).get("kind"),      # completed / error / …
        "error": {"message": err.get("message"), "code": err.get("code")} if err else None,
        "final": final_text,
        "final_salvaged": salvaged,     # ★ P-13: 这条 final 是"打捞"来的（非正常结束 ✓）
        "steps": steps,
        "usage": usage,
        "tools_used": tools_used,
        "tool_errors": tool_errors,
        "diagnostics": diags[:20],
        **meta,
    }


def run_one(task: str, args) -> dict:
    node = os.environ.get("DSH_NODE", DEFAULT_NODE)
    binjs = os.environ.get("DSH_BIN", DEFAULT_BIN)
    profile = os.environ.get("DSH_PROFILE", "headless")
    # ⚠️ 默认 cwd 用【Windows 真实目录】✗ 不能用 WSL 项目路径 ✗
    # 实测：cwd 是 \\wsl.localhost\... 时，pwsh 工具做 ACL 检查会失败 6 次
    # （GetNamedSecurityInfoW failed）—— 每次升级白烧调用。
    # 工作台工具走 HTTP，不依赖 cwd ✓；需要项目文件时用 UNC 或 API ✓
    cwd = os.environ.get("DSH_CWD", DEFAULT_CWD)
    if not Path(node).exists():
        raise SystemExit(f"找不到 node: {node}（可用 DSH_NODE 覆盖）")
    if not Path(binjs).exists():
        raise SystemExit(f"找不到 dsh bin: {binjs}（可用 DSH_BIN 覆盖）")
    # 参数里的路径要转成 Windows 形式（node.exe 是 Windows 进程 ✗ 不认 /mnt/c/…）
    def to_win(p: str) -> str:
        if p.startswith("/mnt/") or p.startswith("/home/") or p.startswith("/"):
            try:
                r = subprocess.run(["wslpath", "-w", p], capture_output=True, text=True, timeout=10)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
            except Exception:
                pass
        return p
    # ★ 环境铁律（实测: pwsh 访问 \\wsl.localhost\… 被 ACL 拒绝 → 反复重试 → 超时 → 空产出 ✓
    #   61 次报错 / 20 条空产出 都源于此）——它不是"改任务"✓ 是给它环境约束 ✓
    ENV_RULE = (
        "【环境说明，务必遵守】本机文件分两侧：WSL 侧（/home/<user>/...、\\wsl.localhost\\...）"
        "与 Windows 侧（E:\\WSL、C:\\...）。规则："
        "① 要读 WSL 里的文件，只能用 read / glob / grep —— 绝不要用 pwsh 去访问 WSL 路径，"
        "那会因 ACL 被拒（Access is denied）并让你反复重试直到超时；"
        "② pwsh 只在需要跑命令或用 Windows 侧工具时使用，且它的 cwd 是 E:\\WSL；"
        "③ 工作台的能力优先用 paper_lora_* 工具，别去翻项目源码找答案；"
        "④ 同一个工具连续失败两次就换思路或直接说明做不到，不要反复撞。\n\n"
    )
    cmd = [node, to_win(binjs), "--profile", profile, "--json", ENV_RULE + task]
    t0 = time.time()
    err_text = ""
    try:
        # 用【进程组】跑: node.exe 是 Windows 进程，Python 超时只杀自己 ✗
        # 实测留下过孤儿 node.exe 继续跑（撞满超时后仍占着）✓ 故用 killpg 兜底 ✓
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=args.timeout,
                           start_new_session=True)
        raw, rc = p.stdout, p.returncode
        err_text = decode_any(p.stderr or b"")
    except subprocess.TimeoutExpired as e:
        raw, rc = (e.stdout or b""), -9
        err_text = decode_any(e.stderr or b"") + "\n[reflect] 超时"
        # 进程组内全部杀掉，避免留下孤儿 node.exe（实测踩过 ✓）
        try:
            import os as _os, signal as _sig
            _os.killpg(_os.getpgid(p.pid), _sig.SIGKILL)
        except Exception:
            pass
    except FileNotFoundError as e:
        raise SystemExit(f"无法执行 node: {node}（{e}）")
    ms = int((time.time() - t0) * 1000)
    text = decode_any(raw)
    events, diags = parse_events(text)
    # stderr 里的诊断（含插件激活警告 / 崩溃栈）也要留档 ✗ 否则失败时一片空白
    for ln in [x for x in err_text.splitlines() if x.strip()][:40]:
        diags.append("stderr: " + ln[:300])
    if rc not in (0, None) and not events:
        diags.append(f"stderr/rc: 子进程 rc={rc}，stdout {len(raw)} 字节、stderr {len(err_text)} 字节")
    traj = build_trajectory(task, events, diags, {
        "provider": "dsh-headless", "profile": profile,
        "rc": rc, "ms": ms, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    if args.keep_raw:
        raw_dir = Path(os.environ.get("TRAJ_DIR", DEFAULT_TRAJ_DIR)) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{traj['session_id'] or traj['ts']}.txt").write_bytes(raw)
    return traj


def append_traj(traj: dict) -> Path:
    d = Path(os.environ.get("TRAJ_DIR", DEFAULT_TRAJ_DIR))
    d.mkdir(parents=True, exist_ok=True)
    f = d / "trajectories.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(traj, ensure_ascii=False) + "\n")
    return f


def summarize(traj: dict) -> str:
    lines = [f"  任务: {traj['task'][:90]}",
             (f"  ⚠ final 为打捞结果（{len(str(traj.get('final') or ''))} 字符；"
              f"原因为超时/中断 ✓）" if traj.get("final_salvaged") else None),
             f"  结束: {traj['reason']}   耗时: {traj['ms']}ms   token: {traj['usage']}",
             f"  工具: {traj['tools_used'] or '（未用工具）'}"]
    if traj.get("tool_errors"):
        lines.append(f"  ⚠ 工具报错: {traj['tool_errors']}")
    if traj.get("error"):
        lines.append(f"  ✗ 错误: {traj['error']}")
    lines = [x for x in lines if x]
    for s in traj["steps"]:
        for c in s["tool_calls"]:
            res = str(c["result"] or "")[:150].replace("\n", " ")
            lines.append(f"    → {c['tool']}({json.dumps(c['input'], ensure_ascii=False)[:80]})"
                         f"\n      ← [{c['status']}] {res}")
    lines.append(f"  final: {str(traj['final'])[:200]}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="反射学习：让 DSH 接手任务并采集轨迹")
    ap.add_argument("--task", help="任务文本")
    ap.add_argument("--task-file", help="一行一个任务的文件")
    ap.add_argument("--limit", type=int, default=0, help="最多跑几条（0=不限）")
    ap.add_argument("--timeout", type=int, default=600, help="单条超时秒数")
    ap.add_argument("--no-raw", dest="keep_raw", action="store_false", help="不保存原始事件流")
    ap.add_argument("--list", action="store_true", help="列出已有轨迹")
    ap.set_defaults(keep_raw=True)
    args = ap.parse_args()

    traj_file = Path(os.environ.get("TRAJ_DIR", DEFAULT_TRAJ_DIR)) / "trajectories.jsonl"

    if args.list:
        if not traj_file.exists():
            print("（还没有轨迹）")
            return 0
        n = 0
        for ln in traj_file.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            t = json.loads(ln)
            n += 1
            print(f"  [{n}] {t.get('ts')}  {t.get('reason'):<10} "
                  f"工具{len(t.get('tools_used') or [])}个  {str(t.get('task'))[:60]}")
        print(f"  共 {n} 条 → {traj_file}")
        return 0

    tasks = []
    if args.task:
        tasks.append(args.task)
    if args.task_file:
        # ⚠️ 实测踩过: 任务文件里一条任务占【多行】（比如前缀带空行 ✗）会被拆成多条碎片 ✗
        #    碎片任务往往无法完成 → 每条都撞满超时 → 白烧时间与 token ✓
        #    这里显式告警，不静默跑 ✓（要跑多行任务请自己在生成端拼成一行 ✓）
        raw_lines = [x.strip() for x in Path(args.task_file).read_text(encoding="utf-8").splitlines() if x.strip()]
        tasks += raw_lines
        short = [x for x in raw_lines if len(x) < 25]
        if short:
            print(f"  ⚠ 警告: 任务文件里有 {len(short)} 条短行（<25 字符），"
                  f"很可能是被拆碎的片段 ✗ 例如: {short[0][:40]!r}", file=sys.stderr)
    if not tasks:
        ap.error("需要 --task 或 --task-file（或 --list）")
    if args.limit:
        tasks = tasks[:args.limit]

    for i, task in enumerate(tasks, 1):
        print(f"=== [{i}/{len(tasks)}] ===")
        traj = run_one(task, args)
        print(summarize(traj))
        f = append_traj(traj)
        print(f"  ✓ 已追加轨迹 → {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
