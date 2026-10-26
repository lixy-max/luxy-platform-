#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vLLM 进程管理器：启动/停止/切换底座模型"""
import os
import re
import sys
import json
import time
import signal
import fcntl
import subprocess
import requests
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PID_FILE = PROJECT_DIR / ".vllm.pid"
LOG_FILE = PROJECT_DIR / "vllm.log"
MODELS_DIR = Path.home() / "models"

DEFAULT_VLLM_ARGS = {
    "max_model_len": 16384,
    "gpu_memory_utilization": 0.55,   # ★ 实测: 0.45 装不下 32K 上下文（KV 只剩 0.43GiB ✗）⇒ 0.55 ✓   # ★ 2026-10-19: 默认降到 0.45（Windows 应用常占 8-9GB 显存 ✗）
    "max_loras": 2,
    "max_lora_rank": 128,
    "max_cpu_loras": 4,
    "max_num_seqs": 16,
}


def log(msg):
    print(f"[vllm_manager] {msg}", flush=True)


def detect_quantization(model_path):
    config_file = Path(model_path) / "config.json"
    if not config_file.exists():
        return None
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception:
        return None
    quant_config = config.get("quantization_config", {})
    quant_method = quant_config.get("quant_method", "").lower()
    if "awq" in quant_method:
        return "awq_marlin"
    if "gptq" in quant_method:
        return "gptq_marlin"
    if "fp8" in quant_method:
        return "fp8"
    return None


def estimate_model_size_gb(model_path):
    """估算模型文件总大小（GB）"""
    try:
        total = sum(f.stat().st_size for f in Path(model_path).rglob("*") if f.is_file())
        return round(total / (1024 ** 3), 2)
    except Exception:
        return 0


GPU_TOTAL_MIB = 16311          # RTX 5060 Ti
VRAM_OVERHEAD_MIB = 1200       # 桌面 + CUDA context 实测开销
CONTEXT_CHOICES = [1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072]
CONTEXT_MARGIN = 1.15          # 估算余量：宁可小一档，也不要启动失败
LAST_ARGS_FILE = PROJECT_DIR / ".vllm.args.json"


def _legacy_auto_config(size_gb):
    """读不到 config.json 时的兜底：按体积粗分档（旧逻辑）。"""
    if size_gb < 6:
        return {"max_model_len": 16384, "gpu_memory_utilization": 0.85}
    elif size_gb < 10:
        return {"max_model_len": 4096, "gpu_memory_utilization": 0.88}
    elif size_gb < 14:
        return {"max_model_len": 2048, "gpu_memory_utilization": 0.90}
    else:
        return {"max_model_len": 1024, "gpu_memory_utilization": 0.90}


