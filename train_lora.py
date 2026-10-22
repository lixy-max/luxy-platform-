#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调用 LLaMA-Factory 训练 LoRA"""
import os
import sys
import json
import shutil
import subprocess
import fcntl
import statistics
import time
from pathlib import Path
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
# ★ 修（2026-10-21）: 原先 line 531/542 用了未定义的 ROOT ✗
#   ⇒ CLI 用 --lora_name 训练一直报 "name 'ROOT' is not defined" ✗
#   （只有后端 API 传 output_dir 的路径能跑 ✓ 所以一直没暴露 ✗）
ROOT = Path(PROJECT_DIR)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================================
# ★ P-4 / P-7: 训练【数据前置闸门】
#   实测教训（2026-10-15）: drill_v1 的数据集每条样本 system 就 5143 token ✗，
#   而 cutoff_len=2048 ⇒ 每条只喂进 ~40%，任务与答案都可能被切掉 ⇒ 训练毫无意义 ✗
#   两次训练都失败（CUDA），但【即使成功也是白训】✓ —— 所以闸门必须放在最前面 ✓
#   依据: docs/LORA_DATA_AND_EVAL_GUIDE.md 第二部分（数据形态的"规定"✓）
# ============================================================================
class DataPreflightError(RuntimeError):
    """数据体检不合格 ⇒ 拒绝开训（并说明原因与改法 ✓）"""


def _est_tokens(text: str) -> int:
    """粗估 token: CJK 按 1 / 其它按 4 字符 1 token（与后端 _est_tokens 同口径 ✓）"""
    t = str(text or "")
    cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
    return cjk + max(0, (len(t) - cjk)) // 4


