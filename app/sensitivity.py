"""元件敏感度分析（中心差分，归一化灵敏度）。

请求体与 ``/api/solve`` 的网络 JSON 完全一致，另带一个 ``sensitivity`` 段：

    {
      ...网络...,
      "sensitivity": {
        "quantity": "voltage",        // 或 "current"
        "target": "2",                // 节点编号 / 支路（元件）编号
        "ratio": 0.001                // 相对扰动比例，1e-4（0.01%）～ 0.1（10%）
      }
    }

先对**基准网络**完整复用 :func:`app.solver.solve` 求解一次，取出目标量
（节点电压或支路电流）的基准值；随后对元件表中的**每个 R / I / V 元件**，
在网络的**深拷贝**上分别把参数值乘以 ``1+ratio``（正扰动）与 ``1-ratio``
（负扰动），各自再复用一次 solve，以中心差分

    dF/dp ≈ ( F(p·(1+h)) − F(p·(1−h)) ) / ( 2·p·h )

近似目标对该元件参数的导数，并以

    S = (p / F0) · dF/dp = ( F+ − F− ) / ( 2·h·F0 )

给出**归一化（相对）灵敏度**：参数每相对变化 1% 时目标近似相对变化百分之几。
因此最终计算只使用目标的三个取值，无需单独回传扰动后的参数值
（``p·(1±h)`` 由前端按基准值自行换算展示）。

- **基准网络无法求解**（无接地 / 浮置子网 / 电压源冲突 / 奇异 / 输入非法）时，
  敏感度分析无法开始，直接抛出 :class:`CircuitError`，并给每个错误分组打上
  ``stage: "baseline"`` 标记（与戴维南接口的阶段标记风格一致）。
- 敏感度参数本身（缺少 sensitivity 段、目标类型非法、目标不在网络中、
  扰动比例越界等）无法开工，直接抛出 ``sensitivity_validation``。
- 基准成功后，**单个元件的任一扰动求解失败**（例如参数为 0 无法做相对扰动、
  扰动到某一侧时网络结构矛盾）时，只把该元件标记为 ``status: "error"``，
  错误分组内联并标注 ``side``（``plus`` / ``minus`` / ``both``），
  **不影响其它元件**的分析。
"""

from __future__ import annotations

import copy
import math
from typing import Any

from .solver import CircuitError, solve

SENSITIVITY_MIN_RATIO = 1e-4   # 0.01%
SENSITIVITY_MAX_RATIO = 1e-1   # 10%
_TINY = 1e-12

QUANTITY_VOLTAGE = "voltage"
QUANTITY_CURRENT = "current"

STAGE_BASELINE = "baseline"
SIDE_PLUS = "plus"
SIDE_MINUS = "minus"


def _reject(items: list[dict[str, str]]) -> CircuitError:
    return CircuitError(
        [{"code": "sensitivity_validation", "message": "敏感度参数未通过校验", "items": items}]
    )


def _with_stage(exc: CircuitError, stage: str) -> CircuitError:
    """给既有错误分组补上阶段标记（浅拷贝分组，不动 items 内容）。"""
    return CircuitError([{**group, "stage": stage} for group in exc.errors])


def _with_side(exc: CircuitError, side: str) -> CircuitError:
    """给扰动求解的错误分组补上扰动侧标记（plus / minus）。"""
    return CircuitError([{**group, "side": side} for group in exc.errors])


def _node_names(payload: dict[str, Any]) -> list[str]:
    """按请求体中的出现顺序兜底提取节点表中的非空节点名（去重）。"""
    names: list[str] = []
    seen: set[str] = set()
    raw_nodes = payload.get("nodes")
    if isinstance(raw_nodes, list):
        for raw in raw_nodes:
            name = str(raw).strip() if raw is not None else ""
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _element_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """按请求体中的顺序提取元件表中“看起来是对象”的行（合法性由 solve 兜底）。"""
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        return []
    return [raw for raw in raw_elements if isinstance(raw, dict)]


