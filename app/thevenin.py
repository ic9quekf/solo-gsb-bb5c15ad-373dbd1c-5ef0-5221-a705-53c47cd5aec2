"""两节点端口戴维南等效（Thevenin equivalent）。

请求体与 ``/api/solve`` 的网络 JSON 完全一致，另带一个 ``port`` 段：

    {
      ...网络...,
      "port": {"node_p": "2", "node_n": "0"}
    }

计算分两个阶段，各自完整复用 :func:`app.solver.solve` 的 MNA 求解与
结构诊断（浮置子网 / 电压源冲突 / 奇异）：

1. **开路电压**（``open_circuit``）：直接求解原网络，取端口电位差
   ``V_oc = V(node_p) - V(node_n)``。端口不挂任何元件，天然开路。
2. **等效电阻**（``equivalent_resistance``）：在网络副本上把独立电压源
   置零（0 V，相当于短路）、独立电流源置零（0 A，相当于开路），再从
   ``node_n`` 向 ``node_p`` 注入 1 A 测试电流（电流从 n 流向 p，
   即向 p 注入 +1 A、从 n 抽出 1 A），则
   ``R_eq = V(node_p) - V(node_n)``（Ω）。

端口参数本身（缺少 port 段 / 端点不在节点表 / 两端相同）无法开工，
直接抛出 :class:`CircuitError`（``port_validation``）。任一计算阶段
失败时，把该阶段 solve 抛出的现成错误分组原样透传，并给每个分组打上
``stage`` 标记，绝不返回半截伪结果。
"""

from __future__ import annotations

import copy
from typing import Any

from .solver import CircuitError, solve

_TEST_SOURCE_ID = "__port_Itest__"
_TEST_CURRENT = 1.0
_TINY = 1e-12

STAGE_OPEN_CIRCUIT = "open_circuit"
STAGE_RESISTANCE = "equivalent_resistance"


def _port_error(items: list[dict[str, str]]) -> CircuitError:
    return CircuitError(
        [{"code": "port_validation", "message": "端口参数未通过校验", "items": items}]
    )


def _with_stage(exc: CircuitError, stage: str) -> CircuitError:
    """给既有错误分组补上阶段标记（浅拷贝分组，不动 items 内容）。"""
    return CircuitError([{**group, "stage": stage} for group in exc.errors])


def _node_names(payload: dict[str, Any]) -> set[str]:
    """从请求体兜底提取节点表中的非空节点名（仅用于端口归属判断）。"""
    names: set[str] = set()
    raw_nodes = payload.get("nodes")
    if isinstance(raw_nodes, list):
        for raw in raw_nodes:
            name = str(raw).strip() if raw is not None else ""
            if name:
                names.add(name)
    return names


def thevenin_equivalent(payload: dict[str, Any]) -> dict[str, Any]:
    """计算两节点端口的开路电压与戴维南等效电阻。"""
    if not isinstance(payload, dict):
        raise _port_error([{"ref": "-", "detail": "请求体应为对象"}])

    port = payload.get("port")
    if not isinstance(port, dict):
        raise _port_error([{"ref": "-", "detail": "缺少 port 端口参数对象"}])

    node_p = str(port.get("node_p") or "").strip()
    node_n = str(port.get("node_n") or "").strip()

    # --------------------------------------------------------- 端口参数校验
    problems: list[dict[str, str]] = []
    if not node_p:
        problems.append({"ref": "端口 p 端", "detail": "未指定端口正端 p 节点"})
    if not node_n:
        problems.append({"ref": "端口 n 端", "detail": "未指定端口负端 n 节点"})

    names = _node_names(payload)
    if node_p and node_p not in names:
        problems.append({"ref": node_p, "detail": f"端口正端 p 节点“{node_p}”不在节点表中"})
    if node_n and node_n not in names:
        problems.append({"ref": node_n, "detail": f"端口负端 n 节点“{node_n}”不在节点表中"})
    if node_p and node_n and node_p == node_n:
        problems.append(
            {"ref": node_p, "detail": "端口的 p、n 端必须选择不同节点（不能对同一节点取等效）"}
        )
    if problems:
        raise _port_error(problems)

    # ----------------------------------------------- 阶段 1：原网络开路电压
    try:
        open_result = solve(copy.deepcopy(payload))
    except CircuitError as exc:
        raise _with_stage(exc, STAGE_OPEN_CIRCUIT)

    oc_voltage = {
        str(row["id"]): float(row["voltage"]) for row in open_result["node_voltages"]
    }
    v_oc = oc_voltage[node_p] - oc_voltage[node_n]
    if abs(v_oc) < _TINY:
        v_oc = 0.0

    # ----------------------------- 阶段 2：独立源置零 + 1 A 测试电流（副本）
    probe = copy.deepcopy(payload)
    used_ids = {
        str(raw.get("id") or "").strip()
        for raw in probe.get("elements", [])
        if isinstance(raw, dict)
    }
    test_id = _TEST_SOURCE_ID
    suffix = 2
    while test_id in used_ids:
        test_id = f"{_TEST_SOURCE_ID}_{suffix}"
        suffix += 1

    for raw in probe["elements"]:
        if isinstance(raw, dict):
            etype = str(raw.get("type") or "").strip().upper()
            if etype in ("V", "I"):
                # V→0V（短路）、I→0A（开路，MNA 中即零注入）
                raw["value"] = 0.0

    # 测试电流方向为“端口 n → 端口 p”：按 solver 约定，I 元件正值电流由
    # node_p 流向 node_n，因此把元件两端登记为 (端口n, 端口p)。盖印效果为
    # 端口 p 得到 +1 A 注入、端口 n 被抽走 1 A。
    probe["elements"].append(
        {
            "id": test_id,
            "type": "I",
            "node_p": node_n,
            "node_n": node_p,
            "value": _TEST_CURRENT,
        }
    )

    try:
        probe_result = solve(probe)
    except CircuitError as exc:
        raise _with_stage(exc, STAGE_RESISTANCE)

    probe_voltage = {
        str(row["id"]): float(row["voltage"]) for row in probe_result["node_voltages"]
    }
    r_eq = (probe_voltage[node_p] - probe_voltage[node_n]) / _TEST_CURRENT
    if abs(r_eq) < _TINY:
        r_eq = 0.0

    return {
        "ok": True,
        "port": {"node_p": node_p, "node_n": node_n},
        "open_circuit_voltage": v_oc,
        "equivalent_resistance": r_eq,
        "test_current": _TEST_CURRENT,
        "warnings": open_result.get("warnings", []),
    }
