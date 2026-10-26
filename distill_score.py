# -*- coding: utf-8 -*-
"""distill_score.py —— S1（Q5 预设）：轨迹蒸馏价值自动评分

对齐《目标与方向统一 v2.0》B1：自动评分是否值得蒸馏（无需用户逐条判断）。
评分 = 可解释加权分（0–100），输出分项理由，阈值决定档位：
  ≥70 高价值（立即蒸馏）｜50–69 观察（进入候选池，重复出现再蒸馏）｜<50 不蒸馏
权重（可改，集中在 WEIGHTS）：
  success 0.30        任务成功且验证通过
  reusability 0.20    同类需求出现频次（相似任务次数）
  novelty 0.20        现有能力/工作流未覆盖（越新越高）
  decomposability 0.15 可拆解成单步能力（步数适中 2–6）
  quality 0.15        轨迹质量（tool_quality A-D / 工具调用完整度）

输入 record 字段（缺省按中性分处理）：
  success: bool
  occurrence: int（相似任务历史次数，来自 route_table.search/trajectories）
  covered: bool（现有能力/工作流是否已覆盖）
  steps: int（拆解步数；0=未测）
  tool_quality: "A"|"B"|"C"|"D"
  has_tool_calls: bool
  user_value: 0–1（可选：业务价值，如采购/招标类高价值任务=1）
"""
import json

WEIGHTS = {"success": 0.30, "reusability": 0.20, "novelty": 0.20,
           "decomposability": 0.15, "quality": 0.15}
THRESHOLD_HIGH = 70
THRESHOLD_WATCH = 50
GRADE_SCORE = {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.2}

CATEGORY_NAMES = {"success": "成功率", "reusability": "复用频次", "novelty": "新颖度",
                  "decomposability": "可拆解性", "quality": "轨迹质量"}


def _success(record):
    v = record.get("success")
    if v is None:
        return 0.5, "未提供成功标记（按中性 0.5）"
    return (1.0 if v else 0.0), ("成功" if v else "失败")


def _reusability(record):
    occ = int(record.get("occurrence") or 0)
    if occ <= 0:
        return 0.2, "首次出现"
    if occ == 1:
        return 0.5, "出现过 1 次"
    if occ < 5:
        return 0.8, "重复 %d 次" % occ
    return 1.0, "高频（%d 次）" % occ


def _novelty(record):
    covered = record.get("covered")
    if covered is True:
        return 0.1, "已有能力/工作流覆盖"
    if covered is False:
        return 1.0, "本地未覆盖（新能力）"
    return 0.6, "覆盖情况未知"


def _decomposability(record):
    steps = int(record.get("steps") or 0)
    if steps <= 0:
        return 0.4, "未测步数（先做拆步测试）"
    if steps == 1:
        return 0.5, "单步（可直接做成单步能力）"
    if 2 <= steps <= 6:
        return 1.0, "可拆 %d 步（适合工作流/单步能力）" % steps
    if steps <= 10:
        return 0.6, "步骤偏多（%d 步）" % steps
    return 0.3, "步骤过多（%d 步）" % steps


def _quality(record):
    grade = str(record.get("tool_quality") or "").upper()
    base = GRADE_SCORE.get(grade, 0.5)
    if record.get("has_tool_calls"):
        base = min(1.0, base + 0.1)
    why = "工具质量 %s" % (grade or "未评级")
    if record.get("has_tool_calls"):
        why += " + 含工具调用"
    return base, why


def score(record):
    """返回 {score, level, parts:[{key,name,value,weight,points,why}], reason}"""
    record = record or {}
    parts = []
    total = 0.0
    for key, fn in (("success", _success), ("reusability", _reusability),
                    ("novelty", _novelty), ("decomposability", _decomposability),
                    ("quality", _quality)):
        v, why = fn(record)
        w = WEIGHTS[key]
        pts = round(v * w * 100, 2)
        total += pts
        parts.append({"key": key, "name": CATEGORY_NAMES[key], "value": round(v, 3),
                      "weight": w, "points": pts, "why": why})
    # 可选业务价值加成（最高 +10）
    uv = record.get("user_value")
    if uv is not None:
        try:
            uv = max(0.0, min(1.0, float(uv)))
            bonus = round(uv * 10, 2)
            total += bonus
            parts.append({"key": "user_value", "name": "业务价值", "value": uv,
                          "weight": 0, "points": bonus, "why": "用户价值加成"})
        except Exception:
            pass
    total = round(max(0.0, min(100.0, total)), 1)
    if total >= THRESHOLD_HIGH:
        level = "high"
    elif total >= THRESHOLD_WATCH:
        level = "watch"
    else:
        level = "skip"
    return {"score": total, "level": level, "parts": parts,
            "thresholds": {"high": THRESHOLD_HIGH, "watch": THRESHOLD_WATCH}}


def score_batch(records):
    return [dict(score(r), record=r) for r in (records or [])]


if __name__ == "__main__":
    import sys
    recs = json.loads(sys.stdin.read() or "[]")
    print(json.dumps(score_batch(recs), ensure_ascii=False, indent=2))
