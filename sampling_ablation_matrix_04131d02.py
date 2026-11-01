"""（分布式追踪的时钟漂移场景）如何避免把分布式追踪的时钟漂移的噪声当成信号？用抽样与样本量分析给出误判模式的判别特征。

测量：抽样与样本量分析（min_samples）；协议：消融矩阵。
仅依赖 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

SEP = ','
MEASURE_FIELD = 'min_samples'
FAILURE_MODE = '提示词变化'


def _read_rows(path: str, limit: int = 0) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if SEP and SEP in line:
                label, _, payload = line.partition(SEP)
                rows.append((label.strip(), payload.strip()))
            else:
                rows.append(("", line.strip()))
            if limit and len(rows) >= limit:
                break
    return rows


def _nums(values: list[str]) -> list[float]:
    out: list[float] = []
    for v in values:
        try:
            out.append(float(v))
        except ValueError:
            continue
    return out


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac



def measure_sampling(rows: list[tuple[str, str]]) -> dict:
    """样本量分析：重复抽样估计波动，收敛到所需最小样本量。"""
    import random

    out: dict = {"metric": "sampling", "unit": "条"}
    values = _nums([p for _, p in rows])
    if not values:
        out["min_samples"] = None
        out["note"] = "样本无法解析为数值"
        return out
    rng = random.Random(20261031)
    effect = abs(max(values) - min(values)) or 1e-9
    curve: list[dict] = []
    need = None
    for size in (10, 20, 50, 100, 200, 500):
        if size > len(values):
            break
        spreads = []
        for _ in range(200):
            sample = [rng.choice(values) for _ in range(size)]
            spreads.append(max(sample) - min(sample))
        width = sum(spreads) / len(spreads)
        curve.append({"n": size, "mean_spread": round(width, 6)})
        if need is None and width <= effect / 2:
            need = size
    out["min_samples"] = need
    out["effect_size"] = round(effect, 6)
    out["convergence_curve"] = curve
    out["converged"] = need is not None
    return out



def measure(rows: list[tuple[str, str]]) -> dict:
    """按测量族分派到具体测量函数。"""
    return measure_sampling(rows)



def run_protocol(measure, rows: list[tuple[str, str]], args: argparse.Namespace) -> dict:
    """消融矩阵：逐组关闭组件，找出最影响结论的开关。"""
    components = [c for c in (args.components or "").split(",") if c.strip()]
    matrix: dict[str, object] = {}
    for combo in range(1 << len(components)) if components else [0]:
        subset = [components[i] for i in range(len(components)) if combo >> i & 1]
        filtered = rows
        for comp in components:
            if comp not in subset:
                filtered = [r for r in filtered if comp not in r[1]]
        matrix["+".join(subset) or "baseline"] = measure(filtered).get('min_samples')
    numeric = {k: v for k, v in matrix.items() if isinstance(v, (int, float))}
    key_component = None
    if numeric and len(numeric) > 1:
        base = numeric.get("baseline", numeric[list(numeric)[0]])
        key_component = max(numeric, key=lambda k: abs(numeric[k] - base))
    return {"protocol": "ablation_matrix", "matrix": matrix,
            "key_component": key_component, "variant_count": len(matrix)}



def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="（分布式追踪的时钟漂移场景）如何避免把分布式追踪的时钟漂移的噪声当成信号？用抽样与样本量分析给出误判模式的判别特征。")
    ap.add_argument("--input", help="输入文件路径")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 行")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    ap.add_argument("--threshold", type=float, default=0.0, help="判定阈值")
    ap.add_argument("--budget", type=int, default=50, help="预算条数")
    ap.add_argument("--bins", type=int, default=10, help="分桶数量")
    ap.add_argument("--components", default="", help="组件列表（逗号分隔）")
    args = ap.parse_args(argv)

    if not args.input:
        ap.print_help()
        return 0

    t0 = time.time()
    try:
        rows = _read_rows(args.input, args.limit)
    except OSError as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 2

    measured = measure(rows)
    result = run_protocol(measure, rows, args)
    result["measure"] = measured
    result["row_count"] = len(rows)
    result["failure_mode"] = FAILURE_MODE
    result["elapsed_seconds"] = round(time.time() - t0, 4)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        value = measured.get(MEASURE_FIELD)
        print(f"读取 1 个文件，共 {result['row_count']} 条记录")
        print(f"{MEASURE_FIELD} = {value}")
        print(f"处理完成，耗时 {result['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