def sensitivity(payload: dict[str, Any]) -> dict[str, Any]:
    """对全部 R / I / V 元件做中心差分敏感度分析。"""
    problems: list[dict[str, str]] = []

    if not isinstance(payload, dict):
        raise _reject([{"ref": "-", "detail": "请求体应为对象"}])

    spec = payload.get("sensitivity")
    if not isinstance(spec, dict):
        raise _reject([{"ref": "-", "detail": "缺少 sensitivity 敏感度参数对象"}])

    # ------------------------------------------------------------- 目标类型
    quantity = str(spec.get("quantity") or "").strip()
    if quantity not in (QUANTITY_VOLTAGE, QUANTITY_CURRENT):
        problems.append(
            {
                "ref": "目标类型",
                "detail": f"目标类型“{spec.get('quantity')}”非法，应为 voltage（节点电压）或 current（支路电流）",
            }
        )

    # ------------------------------------------------------------- 扰动比例
    raw_ratio = spec.get("ratio")
    if isinstance(raw_ratio, bool) or not isinstance(raw_ratio, (int, float)):
        problems.append(
            {"ref": "相对扰动比例", "detail": f"相对扰动比例“{raw_ratio}”必须是数字"}
        )
        ratio = 0.0
    else:
        ratio = float(raw_ratio)
        if not math.isfinite(ratio):
            problems.append(
                {"ref": "相对扰动比例", "detail": f"相对扰动比例“{raw_ratio}”必须是有限实数"}
            )
        elif not (SENSITIVITY_MIN_RATIO <= ratio <= SENSITIVITY_MAX_RATIO):
            problems.append(
                {
                    "ref": "相对扰动比例",
                    "detail": (
                        "相对扰动比例必须在 "
                        f"{SENSITIVITY_MIN_RATIO * 100:g}%～{SENSITIVITY_MAX_RATIO * 100:g}%"
                        f" 之间（当前：{ratio * 100:g}%）"
                    ),
                }
            )

    # ------------------------------------------------------------- 目标对象
    target = str(spec.get("target") or "").strip()
    if not target:
        problems.append({"ref": "分析目标", "detail": "未指定分析目标（节点编号或支路编号）"})

    # 目标是否存在只能对照请求体兜底判断；solve 通过后节点 / 支路必然合法
    node_names = _node_names(payload)
    element_rows = _element_rows(payload)
    index_by_id = {
        str(raw.get("id") or "").strip(): i
        for i, raw in enumerate(element_rows)
        if str(raw.get("id") or "").strip()
    }
    if target and quantity == QUANTITY_VOLTAGE and target not in node_names:
        problems.append(
            {"ref": target, "detail": f"目标节点“{target}”不在节点表中"}
        )
    if target and quantity == QUANTITY_CURRENT and target not in index_by_id:
        problems.append(
            {"ref": target, "detail": f"目标支路（元件）“{target}”不在元件表中"}
        )

    if problems:
        raise _reject(problems)

    # ------------------------------------------------------------- 基准求解
    try:
        base = solve(copy.deepcopy(payload))
    except CircuitError as exc:
        raise _with_stage(exc, STAGE_BASELINE)

    if quantity == QUANTITY_VOLTAGE:
        voltage_by_id = {str(row["id"]): float(row["voltage"]) for row in base["node_voltages"]}
        f0 = voltage_by_id[target]
        target_unit = "V"
        target_name = f"节点 {target} 电压"
    else:
        current_by_id = {str(br["id"]): float(br["current"]) for br in base["branches"]}
        f0 = current_by_id[target]
        target_row = element_rows[index_by_id[target]]
        target_unit = "A"
        target_name = (
            f"支路 {target} 电流（{target_row.get('node_p', '')}→"
            f"{target_row.get('node_n', '')}）"
        )

    # 基准值为 0：归一化灵敏度的分母为 0，只能给出有量纲导数
    f0_zero = abs(f0) < _TINY

    def target_value(result: dict[str, Any]) -> float:
        if quantity == QUANTITY_VOLTAGE:
            table = {str(row["id"]): float(row["voltage"]) for row in result["node_voltages"]}
        else:
            table = {str(br["id"]): float(br["current"]) for br in result["branches"]}
        return table[target]

    # --------------------------------------------- 逐元件正负扰动 + 中心差分
    results: list[dict[str, Any]] = []
    for i, raw in enumerate(element_rows):
        eid = str(raw.get("id") or "").strip()
        etype = str(raw.get("type") or "").strip().upper()
        base_value = float(raw.get("value"))  # solve 已成功，值必然是有限数
        entry: dict[str, Any] = {
            "id": eid,
            "type": etype,
            "node_p": str(raw.get("node_p") or ""),
            "node_n": str(raw.get("node_n") or ""),
            "index": i,
            "base_value": base_value,
            "status": "ok",
        }

        if abs(base_value) < _TINY:
            # 零值元件（零值电流 / 电压源）无法定义“相对扰动”
            entry.update(
                status="error",
                side="both",
                errors=[
                    {
                        "code": "zero_parameter",
                        "message": (
                            f"元件 {eid} 的基准参数值为 0，无法施加相对扰动"
                            "（相对扰动量为 0，中心差分无定义），请改为非零基准值后重试"
                        ),
                        "items": [],
                        "side": "both",
                    }
                ],
            )
            results.append(entry)
            continue

        value_plus = base_value * (1.0 + ratio)
        value_minus = base_value * (1.0 - ratio)
        entry["value_plus"] = value_plus
        entry["value_minus"] = value_minus

        side_errors: list[dict[str, Any]] = []
        f_plus = f_minus = math.nan

        for side, perturbed in ((SIDE_PLUS, value_plus), (SIDE_MINUS, value_minus)):
            probe = copy.deepcopy(payload)
            probe["elements"][i]["value"] = perturbed
            try:
                probe_result = solve(probe)
            except CircuitError as exc:
                side_errors.extend(_with_side(exc, side).errors)
            else:
                value = target_value(probe_result)
                if not math.isfinite(value):
                    side_errors.append(
                        {
                            "code": "non_finite_target",
                            "message": f"元件 {eid} 的{('正' if side == SIDE_PLUS else '负')}扰动解中目标值不是有限实数",
                            "items": [],
                            "side": side,
                        }
                    )
                elif side == SIDE_PLUS:
                    f_plus = value
                else:
                    f_minus = value

        if side_errors:
            sides = {g.get("side") for g in side_errors}
            entry.update(
                status="error",
                side=(next(iter(sides)) if len(sides) == 1 else "both"),
                errors=side_errors,
            )
            results.append(entry)
            continue

        denom_param = 2.0 * base_value * ratio
        derivative = (f_plus - f_minus) / denom_param
        entry["target_plus"] = f_plus
        entry["target_minus"] = f_minus
        entry["derivative"] = 0.0 if abs(derivative) < _TINY else derivative
        if f0_zero:
            entry["sensitivity"] = None
        else:
            sensitivity_value = derivative * base_value / f0
            entry["sensitivity"] = (
                0.0 if abs(sensitivity_value) < _TINY else sensitivity_value
            )
        results.append(entry)

    # ----------------------------- 排序：按 |归一化灵敏度| 降序，失败项垫底
    def sort_key(entry: dict[str, Any]) -> tuple[int, float, int]:
        s = entry.get("sensitivity")
        if entry["status"] != "ok":
            return (2, 0.0, entry["index"])          # 失败项垫底
        if s is None:
            return (1, 0.0, entry["index"])          # 目标基准为 0，仅有导数
        return (0, -abs(float(s)), entry["index"])   # 按 |归一化灵敏度| 降序

    ranked = sorted(results, key=sort_key)
    rank = 0
    for entry in ranked:
        if entry["status"] == "ok" and entry["sensitivity"] is not None:
            rank += 1
            entry["rank"] = rank
        else:
            entry["rank"] = None

    ok_count = sum(1 for e in results if e["status"] == "ok")
    fail_count = len(results) - ok_count

    return {
        "ok": True,
        "sensitivity": {
            "quantity": quantity,
            "target": target,
            "target_name": target_name,
            "target_unit": target_unit,
            "target_base": f0,
            "ratio": ratio,
        },
        "elements": ranked,
        "counts": {"total": len(results), "ok": ok_count, "failed": fail_count},
        "warnings": base.get("warnings", []),
    }