def data_preflight(dataset_file, cutoff_len=2048, output_dir=None, log_fn=None):
    """体检训练集: 超长截断比例 / system 占比 / 样本量。不合格直接拒绝 ✓

    返回 report dict（并写 <output_dir>/data_report.json ✓ 供前端/追踪查看 ✓）
    """
    say = log_fn or (lambda m: None)
    rows = []
    with open(dataset_file, "r", encoding="utf-8") as f:
        for line in f:
            # ★ 修（2026-10-22）：训练体检只认 conversations/from ⇒
            #   messages 格式判 F1-F8 全不过（valid_ratio=0）✗ 先归一化 ✓
            try:
                _o = json.loads(line)
            except Exception:
                _o = None
            if isinstance(_o, dict) and "conversations" not in _o \
                    and isinstance(_o.get("messages"), list):
                _RM = {"user": "human", "assistant": "gpt", "system": "system"}
                _o = dict(_o)
                _o["conversations"] = [
                    {"from": _RM.get(str(_x.get("role") or "user"), "human"),
                     "value": str(_x.get("content") or "")}
                    for _x in _o["messages"]]
                line = _o
            # ★ 修（2026-10-22）：必须 append —— 上次改动把原来的
            #   rows.append(json.loads(line)) 一起删了 ⇒ rows 空 ⇒ 全判不过 ✗
            if isinstance(_o, dict):
                rows.append(_o)
    convs = []
    for r in rows:
        c = r.get("conversations") or []
        total = sum(_est_tokens(x.get("value")) for x in c)
        # ★ P-73/P4（2026-10-19）: system 提取对齐标准格式 ✓
        #   标准格式: system 是【顶层独立字段】✗ 不在 conversations 里 ✓
        #   旧格式:   conversations[0].from == "system"（兼容 ✓）
        #   原来的写法把 c[0] 当 system ✗ ⇒ 标准格式下 c[0] 是 human ⇒ 统计错 ✗
        _sys_top = r.get("system")
        if isinstance(_sys_top, str) and _sys_top.strip():
            system = _est_tokens(_sys_top)
        elif c and isinstance(c[0], dict) and str(c[0].get("from", "")).lower() == "system":
            system = _est_tokens(c[0].get("value"))   # 兼容旧格式 ✓
        else:
            system = 0
        # ★ P-70（2026-10-19）: 计入 tools 字段 ✗ 原来完全没算 ✗
        #   tools 是 JSON 字符串 ✓ 实测 91 工具 ≈ 8k token ✗ 会远超 cutoff ✗
        _tools = r.get("tools")
        tools_tok = _est_tokens(_tools) if isinstance(_tools, str) and _tools else 0
        total += tools_tok
        convs.append({"total": total, "system": system, "n": len(c), "tools": tools_tok})
    if not convs:
        raise DataPreflightError("训练数据集为空（0 条样本）")

    totals = sorted(x["total"] for x in convs)
    over = [x for x in convs if x["total"] > cutoff_len]
    sys_sum = sum(x["system"] for x in convs)
    tot_sum = sum(x["total"] for x in convs) or 1
    report = {
        "samples": len(convs),
        "cutoff_len": cutoff_len,
        "tokens_avg": int(sum(totals) / len(totals)),
        "tokens_p95": totals[min(len(totals) - 1, int(len(totals) * 0.95))],
        "tokens_max": totals[-1],
        "over_cutoff_count": len(over),
        "over_cutoff_ratio": round(len(over) / len(convs), 4),
        "system_token_ratio": round(sys_sum / tot_sum, 4),
        # ★ P-70: tools 字段统计（P-70 ✓）
        "tools_tokens_avg": int(sum(x.get("tools", 0) for x in convs) / len(convs)),
        "tools_tokens_max": max((x.get("tools", 0) for x in convs), default=0),
        "samples_with_tools": sum(1 for x in convs if x.get("tools", 0) > 0),
    }
    # ★ P-73/P4（2026-10-19）: 加格式校验（dataset_toolkit 唯一真相 ✓）
    try:
        import dataset_toolkit as _dz
        _wl = _dz.load_tool_whitelist()
        _fmt_ok = 0
        _fmt_probs = {}
        for _r in rows:
            _ok, _ps = _dz.validate(_r, _wl)
            if _ok:
                _fmt_ok += 1
            else:
                for _pr in _ps:
                    _k = _pr.split(":")[0]
                    _fmt_probs[_k] = _fmt_probs.get(_k, 0) + 1
        report["format_valid"] = _fmt_ok
        report["format_valid_ratio"] = round(_fmt_ok / len(rows), 4) if rows else 0
        report["format_problems"] = _fmt_probs
    except Exception as _e:
        report["format_valid"] = -1
        report["format_problems"] = {"dataset_toolkit 不可用": str(_e)[:80]}

    report["verdict"] = "pass"
    report["problems"] = []

    allow_trunc = os.environ.get("TRAIN_ALLOW_TRUNCATION") == "1"
    allow_heavy = os.environ.get("TRAIN_ALLOW_SYSTEM_HEAVY") == "1"

    if report["samples"] < 8:
        report["problems"].append(f"样本太少（{report['samples']} < 8）")
    # ★ P-73/P4: 格式不合格率 > 10% ⇒ 拦 ✗（F1-F8 ✓）
    _fvr = report.get("format_valid_ratio", 1.0)
    if report.get("format_valid", 0) >= 0 and _fvr < 0.90:
        report["problems"].append(
            f"格式不合格: 只有 {_fvr*100:.0f}% 样本通过 F1-F8 ✗ "
            f"（问题: {report.get('format_problems')}）⇒ 训练会学错 ✗ "
            f"改法: 用「上传数据集 & 一键清洗」修复格式 ✓")
    if report["over_cutoff_ratio"] > 0.10 and not allow_trunc:
        report["problems"].append(
            f"{report['over_cutoff_count']}/{report['samples']} 条样本超过 cutoff_len={cutoff_len}"
            f"（{report['over_cutoff_ratio']*100:.0f}% > 10%）⇒ 会被截断 ✗ "
            f"改法: 瘦身 system 提示（见 docs/PLATFORM_ISSUES.md P-6）或提高 cutoff_len ✓"
            f"（确要忽略请设 TRAIN_ALLOW_TRUNCATION=1）")
    # ★ system 规则（2026-10-15 修正 ✗ 第一版只看占比 ⇒ 把"短样本 + 短 system"也误杀 ✗）
    #   真正的问题是【system 绝对太长】（实测 5143 token，把 cutoff 吃光 ✗），不是占比本身 ✓
    #   所以: 只当 system 绝对长度超标（>600 token）时才拦；占比只在 system 不小的时候作为辅助信号 ✓
    report["system_tokens_avg"] = int(sys_sum / len(convs))
    # ★ P-70（2026-10-19）: tools 字段过大也拦 ✗（原来完全没管 ✗）
    _tools_avg = report.get("tools_tokens_avg", 0)
    _tools_cap = max(256, int(cutoff_len * 0.4))
    if _tools_avg > _tools_cap and not allow_heavy:
        report["problems"].append(
            f"tools 字段平均 {_tools_avg} token（> {_tools_cap} = cutoff 的 40%）✗ "
            f"会把 cutoff 吃光 ⇒ 训练样本里请只放【本任务用到的工具】✗ 不要塞全部工具清单 ✗ "
            f"（{report.get('samples_with_tools', 0)}/{report.get('samples', 0)} 条样本带 tools ✓）"
            f"（确要忽略请设 TRAIN_ALLOW_SYSTEM_HEAVY=1）")

    system_too_long = report["system_tokens_avg"] > 600
    if system_too_long and not allow_heavy:
        report["problems"].append(
            f"system 平均 {report['system_tokens_avg']} token（> 600）✗ 会把 cutoff 吃光 "
            f"⇒ system 提示里多半有可删的冗余（例如把工具清单又写了一遍 ✗）；"
            f"占整条样本 {report['system_token_ratio']*100:.1f}%（仅作参考 ✓）"
            f"（确要忽略请设 TRAIN_ALLOW_SYSTEM_HEAVY=1）")

    if report["problems"]:
        report["verdict"] = "reject"

    _sys_avg = int(sys_sum / len(convs))
    say(f"数据体检: 样本 {report['samples']} · token 平均 {report['tokens_avg']} / p95 "
        f"{report['tokens_p95']} / 最大 {report['tokens_max']} · 超 cutoff "
        f"{report['over_cutoff_count']} 条（{report['over_cutoff_ratio']*100:.0f}%）· "
        f"system 平均 {_sys_avg} token（占比 {report['system_token_ratio']*100:.1f}%）"
        f" ⇒ {report['verdict']}")

    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "data_report.json"), "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except Exception as e:
            say(f"（data_report.json 写入失败，不影响流程: {e}）")

    if report["verdict"] == "reject":
        raise DataPreflightError(
            "❌ 数据体检不合格，已拒绝开训（避免白烧时间 ✗）:\n  - "
            + "\n  - ".join(report["problems"])
            + "\n  详情见 docs/LORA_DATA_AND_EVAL_GUIDE.md 与 docs/PLATFORM_ISSUES.md")
    return report