def kv_bytes_per_token(model_path):
    """
    单个 token 的 KV cache 字节数 = 2(K/V) × 层数 × KV头数 × head_dim × 2(half)。
    读不到配置返回 None，由调用方退回体积分档。
    """
    try:
        with open(Path(model_path) / "config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        layers = int(cfg["num_hidden_layers"])
        attn_heads = int(cfg["num_attention_heads"])
        kv_heads = int(cfg.get("num_key_value_heads") or attn_heads)
        head_dim = cfg.get("head_dim") or (int(cfg["hidden_size"]) // attn_heads)
        return 2 * layers * kv_heads * int(head_dim) * 2
    except Exception:
        return None


def _model_max_position(model_path):
    try:
        with open(Path(model_path) / "config.json", "r", encoding="utf-8") as f:
            return int(json.load(f).get("max_position_embeddings") or 0) or None
    except Exception:
        return None


def auto_config_for_model(model_path, size_gb=None):
    """
    按"权重占用 + KV cache 需求"计算上下文窗口，而不是只看模型体积。

    旧实现只看磁盘体积分档，把 4B FP16（磁盘 7.6GB）误判进 <10GB 那一档（4096），
    于是 16GB 卡上明明能开 32K 上下文的 4B 模型被压到 4096 —— 这是"上下文不够"的根因。
    """
    if size_gb is None:
        size_gb = estimate_model_size_gb(model_path)

    kv_per_token = kv_bytes_per_token(model_path)
    if not kv_per_token:
        return _legacy_auto_config(size_gb)

    util = 0.88 if size_gb >= 6 else 0.85
    # 显存预算：总显存 × 利用上限 − 权重 − 系统/CUDA 开销
    budget_mib = GPU_TOTAL_MIB * util - size_gb * 1024 - VRAM_OVERHEAD_MIB
    if budget_mib <= 0:
        return {"max_model_len": 1024, "gpu_memory_utilization": 0.90}

    fit = int(budget_mib * 1024 * 1024 / kv_per_token / CONTEXT_MARGIN)
    choice = CONTEXT_CHOICES[0]
    for c in CONTEXT_CHOICES:
        if c <= fit:
            choice = c
    cap = _model_max_position(model_path)
    if cap:
        choice = min(choice, cap)
    log(f"KV/token={kv_per_token}B 可用KV≈{budget_mib}MiB 可容纳≈{fit} tokens -> max_model_len={choice}")
    return {"max_model_len": int(choice), "gpu_memory_utilization": util}


def _load_last_args(model_path):
    """读取同一模型上次成功启动的参数（用于无参重启时保持上下文不被悄悄改小）。"""
    try:
        with open(LAST_ARGS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("model_path") == model_path:
            return d.get("args") or {}
    except Exception:
        pass
    return None


def _save_last_args(model_path, args):
    try:
        with open(LAST_ARGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"model_path": model_path, "args": args}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _proc_cmdline(pid):
    """读 /proc/<pid>/cmdline（进程不存在返回 ''）"""
    try:
        with open("/proc/%d/cmdline" % int(pid), "rb") as f:
            return f.read().replace(b"\x00", b" ").decode(errors="ignore")
    except Exception:
        return ""


def _pid_is_vllm(pid, port=None):
    """★ 修 BUG（2026-10-26）：PID 可能被系统复用 → 只 os.kill(pid,0) 不够。
    必须验证 cmdline 里确实是 vllm.entrypoints（可选校验 --port）。"""
    cmd = _proc_cmdline(pid)
    if "vllm.entrypoints" not in cmd:
        return False
    if port is not None:
        p = str(int(port))
        if ("--port " + p) not in cmd and ("--port=" + p) not in cmd:
            # 兼容：没显式写 --port 但模型在跑（默认 8000）
            if p != "8000":
                return False
    return True


def find_vllm_pids(port=None):
    """扫描系统里的 vLLM 进程（用于 PID 文件缺失/stale 时纠正）"""
    import subprocess
    out = []
    try:
        r = subprocess.run(["pgrep", "-af", "vllm.entrypoints.openai.api_server"],
                           capture_output=True, text=True, timeout=5)
        for ln in (r.stdout or "").splitlines():
            parts = ln.strip().split(None, 1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            cmd = parts[1] if len(parts) > 1 else ""
            if port is None or ("--port " + str(port)) in cmd or ("--port=" + str(port)) in cmd:
                out.append(pid)
    except Exception:
        pass
    return out


def is_running(port=None):
    """通过 PID 文件检查 vLLM 是否在跑（★ 校验 cmdline，防 PID 复用误报）"""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
        except (ProcessLookupError, ValueError):
            PID_FILE.unlink(missing_ok=True)
            return False, None
        except PermissionError:
            # ★ 修 BUG（2026-10-26）：PID 指向别的用户进程（如 pid=1）→ 不是我们管的 vLLM
            #   原来未捕获 PermissionError → /vllm/status 直接 500
            PID_FILE.unlink(missing_ok=True)
            return False, None
        # PID 存活但可能被复用 → 校验 cmdline
        if _pid_is_vllm(pid, port):
            return True, pid
        PID_FILE.unlink(missing_ok=True)
        # 顺便找真正的 vLLM 进程（手动启动/PID 文件过期）
        real = find_vllm_pids(port)
        if real:
            PID_FILE.write_text(str(real[0]))
            return True, real[0]
        return False, None
    return False, None


def is_port_responding(port=8000, timeout=3):
    """不管谁启动的，只要端口能响应就算在跑"""
    try:
        r = requests.get(f"http://127.0.0.1:{port}/v1/models", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def stop_vllm(timeout=3):
    """停止 vLLM 进程（兼容手动启动）。
    ★ 优化（2026-10-26）：原来 timeout=15 → 实测 stop 17s；vLLM 对 SIGTERM 反应慢，
      改为 6s 优雅等待后 SIGKILL（目标 <8s），并用端口确认是否真的停了。"""
    running, pid = is_running()

    if not running:
        if is_port_responding():
            log("检测到无 PID 记录的 vLLM 进程，使用 pkill 清理")
            subprocess.run(
                ["pkill", "-f", "vllm.entrypoints.openai.api_server"],
                check=False
            )
            time.sleep(5)
            return True
        log("vLLM 未运行")
        return True

    log(f"正在停止 vLLM (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    for _ in range(timeout):
        time.sleep(1)
        if not is_running()[0]:
            PID_FILE.unlink(missing_ok=True)
            log("vLLM 已停止")
            return True

    log("优雅停止超时，强制 kill -9")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for _ in range(5):
        time.sleep(0.5)
        if not is_port_responding():
            break
    PID_FILE.unlink(missing_ok=True)
    return True


def gpu_used_mib():
    """当前显存占用（MiB）；取不到返回 -1 ✓"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out.splitlines()[0].strip())
    except Exception:
        return -1


def reap_orphan_enginecores():
    """★ P-5: 清掉【没有对应 entrypoints 进程】的 VLLM::EngineCore 孤儿 ✓

    实测（2026-10-15）: 训练/启动失败后每次留 3~5 个孤儿（合计 ~15.5GB ✗）
    ⇒ 下一次启动必然 OOM ⇒ keeper 每 37 秒重拉一次、次次死 ✗
    安全前提: 当前【没有】vllm.entrypoints 进程在跑时才清 ✓（否则可能误杀正在加载的实例 ✗）
    """
    try:
        alive = subprocess.run(["pgrep", "-f", "vllm.entrypoints"],
                               capture_output=True, text=True, timeout=10).stdout.split()
        if alive:
            log(f"检测到 {len(alive)} 个正常 vLLM 进程 ⇒ 不清理孤儿（安全 ✓）")
            return []
        pids = subprocess.run(["pgrep", "-f", "VLLM::EngineCore"],
                              capture_output=True, text=True, timeout=10).stdout.split()
        killed = []
        for pid in pids:
            pid = pid.strip()
            if not pid.isdigit():
                continue
            try:
                os.kill(int(pid), signal.SIGKILL)
                killed.append(int(pid))
            except Exception:
                pass
        if killed:
            log(f"已清理 VLLM::EngineCore 孤儿 {killed}（清理前显存 {gpu_used_mib()} MiB）")
        return killed
    except Exception as e:
        log(f"孤儿清理异常（忽略）: {e}")
        return []


def wait_for_gpu_release(target_mib=2500, timeout=45):
    """等到显存回落到阈值以下（或超时）✓ 返回是否达标 ✓"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        used = gpu_used_mib()
        if 0 <= used <= target_mib:
            log(f"显存已释放到位: {used} MiB（阈值 {target_mib} ✓）")
            return True
        time.sleep(2)
    log(f"⚠️ 显存未在 {timeout}s 内回落到 {target_mib} MiB（当前 {gpu_used_mib()} MiB）")
    return False


# ★ 2026-10-19 实测教训（vllm.log 铁证）:
#   keeper 每 20s 探活 ⇒ 加载要 30~90s ⇒ 判定"未就绪" ⇒ 调 start
#   而 _start_vllm_impl 第一行就 stop_vllm() ⇒ 把正在加载的实例杀掉 ⇒ 再启动 ⇒ 又没好 ⇒ 死循环 ✗
#   18:45~18:49 的 vllm.log 里 5 次启动 + "leaked semaphore at shutdown" 就是这个循环 ✓
#   修法: 若存在【年轻实例（< 180s）】且端口未通 ⇒ 视为"正在加载" ✓ 直接返回成功 ✓ 不杀 ✗
LOADING_GRACE_SEC = 180


def _proc_age_sec(pid):
    """进程已运行秒数（用于判断是不是"正在加载的年轻实例"✓）"""
    try:
        import subprocess
        out = subprocess.run(["ps", "-o", "etimes=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=3)
        return int((out.stdout or "").strip() or 0)
    except Exception:
        return None


def loading_in_progress(port=8000):
    """是否有"正在加载"的实例 ✓ 返回 (bool, pid)"""
    try:
        running, pid = is_running()
        if not running:
            return False, None
        if is_port_responding(port, timeout=1):
            return False, pid          # 已就绪 ⇒ 不算加载中 ✓
        age = _proc_age_sec(pid)
        if age is not None and age < LOADING_GRACE_SEC:
            return True, pid
    except Exception:
        pass
    return False, None


def start_vllm(model_path, port=8000, extra_args=None, venv_python=None):
    """★ P-12: 启动互斥 + 幂等 —— 同一时刻只允许一个启动流程 ✓

    实测问题: keeper 每 20s 巡检没起来就再发一次 start ⇒ 与手动 start 互相踩，
    表现为 pid 每 37 秒换一个、每个都 OOM 死掉 ✗（日志 pid 4562→4806→5051→5293… ✓）
    """
    lock_path = PROJECT_DIR / ".vllm.start.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        running, pid = is_running()
        return (True, pid) if running else (False, "另一个 vLLM 启动流程正在进行中（已有锁 ✓）")
    try:
        if is_port_responding(port, timeout=2):
            running, pid = is_running()
            log(f"vLLM 已在运行（PID {pid}）⇒ 跳过重复启动 ✓")
            return True, pid
        # ★ 加载中保护（2026-10-19）: 存在年轻实例但端口未通 ⇒ 它就是"正在加载" ✓ 别杀 ✗
        _loading, _lpid = loading_in_progress(port)
        if _loading:
            _age = _proc_age_sec(_lpid)
            log(f"vLLM 正在加载（PID {_lpid} · {_age}s）⇒ 本次不重启 ✓（避免自杀循环 ✗）")
            return True, _lpid
        return _start_vllm_impl(model_path, port=port, extra_args=extra_args,
                                venv_python=venv_python)
    finally:
        try:
            os.ftruncate(fd, 0)
        except Exception:
            pass
        os.close(fd)


def _start_vllm_impl(model_path, port=8000, extra_args=None, venv_python=None):
    if not os.path.exists(model_path):
        return False, f"模型路径不存在: {model_path}"

    # ★ 2026-10-19: 再护一次 —— 别杀正在加载的实例 ✗
    _run, _pid = is_running()
    if _run:
        _age = _proc_age_sec(_pid)
        if _age is not None and _age < LOADING_GRACE_SEC and not is_port_responding(port, timeout=1):
            log(f"存在年轻实例（PID {_pid} · {_age}s）且端口未通 ⇒ 判定为加载中 ✓ 不杀 ✗")
            return True, _pid
    stop_vllm()
    # ★ P-5: 停完必须【等显存真的释放】+ 清孤儿 ✗
    #   实测: 发完 stop 就继续 ⇒ 旧实例还占着 15GB ⇒ 新实例 OOM 死掉 ✗
    wait_for_gpu_release(target_mib=2500, timeout=30)
    reap_orphan_enginecores()
    wait_for_gpu_release(target_mib=2500, timeout=15)
    quantization = detect_quantization(model_path)

    # ===== 自动根据模型权重 + KV cache 需求选参数 =====
    size_gb = estimate_model_size_gb(model_path)
    auto = auto_config_for_model(model_path, size_gb)
    log(f"模型大小: {size_gb} GB，自动配置: {auto}")

    args = dict(DEFAULT_VLLM_ARGS)
    args.update(auto)              # 用自动配置覆盖默认值

    # 只保留调用方真正指定的参数（None 视为"未指定"）
    explicit = {k: v for k, v in (extra_args or {}).items() if v is not None}

    # 无参重启（例如训练结束后自动拉起 vLLM）时沿用上次参数，
    # 避免把已选定的上下文窗口悄悄改小。
    if not explicit:
        last = _load_last_args(model_path)
        if last:
            args.update({k: v for k, v in last.items() if v is not None})
            log(f"沿用上次参数: {last}")

    if explicit:
        args.update(explicit)
        log(f"用户指定参数: {explicit}")

    if venv_python is None:
        venv_python = str(Path.home() / ".venv" / "bin" / "python")

    if not os.path.exists(venv_python):
        return False, f"Python 不存在: {venv_python}"

    # ★ P1（2026-10-26）：LoRA 参数改为可开关 + 绑定回环地址
    #   背景：14B 主脑（GPU0, 16G）带平台默认 LoRA 参数（max-loras=2/rank=128/
    #   max-num-seqs=16）实测额外占 2.5GiB ⇒ KV 只剩 2.14GiB（20480 上下文需 3.75GiB
    #   → 启动失败，估算上限仅 11648）。主脑不需要 LoRA（LoRA 在工蜂 7B 上），
    #   用 VLLM_ENABLE_LORA=0 起主脑：KV 4.67GiB、20480 上下文、25.5K tokens 容量 ✓
    #   · VLLM_ENABLE_LORA（默认 "1" 保持原行为 ✓）
    #   · VLLM_HOST（默认 127.0.0.1：原来不传 --host，vLLM 默认可能是 0.0.0.0，
    #     同机其他 distro/进程可直连 ⇒ 安全隐患；显式绑回环 ✓）
    _enable_lora = os.environ.get("VLLM_ENABLE_LORA", "1") != "0"
    _vllm_host = os.environ.get("VLLM_HOST", "127.0.0.1")
    cmd = [
        venv_python, "-m", "vllm.entrypoints.openai.api_server",
        "--model", model_path,
        "--host", _vllm_host,
    ]
    if _enable_lora:
        cmd += [
            "--enable-lora",
            "--max-loras", str(args["max_loras"]),
            "--max-lora-rank", str(args["max_lora_rank"]),
            "--max-cpu-loras", str(args["max_cpu_loras"]),
        ]
    cmd += [
        "--max-model-len", str(args["max_model_len"]),
        "--gpu-memory-utilization", str(args["gpu_memory_utilization"]),
        "--enable-prefix-caching",
        "--dtype", "half",
        "--port", str(port),
        "--enforce-eager",
        "--max-num-seqs", str(args["max_num_seqs"]),
        # ★ function-calling（本地 agent 调工具必须 ✓）
        #   实测: 不带这两个参数时，带 tools 的请求会被 vLLM 拒绝:
        #   「"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set」
        #   → 工作台 agent 一选本地模型就失败 ✓
        "--enable-auto-tool-choice",
        "--tool-call-parser", "hermes",     # Qwen2.5-Instruct 的 tool 调用格式 ✓
    ]
    if quantization:
        cmd += ["--quantization", quantization]

    env = os.environ.copy()
    # ★ GPU 配置（2026-10-19 重设计 ✓）: 只有【双卡模式】才指定 GPU ✓
    #   · gpu_mode="auto"（默认 ✓）⇒ **不设置** CUDA_VISIBLE_DEVICES ✓
    #     ⇒ vLLM 用默认卡（原行为 ✓）✓ 单卡用户开箱即用 ✓
    #   · gpu_mode="dual" ⇒ 用 settings.inference_gpu ✓
    #   · 环境变量 VLLM_GPU_INDEX 可强制覆盖 ✓（高级 ✓）
    try:
        import gpu_config as _gcfg
        _st = {}
        try:
            _st = json.load(open(os.path.join(PROJECT_DIR, "settings.json"), encoding="utf-8"))
        except Exception:
            _st = {}
        _env_gpu = _gcfg.cuda_device_env(_st, "inference")
        _c = _gcfg.get_gpu_config(_st)
        _force = os.environ.get("VLLM_GPU_INDEX", "")
        if _force:
            _env_gpu = {"CUDA_VISIBLE_DEVICES": str(_force)}
            print(f"[vllm] CUDA_VISIBLE_DEVICES={_force} ✓（环境变量强制 ✓）", flush=True)
        elif _env_gpu:
            env.update(_env_gpu)
            print(f"[vllm] 双卡模式 ✓ CUDA_VISIBLE_DEVICES={_env_gpu.get('CUDA_VISIBLE_DEVICES')}（推理卡 ✓）", flush=True)
        else:
            print(f"[vllm] 单卡模式 ✓ 不指定 GPU（用默认卡 ✓）| {_c['reason']}", flush=True)
    except Exception as _ge:
        print(f"[vllm] GPU 配置读取失败（用默认 ✓）: {_ge}", flush=True)
    env["VLLM_USE_FLASHINFER_SAMPLER"] = "0"
    env["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] = "True" if _enable_lora else "False"
    env["PYTHONIOENCODING"] = "utf-8"

    log(f"启动 vLLM: {model_path}")
    log(f"量化方式: {quantization or 'FP16/BF16'}")
    log(f"端口: {port}")

    log_handle = open(LOG_FILE, 'a', encoding='utf-8')
    log_handle.write(f"\n{'='*60}\n")
    log_handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 {model_path}\n")
    log_handle.write(f"模型大小: {size_gb} GB\n")
    log_handle.write(f"最终参数: max_model_len={args['max_model_len']}, gpu_util={args['gpu_memory_utilization']}\n")
    log_handle.write(f"命令: {' '.join(cmd)}\n")
    log_handle.flush()

    proc = subprocess.Popen(
        cmd,
        stdout=log_handle, stderr=subprocess.STDOUT,
        env=env, start_new_session=True,
        cwd=str(PROJECT_DIR)
    )

    PID_FILE.write_text(str(proc.pid))
    _save_last_args(model_path, args)
    log(f"vLLM 已启动，PID: {proc.pid}，等待就绪...")
    return True, proc.pid


def wait_until_ready(port=8000, timeout=180):
    start = time.time()
    while time.time() - start < timeout:
        if is_port_responding(port):
            return True
        time.sleep(3)
    return False


def get_status(port=8000):
    """查询 vLLM 当前状态（不管谁启动的，只要端口能响应就算在跑）"""
    try:
        running, pid = is_running(port)
    except Exception:
        running, pid = False, None
    result = {"running": False, "pid": pid, "model": None, "port": port}
    # ★ 修 BUG：PID 文件 stale 但端口有 vLLM（手动启动）→ 扫描真实 PID 补上
    if not pid:
        _real = find_vllm_pids(port)
        if _real:
            result["pid"] = _real[0]
            try:
                PID_FILE.write_text(str(_real[0]))
            except Exception:
                pass

    try:
        r = requests.get(f"http://127.0.0.1:{port}/v1/models", timeout=3)
        if r.status_code == 200:
            result["running"] = True
            models = r.json().get("data", [])
            for m in models:
                if m["id"].startswith("/"):
                    result["model"] = m["id"]
                    break
    except Exception:
        pass

    return result


def list_local_models():
    if not MODELS_DIR.exists():
        return []
    models = []
    for name in sorted(os.listdir(MODELS_DIR)):
        path = MODELS_DIR / name
        if not path.is_dir():
            continue
        config_file = path / "config.json"
        if not config_file.exists():
            continue
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception:
            config = {}
        quant_config = config.get("quantization_config", {})
        quant_method = quant_config.get("quant_method", "fp16")
        total_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        models.append({
            "name": name,
            "path": str(path),
            "quantization": quant_method,
            "size_gb": round(total_size / (1024**3), 2),
            "architecture": config.get("architectures", ["unknown"])[0]
        })
    return models


def get_log_tail(lines=100):
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        all_lines = f.readlines()
    return [l.rstrip() for l in all_lines[-lines:]]


def _extract_failure_reason(tail_lines=120):
    """从日志尾部提取人类可读的失败原因"""
    lines = get_log_tail(tail_lines)
    if not lines:
        return '无法读取 vLLM 日志'

    text = '\n'.join(lines)

    # 1) GPU 显存预算不足（desired > free）—— 英文原生报错
    m = re.search(
        r'Free memory on device \(([\d.]+)/([\d.]+) GiB\) on startup is less than desired '
        r'GPU memory utilization \(([\d.]+), ([\d.]+) GiB\)',
        text
    )
    if m:
        free = m.group(1)
        total = m.group(2)
        util = m.group(3)
        desired = m.group(4)
        return (
            f"GPU 显存预算不足\n"
            f"当前空闲 {free} GiB（总 {total} GiB），但 gpu_memory_utilization={util} "
            f"要求 {desired} GiB，无法满足。\n"
            f"建议：把 gpu_memory_utilization 降到 0.85 以下，或关闭占用显存的其它程序。"
        )

    # 2) KV cache 装不下
    if 'Available KV cache memory' in text and 'KV cache is needed' in text:
        avail_match = re.search(r'Available KV cache memory: ([\d.]+) GiB', text)
        need_match = re.search(r'\(([\d.]+) GiB KV cache is needed', text)
        avail = avail_match.group(1) if avail_match else '?'
        need = need_match.group(1) if need_match else '?'
        max_len_match = re.search(r"with the models's max seq len \((\d+)\)", text)
        max_len = max_len_match.group(1) if max_len_match else '?'
        est_len_match = re.search(r'estimated maximum model length is (\d+)', text)
        est_len = est_len_match.group(1) if est_len_match else '?'
        return (
            f"显存不足（KV cache 装不下）\n"
            f"当前 max_model_len={max_len}，需要 {need} GiB KV cache，"
            f"但只剩 {avail} GiB 可用。\n"
            f"根据当前显存，建议 max_model_len 不超过 {est_len}。"
        )

    # 3) CUDA OOM
    if 'CUDA out of memory' in text or 'OutOfMemoryError' in text:
        return 'CUDA 显存溢出。请降低 max_model_len 或提高 gpu_memory_utilization。'

    # 4) 模型加载失败
    if 'Error no file named' in text or 'FileNotFoundError' in text:
        return '模型文件缺失或损坏，请重新上传。'

    # 5) 引擎初始化失败 → 尽力抓取真正的报错行
    if 'Engine core initialization failed' in text:
        err_lines = [l for l in lines if 'ValueError:' in l or 'RuntimeError:' in l]
        if err_lines:
            last_err = err_lines[-1]
            # 去掉 "(EngineCore_DP0 pid=xxxx) ERROR ... [core.py:708]   " 这类前缀
            if '] ' in last_err:
                last_err = last_err.split('] ', 1)[-1]
            return 'vLLM 引擎初始化失败：\n' + last_err
        return 'vLLM 引擎初始化失败。请查看下方日志定位具体原因。'

    # 6) 兜底：返回最后几行
    return '\n'.join(lines[-8:])


def wait_for_startup_or_fail(port=8000, timeout=180, pid=None):
    """
    等待 vLLM 就绪，或检测到失败/超时。
    返回 (status, detail)
    - status: 'ready' | 'failed' | 'timeout'
    """
    start = time.time()
    while time.time() - start < timeout:
        # 1. 端口响应 = 就绪
        if is_port_responding(port):
            return 'ready', ''

        # 2. 进程已死 = 失败
        if pid:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                time.sleep(1)
                return 'failed', _extract_failure_reason()

        time.sleep(2)

    # 超时
    detail = _extract_failure_reason()
    return 'timeout', detail or f'启动超时（{timeout} 秒），请检查 vLLM 日志'


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "list"])
    parser.add_argument("--model", help="模型路径")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.action == "start":
        if not args.model:
            print("需要 --model 参数")
            sys.exit(1)
        ok, msg = start_vllm(args.model, args.port)
        print(f"启动结果: {ok}, {msg}")
    elif args.action == "stop":
        stop_vllm()
    elif args.action == "status":
        print(json.dumps(get_status(args.port), indent=2, ensure_ascii=False))
    elif args.action == "list":
        print(json.dumps(list_local_models(), indent=2, ensure_ascii=False))