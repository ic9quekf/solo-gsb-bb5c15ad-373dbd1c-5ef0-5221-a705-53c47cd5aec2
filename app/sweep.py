"""单参数扫描：在指定元件参数的取值区间上逐点复用 :func:`solve`。

扫描请求体与 ``/api/solve`` 的网络 JSON 完全一致，另带一个 ``sweep`` 段：

    {
      ...网络...,
      "sweep": {"element_id": "R1", "start": 1.0, "end": 100.0, "points": 21}
    }

后端在网络的**深拷贝**上逐点替换元件参数，再调用现有求解与结构诊断，
因此扫描不会改写调用方传入的电路表。任一点求解失败（结构矛盾 / 奇异 /
该点取值非法）时，只把该点标记为 ``error`` 并原样保留错误分组，
不中断整次扫描。

扫描参数本身（元件缺失、区间非法、电阻范围含非正值、点数越界等）
无法成扫，直接抛出 :class:`CircuitError`（``sweep_validation``）。
"""

from __future__ import annotations

import copy
import math
from typing import Any

from .solver import CircuitError, solve

SWEEP_MIN_POINTS = 2
SWEEP_MAX_POINTS = 101

_UNIT = {"R": "Ω", "I": "A", "V": "V"}
_BRANCH_KEYS = ("id", "type", "node_p", "node_n")


def _reject(items: list[dict[str, str]]) -> CircuitError:
    return CircuitError(
        [{"code": "sweep_validation", "message": "扫描参数未通过校验", "items": items}]
    )


def _as_finite(raw: Any, name: str, problems: list[dict[str, str]]) -> float | None:
    """把起值 / 止值解析为有限实数，失败时追加校验问题。"""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        problems.append({"ref": name, "detail": f"{name}必须是数字（当前：{raw!r}）"})
        return None
    value = float(raw)
    if not math.isfinite(value):
        problems.append({"ref": name, "detail": f"{name}必须是有限实数（当前：{raw!r}）"})
        return None
    return value


def sweep(payload: dict[str, Any]) -> dict[str, Any]:
    """对单个元件参数做等间距扫描，返回每个采样点的求解结果或错误。"""
    problems: list[dict[str, str]] = []

    if not isinstance(payload, dict):
        raise _reject([{"ref": "-", "detail": "请求体应为对象"}])

    spec = payload.get("sweep")
    if not isinstance(spec, dict):
        raise _reject([{"ref": "-", "detail": "缺少 sweep 扫描参数对象"}])

    # ------------------------------------------------------------- 扫描元件
    element_id = str(spec.get("element_id") or "").strip()
    if not element_id:
        problems.append({"ref": "扫描元件", "detail": "未指定扫描元件编号"})

    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        problems.append(
            {"ref": element_id or "扫描元件", "detail": "元件表缺失或格式错误，无法定位扫描元件"}
        )
        matches: list[int] = []
    else:
        matches = [
            i
            for i, raw in enumerate(raw_elements)
            if isinstance(raw, dict) and str(raw.get("id") or "").strip() == element_id
        ]
        if element_id and not matches:
            problems.append({"ref": element_id, "detail": "扫描元件不在元件表中"})
        elif len(matches) > 1:
            problems.append(
                {
                    "ref": element_id,
                    "detail": "该元件编号在表中重复，无法唯一确定扫描对象，请先修改元件表",
                }
            )

    etype = ""
    if len(matches) == 1 and isinstance(raw_elements[matches[0]], dict):
        etype = str(raw_elements[matches[0]].get("type") or "").strip().upper()

    # ------------------------------------------------------------- 采样点数
    raw_points = spec.get("points")
    points: int | None = None
    if isinstance(raw_points, bool) or not isinstance(raw_points, int):
        problems.append(
            {"ref": "采样点数", "detail": f"采样点数必须是整数（当前：{raw_points!r}）"}
        )
    else:
        points = raw_points
        if not (SWEEP_MIN_POINTS <= points <= SWEEP_MAX_POINTS):
            problems.append(
                {
                    "ref": "采样点数",
                    "detail": (
                        f"采样点数必须在 {SWEEP_MIN_POINTS}～{SWEEP_MAX_POINTS} 之间"
                        f"（当前：{points}）"
                    ),
                }
            )

    # ------------------------------------------------------------- 起 / 止值
    start = _as_finite(spec.get("start"), "起值", problems)
    end = _as_finite(spec.get("end"), "止值", problems)
    if start is not None and end is not None and start == end:
        problems.append(
            {"ref": "扫描范围", "detail": "起值与止值不能相同，扫描区间至少要包含两个不同的采样值"}
        )

    # 电阻：线性插值的中间点是两端点的凸组合，两端点都为正即整段为正
    if etype == "R" and start is not None and end is not None and (start <= 0 or end <= 0):
        problems.append(
            {
                "ref": element_id,
                "detail": (
                    f"电阻扫描范围包含非正阻值：起值（{start:g}）与止值（{end:g}）"
                    "都必须大于 0"
                ),
            }
        )

    if problems:
        raise _reject(problems)

    assert points is not None and start is not None and end is not None
    values = [start + (end - start) * i / (points - 1) for i in range(points)]

    # ------------------------------------------------------------- 逐点求解
    schema_nodes: list[str] = []
    schema_branches: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []

    for i, value in enumerate(values):
        # 在副本上替换参数，绝不动调用方的电路表
        point_payload = copy.deepcopy(payload)
        point_payload["elements"][matches[0]]["value"] = value
        try:
            result = solve(point_payload)
        except CircuitError as exc:
            records.append(
                {"index": i, "value": value, "status": "error", "errors": exc.errors}
            )
            continue

        if not schema_nodes:
            schema_nodes = [row["id"] for row in result["node_voltages"]]
            schema_branches = [
                {key: str(branch[key]) for key in _BRANCH_KEYS}
                for branch in result["branches"]
            ]
        records.append(
            {
                "index": i,
                "value": value,
                "status": "ok",
                # 与 nodes / branches 同序对齐，前端切换观测量时无需再次请求
                "voltages": [float(row["voltage"]) for row in result["node_voltages"]],
                "currents": [float(branch["current"]) for branch in result["branches"]],
            }
        )

    # 所有点都失败（例如基准网络本身无接地）：从原始请求兜底推导观测量清单，
    # 前端仍可切换节点 / 支路，只是没有可绘制的数据
    if not schema_nodes:
        raw_nodes = payload.get("nodes")
        if isinstance(raw_nodes, list):
            seen: set[str] = set()
            for raw in raw_nodes:
                name = str(raw).strip() if raw is not None else ""
                if name and name not in seen:
                    seen.add(name)
                    schema_nodes.append(name)
        if isinstance(raw_elements, list):
            for raw in raw_elements:
                if isinstance(raw, dict):
                    schema_branches.append(
                        {key: str(raw.get(key) or "").strip() for key in _BRANCH_KEYS}
                    )

    return {
        "ok": True,
        "sweep": {
            "element_id": element_id,
            "type": etype,
            "unit": _UNIT.get(etype, ""),
            "start": start,
            "end": end,
            "points": points,
        },
        "nodes": schema_nodes,
        "branches": schema_branches,
        "points": records,
    }
