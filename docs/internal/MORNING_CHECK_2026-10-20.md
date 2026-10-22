# ☀️ 早晨验收清单（2026-10-20）

> 夜间自动链路已启动 ✓ 你早上起来按这个清单逐项验收即可 ✓
> 全部自动完成 ✓ 无需你干预 ✓（除非有异常 ⚠）

---

## 一、快速看结果（30 秒）

```bash
# ① 夜间报告（最重要的一个文件）
cat /mnt/e/WSL/_check/drill/out/MORNING_REPORT.md

# ② 数据量
wc -l /home/luxy/paper_lora_project/training_data/exercise_book_v3_3_0_merged.jsonl

# ③ 采集轮次
cat /mnt/e/WSL/_check/drill/out/c12_state.json
```

---

## 二、逐项验收

### ✅ 1. 12 轮采集（预计 06:00 完成）
```bash
# 轮次完成情况（应显示 12 轮都 done:true）
cat /mnt/e/WSL/_check/drill/out/c12_state.json | python3 -m json.tool

# 采集日志（温度/显存每轮都记 ✓）
tail -100 /mnt/e/WSL/_check/drill/out/c12_run.log

# 数据条数
grep -c 'c12_r' /mnt/e/WSL/_check/drill/out/runs.jsonl
```

**验收标准**：
- [ ] 12 轮全部 `done: true`
- [ ] 每轮 ok 数 ≥ 28（32 条里）
- [ ] 温度峰值 < 80°C（日志里搜 `GPU before`）
- [ ] 显存峰值 < 15.5GB
- [ ] 无 vLLM 崩溃（或崩溃后自动恢复 ✓）

### ✅ 2. 数据提取 + 合并（采集完自动跑 ✓）
```bash
tail -40 /mnt/e/WSL/_check/drill/out/night_chain.log | grep -E 'samples|parsed|F1-F8|Q1|总样本'
```

**验收标准**：
- [ ] F1-F8 全过 ✓
- [ ] Q1 system 占比 < 20% ✓
- [ ] 总样本量（预计 ~500-600）
- [ ] 真实错误观测有（42% 左右 ✓）

### ✅ 3. 训练（自动 ✓）
```bash
tail -40 /mnt/e/WSL/_check/drill/out/night_chain.log | grep -E 'training ok|LoRA|loss'
ls -la /home/luxy/loras/exercise-v3-3-0/
```

**验收标准**：
- [ ] `training ok: True`
- [ ] `adapter_model.safetensors` 存在（~80MB）
- [ ] loss 健康（1.0 左右 ✓ 不是 0.0008 ✗）

### ✅ 4. A/B 评测（自动 ✓）
```bash
# 工具指标（主指标 ✓）
cd /home/luxy/paper_lora_project
python3 scripts/tool_metrics.py --runs /mnt/e/WSL/_check/drill/out/runs.jsonl \
  --eval-set docs/EVAL_SET_32.json --compare v330_off v330_on

# 判分器（辅助）
python3 scripts/drill_score.py --runs /mnt/e/WSL/_check/drill/out/runs.jsonl \
  --compare v330_off v330_on
```

**验收标准**：
- [ ] n = 32 ✓（不是 12 ✓）
- [ ] 环境漂移 = 0 ✓（diff 无变化）
- [ ] 工具选择命中率变化（这是关键 ✓）
- [ ] 调用轮数变化
- [ ] **结论**：|t| > 2 且漂移=0 ⇒ 显著 ✓；否则照实说 ✗

### ✅ 5. 服务状态
```bash
curl -s -o /dev/null -w 'vllm=%{http_code} ' http://127.0.0.1:8000/v1/models
curl -s -o /dev/null -w 'backend=%{http_code} ' http://127.0.0.1:8001/health
curl -s -o /dev/null -w 'frontend=%{http_code}\n' http://127.0.0.1:8080/index3.html
```

**验收标准**：三个都是 200 ✓

---

## 三、夜间完成的所有产出

| 产出 | 位置 |
|---|---|
| **采集状态** | `E:\WSL\_check\drill\out\c12_state.json` |
| **采集日志** | `E:\WSL\_check\drill\out\c12_run.log` |
| **夜间链路日志** | `E:\WSL\_check\drill\out\night_chain.log` |
| **早晨报告** ⭐ | `E:\WSL\_check\drill\out\MORNING_REPORT.md` |
| **训练数据** | `training_data/exercise_book_v3_3_0_merged.jsonl` |
| **LoRA** | `~/loras/exercise-v3-3-0/` |
| **环境快照** | `data/eval_snapshots/before_v330.json` / `after_v330.json` |

---

## 四、⚠ 可能出问题的环节（如果邮件/报告没出来）

| 环节 | 怎么查 | 怎么修 |
|---|---|---|
| **采集中途 vLLM 崩** | `c12_run.log` 搜 `vLLM DOWN` | 脚本自动重启 3 次 ✓ 还不行手动 `POST /vllm/start` |
| **采集超时**（>7h） | 看 `c12_state.json` 卡在第几轮 | 重跑 `collect_12r.ps1` ✓ 自动续 ✓ |
| **训练失败** | `night_chain.log` 搜 `ABORT` | 看错误 ✓ 修后重跑 `night_chain.ps1` |
| **A/B 中断** | `night_chain.log` 搜 `arm` | 重跑 `run_lora_ab.mjs --ids ... --tag v330_off/on` |
| **GPU 过热** | 日志温度 | 已内置 80°C 保护 ✓ |

---

## 五、今天的任务队列（你起床后）

```
✅ 已完成（夜间）: 12 轮采集 + 数据提取合并 + 训练 + A/B
⏳ 接着做:
   1. 看 A/B 结果 ⇒ 决定下一步（继续训 / 换方向 / 止损 ✓）
   2. UI 美化 V1-V4（28h）
   3. 【最后】P-86 全参训练 + 专家模型 + 封装（8h + 15-30h 训练）
```

---

## 六、一句话

```
夜间全自动跑完: 采集 12 轮 ⇒ 提数据 ⇒ 训 LoRA ⇒ A/B ⇒ 出报告
你早上只需看: E:\WSL\_check\drill\out\MORNING_REPORT.md
然后按本清单逐项打勾 ✓
```

---

*生成于 2026-10-19 02:45 · 夜间链路 job: pwsh-95 · 采集 job: pwsh-88*