# ============================================================================
# ★ P-3: 训练【单飞锁】—— 防并发训练把 CUDA 搞崩
#   实测: drill_v1 与两条 swk_persona_v3 时间重叠 ⇒ CUBLAS_STATUS_INTERNAL_ERROR ✗
#   放在 train_lora.py 里 ⇒ 绕过 API 直跑也拦得住 ✓
# ============================================================================
TRAIN_LOCK_PATH = os.environ.get("TRAIN_LOCK_PATH", "/tmp/paper_lora_train.lock")


def _lock_path():
    """每次读取（便于测试用 TRAIN_LOCK_PATH 指到临时文件 ✓ 不与真实训练撞车 ✓）"""
    return os.environ.get("TRAIN_LOCK_PATH", TRAIN_LOCK_PATH)


def _release_train_lock(fd):
    """释放训练锁（进程退出也会自动释放 ✓ 这里做显式释放便于测试 ✓）"""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except Exception:
        pass


def acquire_train_lock():
    """非阻塞抢锁；抢不到 ⇒ 直接拒绝（说明已有训练在跑 ✓）。返回 fd（需保持存活 ✓）。"""
    path = _lock_path()
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            holder = os.read(fd, 200).decode("utf-8", "replace").strip()
        except Exception:
            holder = ""
        os.close(fd)
        raise RuntimeError(
            "❌ 已有训练在跑（单飞锁被占用 ✗），拒绝并发启动。\n"
            f"  锁文件: {path}\n  占用者: {holder or '未知'}\n"
            "  并发训练会让 CUDA 上下文崩掉（实测 CUBLAS_STATUS_INTERNAL_ERROR ✗）——"
            "请等它结束，或先取消它 ✓")
    os.ftruncate(fd, 0)
    os.write(fd, f"pid={os.getpid()} at={datetime.now().isoformat()} ".encode())
    return fd


def recommend_rank(data_count):
    if data_count < 100:
        return 8
    elif data_count < 500:
        return 16
    elif data_count < 2000:
        return 32
    return 64


def recommend_epochs(data_count):
    if data_count < 100:
        return 5
    elif data_count < 500:
        return 3
    return 2


def detect_model_quantization(model_path):
    config_file = os.path.join(model_path, "config.json")
    if not os.path.exists(config_file):
        return None
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception:
        return None

    quant_config = config.get("quantization_config", {})
    quant_method = quant_config.get("quant_method", "").lower()

    if "awq" in quant_method:
        return "awq"
    if "gptq" in quant_method:
        return "gptq"
    if "fp8" in quant_method:
        return "fp8"
    return None


def detect_template(model_path):
    config_file = os.path.join(model_path, "config.json")
    if not os.path.exists(config_file):
        return "qwen"

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception:
        return "qwen"

    model_type = config.get("model_type", "").lower()

    if "qwen3" in model_type:
        return "qwen3_nothink"
    if "qwen" in model_type:
        return "qwen"
    if "llama" in model_type:
        return "llama3"
    return "qwen"


def compute_save_steps(data_count, epochs, grad_accum=8):
    total_steps = max(1, (data_count * epochs) // grad_accum)
    save_steps = max(1, total_steps // 2)
    return save_steps


def fix_adapter_config(output_dir, real_base_model):
    """
    修正 adapter_config.json 里的 base_model_name_or_path。
    LLaMA-Factory 会写入 model.config._name_or_path（可能是错的），
    我们强制改成用户实际选择的底座路径。
    """
    cfg_path = os.path.join(output_dir, "adapter_config.json")
    if not os.path.exists(cfg_path):
        return False
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        old = cfg.get("base_model_name_or_path", "")
        cfg["base_model_name_or_path"] = real_base_model
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        log(f"修正 base_model_name_or_path: {old} → {real_base_model}")
        return True
    except Exception as e:
        log(f"修正 adapter_config 失败: {e}")
        return False


def resolve_llamafactory_cli():
    """
    定位 llamafactory-cli。

    原先直接用裸命令名 "llamafactory-cli"，只在后端从"已激活 venv 的终端"启动时
    才能命中（PATH 里有 ~/.venv/bin）。一旦后端用绝对路径的 python 启动，
    子进程就找不到命令，训练在最后一步直接 FileNotFoundError。

    这里优先取与当前解释器同目录的 CLI（venv/bin/llamafactory-cli），
    完全不依赖 PATH；再退回 PATH 查找。
    """
    candidate = os.path.join(os.path.dirname(sys.executable), "llamafactory-cli")
    if os.path.exists(candidate):
        return candidate
    found = shutil.which("llamafactory-cli")
    if found:
        return found
    raise RuntimeError(
        "找不到 llamafactory-cli。\n"
        f"  已尝试: {candidate}\n"
        f"  解释器: {sys.executable}\n"
        "请确认 LLaMA-Factory 装在当前 venv 中（pip install llamafactory==0.9.5），"
        "或从已激活 venv 的终端启动后端。"
    )


# ══════════════════════════════════════════════════════════
# ★ Q-2（2026-10-21）: 训练进度解析 ✓
#   问题: 原来只 print 原始日志 ✗ 看不到结构化 loss/step/epoch ✗
#   修法: 解析 llamafactory 输出 ⇒ 结构化进度行 + 进度文件 ✓
# ══════════════════════════════════════════════════════════
import re as _re

_LOSS_RE = _re.compile(r"'loss':\s*([0-9.]+)")
_EPOCH_RE = _re.compile(r"'epoch':\s*([0-9.]+)")
_LR_RE = _re.compile(r"'learning_rate':\s*([0-9.eE+-]+)")
_TQDM_RE = _re.compile(r"(\d+)%\|[^|]*\|\s*(\d+)/(\d+)")
_STEP_RE = _re.compile(r"'global_step':\s*(\d+)")


def _parse_train_line(line: str) -> dict | None:
    """从一行输出解析进度 ✓ 解析不到 ⇒ None ✓"""
    out = {}
    m = _LOSS_RE.search(line)
    if m:
        try:
            out['loss'] = float(m.group(1))
        except ValueError:
            pass
    m = _EPOCH_RE.search(line)
    if m:
        try:
            out['epoch'] = float(m.group(1))
        except ValueError:
            pass
    m = _LR_RE.search(line)
    if m:
        out['lr'] = m.group(1)
    m = _STEP_RE.search(line)
    if m:
        try:
            out['step'] = int(m.group(1))
        except ValueError:
            pass
    m = _TQDM_RE.search(line)
    if m:
        try:
            out['percent'] = int(m.group(1))
            out['step'] = int(m.group(2))
            out['total_steps'] = int(m.group(3))
        except ValueError:
            pass
    return out or None


def train(base_model, dataset_file, output_dir, lora_name, data_count):
    # ★ 顺序很重要: 先把"便宜且该早报错"的校验做完，最后才抢锁 ✓
    #   （否则 AWQ 底座/数据不存在/体检不达标 都会伪装成"已有训练在跑"✗）
    # base_model 必须是绝对路径
    base_model = os.path.abspath(os.path.expanduser(base_model))
    log(f"训练底座（用户选择）: {base_model}")

    if not os.path.exists(base_model):
        raise RuntimeError(f"训练底座路径不存在: {base_model}")

    # ★ H5-v2: 允许显式覆盖 rank / epochs（做"容量墙 vs 数据不足"对照 ✓ GUIDE §1.2）
    #   不设置环境变量时，行为与以前完全一致 ✓
    rank = int(os.environ.get("TRAIN_RANK") or recommend_rank(data_count))
    alpha = rank * 2
    epochs = int(os.environ.get("TRAIN_EPOCHS") or recommend_epochs(data_count))
    lr = os.environ.get("TRAIN_LR") or "1e-4"
    log(f"超参来源: rank={'env TRAIN_RANK' if os.environ.get('TRAIN_RANK') else 'auto'} · "
        f"epochs={'env TRAIN_EPOCHS' if os.environ.get('TRAIN_EPOCHS') else 'auto'} · lr={lr}")
    save_steps = compute_save_steps(data_count, epochs)

    log(f"训练参数: rank={rank}, alpha={alpha}, epochs={epochs}, save_steps={save_steps}"
        f"（P1-A4 已补: save_strategy=epoch, val_size=0.1, cutoff_len=2048）")

    quant_method = detect_model_quantization(base_model)
    log(f"底座量化方式: {quant_method or 'FP16/BF16 (未量化)'}")

    if quant_method in ("awq", "gptq", "fp8"):
        raise RuntimeError(
            f"❌ 无法在 {quant_method.upper()} 量化模型上训练 LoRA。\n"
            f"请在前端训练表单里把'训练底座'切换为未量化的 FP16 版本。"
        )

    template = detect_template(base_model)
    log(f"使用模板: {template}")

    if not os.path.exists(dataset_file):
        raise RuntimeError(f"数据集文件不存在: {dataset_file}")

    with open(dataset_file, 'r', encoding='utf-8') as f:
        valid_lines = [l for l in f if l.strip()]

    if len(valid_lines) == 0:
        raise RuntimeError(f"训练数据集为空（0 条样本）。")

    log(f"数据集有效：{len(valid_lines)} 条样本")

    # ★ P-4/P-7: 数据体检（不合格 ⇒ 拒绝开训 ✓ 会把报告写到 output_dir ✓）
    cutoff_len = int(os.environ.get("TRAIN_CUTOFF_LEN", "2048"))
    data_preflight(dataset_file, cutoff_len=cutoff_len, output_dir=output_dir, log_fn=log)

    dataset_dir = os.path.join(PROJECT_DIR, "training_data")
    os.makedirs(dataset_dir, exist_ok=True)

    dataset_name = "auto_train"
    dataset_info = {
        dataset_name: {
            "file_name": os.path.abspath(dataset_file),
            "formatting": "sharegpt",
            "columns": {"messages": "conversations"}
        }
    }
    info_path = os.path.join(dataset_dir, "dataset_info.json")
    with open(info_path, 'w', encoding='utf-8') as f:
        json.dump(dataset_info, f, ensure_ascii=False, indent=2)

    os.makedirs(output_dir, exist_ok=True)

    # ★ P-3: 校验都过了，现在才抢【单飞锁】（拿不到 ⇒ 拒绝并发 ✓）
    _lock_fd = acquire_train_lock()
    log(f"已取得训练锁: {_lock_path()}（pid={os.getpid()} ✓）")

    cmd = [
        resolve_llamafactory_cli(), "train",
        "--model_name_or_path", base_model,
        "--dataset", dataset_name,
        "--dataset_dir", dataset_dir,
        "--template", template,
        "--finetuning_type", "lora",
        "--lora_rank", str(rank),
        "--lora_alpha", str(alpha),
        "--lora_dropout", "0.05",
        "--output_dir", output_dir,
        "--per_device_train_batch_size", "1",
        "--gradient_accumulation_steps", "8",
        "--learning_rate", lr,
        "--num_train_epochs", str(epochs),
        "--lr_scheduler_type", "cosine",
        "--warmup_ratio", "0.1",
        "--logging_steps", "1",
        "--save_steps", str(save_steps),

        "--save_strategy", "epoch",

        "--val_size", "0.1",

        "--cutoff_len", str(cutoff_len),
        # ★ P-5: 梯度检查点 —— 7B QLoRA 在 16GB 卡上没有它会在 step 8~10 崩 ✗（实测两次 ✓）
        #   代价: 慢 ~20%，换 30~50% 激活显存 ✓ 对"能跑完"是决定性的 ✓
        "--gradient_checkpointing", "true",
        "--bf16", "true",
        "--plot_loss", "false",
        "--do_train", "true",
        "--overwrite_output_dir"
    ]

    cmd += ["--quantization_bit", "4"]

    log(f"启动训练: {' '.join(cmd)}")

    # 子进程 PATH 里补上 venv/bin，保证 llamafactory 内部再调用任何脚本都能命中
    child_env = os.environ.copy()
    child_env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + child_env.get("PATH", "")
    child_env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', bufsize=1, cwd=PROJECT_DIR,
        env=child_env
    )
    # ★ Q-2（2026-10-21）: 边打印边解析 ✓
    _last_emit = {'step': -1, 'n': 0}
    _progress_path = os.path.join(output_dir, 'train_progress.json')
    _progress = {'status': 'running', 'started_at': datetime.now().isoformat(),
                 'step': 0, 'total_steps': None, 'loss': None, 'epoch': None,
                 'last_line': ''}
    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(_progress_path, 'w', encoding='utf-8') as _pf:
            json.dump(_progress, _pf, ensure_ascii=False)
    except Exception:
        pass

    for line in proc.stdout:
        raw = line.rstrip()
        print(raw, flush=True)
        try:
            info = _parse_train_line(raw)
            if not info:
                continue
            # 只在新 step 时输出结构化行 ✓（避免刷屏 ✓）
            _st = info.get('step')
            _emit = (_st is not None and _st != _last_emit['step']) \
                or (info.get('loss') is not None and info.get('epoch') is not None
                    and _last_emit['n'] % 10 == 0)
            if _emit:
                if _st is not None:
                    _last_emit['step'] = _st
                _last_emit['n'] += 1
                _parts = []
                if _st is not None:
                    _tot = info.get('total_steps')
                    _parts.append('step=%d/%s' % (_st, _tot if _tot else '?'))
                if info.get('percent') is not None:
                    _parts.append('%d%%' % info['percent'])
                if info.get('loss') is not None:
                    _parts.append('loss=%.4f' % info['loss'])
                if info.get('epoch') is not None:
                    _parts.append('epoch=%.2f' % info['epoch'])
                if info.get('lr'):
                    _parts.append('lr=%s' % info['lr'])
                log('[progress] ' + ' '.join(_parts))
            # 更新进度文件 ✓
            _progress.update({k: v for k, v in info.items() if v is not None})
            _progress['last_line'] = raw[-300:]
            _progress['updated_at'] = datetime.now().isoformat()
            try:
                with open(_progress_path, 'w', encoding='utf-8') as _pf:
                    json.dump(_progress, _pf, ensure_ascii=False)
            except Exception:
                pass
        except Exception:
            pass          # 解析失败绝不影响训练 ✓
    proc.wait()

    # 训练结束 ⇒ 更新进度文件 ✓
    try:
        _progress['status'] = 'finished' if proc.returncode == 0 else 'failed'
        _progress['returncode'] = proc.returncode
        _progress['finished_at'] = datetime.now().isoformat()
        with open(_progress_path, 'w', encoding='utf-8') as _pf:
            json.dump(_progress, _pf, ensure_ascii=False)
        log('[progress] 训练结束 status=%s returncode=%s' % (
            _progress['status'], proc.returncode))
    except Exception:
        pass

    if proc.returncode != 0:
        raise RuntimeError(f"训练失败，退出码 {proc.returncode}")

    adapter_file = os.path.join(output_dir, "adapter_model.safetensors")
    config_file = os.path.join(output_dir, "adapter_config.json")

    if not os.path.exists(adapter_file) or not os.path.exists(config_file):
        try:
            contents = os.listdir(output_dir)
        except Exception:
            contents = []
        log(f"⚠️ 训练输出目录内容: {contents}")
        raise RuntimeError(f"训练进程返回成功，但未找到 adapter 文件。")

    # ===== 关键：修正 adapter_config.json =====
    fix_adapter_config(output_dir, base_model)

    # 复制 adapter 到 ~/loras/{lora_name}
    lora_target = os.path.expanduser(f"~/loras/{lora_name}")
    if os.path.exists(lora_target):
        shutil.rmtree(lora_target)
    os.makedirs(lora_target, exist_ok=True)

    for f in ["adapter_config.json", "adapter_model.safetensors"]:
        src = os.path.join(output_dir, f)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(lora_target, f))

    log(f"训练完成！LoRA 已注册: {lora_target}")
    log(f"绑定底座: {base_model}")
    _release_train_lock(_lock_fd)
    return lora_target


def _parse_argv(argv):
    """★ P-68（2026-10-19）: 兼容两种调用风格

    ① 旗标风格（scripts/train_semantic.sh 等 ✓）:
         --base_model X --dataset_file Y --lora_name Z --data_count N [--output_dir D] [--auto_restart_vllm]
    ② 位置参数（老脚本 / 我的临时脚本 ✓ 向后兼容 ✓）:
         <base_model> <dataset_file> <output_dir> <lora_name> <data_count>

    返回 dict（train() 的关键字参数 ✓）
    """
    if any(a.startswith('--') for a in argv):
        import argparse
        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument('--base_model', required=True)
        ap.add_argument('--dataset_file', required=True)
        ap.add_argument('--output_dir', default='')
        ap.add_argument('--lora_name', required=True)
        ap.add_argument('--data_count', type=int, required=True)
        # 这些是【平台层】参数，train_lora 本身不用 ✓ 接受并忽略 ✓（免得一键入口报"未知参数"✗）
        ap.add_argument('--auto_restart_vllm', action='store_true')
        ap.add_argument('--review_before_train', action='store_true')
        known, _unknown = ap.parse_known_args(argv)   # ★ 未知参数忽略 ✓ 不炸 ✗
        return {
            'base_model': known.base_model,
            'dataset_file': known.dataset_file,
            'output_dir': known.output_dir or str(ROOT / 'training_output' / known.lora_name),
            'lora_name': known.lora_name,
            'data_count': known.data_count,
        }
    if len(argv) < 5:
        print("用法(位置): python train_lora.py <base_model> <dataset_file> <output_dir> <lora_name> <data_count>")
        print("用法(旗标): python train_lora.py --base_model X --dataset_file Y --lora_name Z --data_count N [--output_dir D]")
        sys.exit(1)
    return {
        'base_model': argv[0],
        'dataset_file': argv[1],
        'output_dir': argv[2] or str(ROOT / 'training_output' / argv[3]),
        'lora_name': argv[3],
        'data_count': int(argv[4]),
    }


if __name__ == "__main__":
    try:
        train(**_parse_argv(sys.argv[1:]))
    except Exception as e:
        # ★ 失败也要留下明确原因（数据体检不合格 / 已有训练在跑 / 训练崩 ✓）
        log(f"❌ 失败：{e}")
        sys.exit(1)