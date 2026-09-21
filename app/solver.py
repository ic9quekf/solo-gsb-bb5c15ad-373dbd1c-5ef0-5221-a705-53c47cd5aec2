"""改进节点分析（Modified Nodal Analysis）求解核心。

支持元件：
  R  电阻        value = 电阻值（Ω，> 0），支路方向 node_p -> node_n
  I  独立电流源  value = 电流（A），正值表示电流从 node_p 流向 node_n
  V  独立电压源  value = 电动势（V），node_p 为正极、node_n 为负极

求解失败时抛出 :class:`CircuitError`，其中携带结构化的错误分组，
供前端区分“无接地 / 浮置子网 / 理想电压源互相矛盾”等情形。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

MAX_NODES = 30
MAX_ELEMENTS = 90
_ID_MAX = 24

# 电压源约束一致性判据（绝对 + 相对）
_V_ABS_TOL = 1e-9
_V_REL_TOL = 1e-8
# 条件数超过该值时给出数值稳定性警告
_COND_WARN = 1e10


class CircuitError(Exception):
    """结构化电路错误，errors 为错误分组列表。"""

    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__(errors[0]["message"] if errors else "电路校核失败")

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "errors": self.errors}


def _group(code: str, message: str, items: list[dict[str, Any]] | None = None):
    return CircuitError([{"code": code, "message": message, "items": items or []}])


def solve(payload: dict[str, Any]) -> dict[str, Any]:
    """校核并求解一个直流网络。

    成功返回结果字典；输入非法或网络结构矛盾时抛出 :class:`CircuitError`。
    """
    problems: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ nodes
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raw_nodes = []
    ground = str(payload.get("ground") or "").strip()

    if len(raw_nodes) > MAX_NODES:
        problems.append(
            {"ref": "-", "detail": f"节点数量为 {len(raw_nodes)}，超过上限 {MAX_NODES}"}
        )

    nodes: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_nodes):
        name = str(raw).strip() if raw is not None else ""
        ref = name or f"节点行 {i + 1}"
        if not name:
            problems.append({"ref": ref, "detail": "节点编号为空"})
        elif name in seen:
            problems.append({"ref": ref, "detail": "节点编号重复"})
        elif len(name) > _ID_MAX:
            problems.append({"ref": ref, "detail": f"节点编号超过 {_ID_MAX} 个字符"})
        else:
            seen.add(name)
            nodes.append(name)

    if len(nodes) == 0 and len(raw_nodes) <= MAX_NODES:
        problems.append({"ref": "-", "detail": "至少需要一个节点（接地节点）"})

    # --------------------------------------------------------------- elements
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        raw_elements = []
    if len(raw_elements) > MAX_ELEMENTS:
        problems.append(
            {"ref": "-", "detail": f"元件数量为 {len(raw_elements)}，超过上限 {MAX_ELEMENTS}"}
        )

    elements: list[dict[str, Any]] = []
    seen_e: set[str] = set()
    for i, raw in enumerate(raw_elements):
        if not isinstance(raw, dict):
            problems.append({"ref": f"元件行 {i + 1}", "detail": "元件数据格式错误"})
            continue
        eid = str(raw.get("id") or "").strip()
        etype = str(raw.get("type") or "").strip().upper()
        np_node = str(raw.get("node_p") or "").strip()
        nn_node = str(raw.get("node_n") or "").strip()
        raw_value = raw.get("value")
        ref = eid or f"元件行 {i + 1}"

        row_ok = True
        if not eid:
            problems.append({"ref": f"元件行 {i + 1}", "detail": "元件编号为空"})
            row_ok = False
        elif eid in seen_e:
            problems.append({"ref": eid, "detail": "元件编号重复"})
            row_ok = False
        elif len(eid) > _ID_MAX:
            problems.append({"ref": eid, "detail": f"元件编号超过 {_ID_MAX} 个字符"})
            row_ok = False

        if etype not in ("R", "I", "V"):
            problems.append({"ref": ref, "detail": f"未知元件类型“{etype}”，应为 R / I / V"})
            row_ok = False

        value = 0.0
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            problems.append({"ref": ref, "detail": f"参数值“{raw_value}”不是数字"})
            row_ok = False
        else:
            if not math.isfinite(value):
                problems.append({"ref": ref, "detail": "参数值必须为有限实数"})
                row_ok = False
            elif etype == "R" and value <= 0.0:
                problems.append({"ref": ref, "detail": "电阻值必须大于 0（理想导线请使用 0V 电压源）"})
                row_ok = False

        if not np_node or np_node not in seen:
            problems.append({"ref": ref, "detail": f"正端/起始节点“{np_node}”不在节点表中"})
            row_ok = False
        if not nn_node or nn_node not in seen:
            problems.append({"ref": ref, "detail": f"负端/终止节点“{nn_node}”不在节点表中"})
            row_ok = False
        if np_node and nn_node and np_node == nn_node:
            problems.append({"ref": ref, "detail": "元件两端不能接在同一个节点上（自环无意义）"})
            row_ok = False

        if row_ok:
            seen_e.add(eid)
            elements.append(
                {
                    "id": eid,
                    "type": etype,
                    "node_p": np_node,
                    "node_n": nn_node,
                    "value": value,
                }
            )

    if problems:
        raise CircuitError(
            [{"code": "validation", "message": "输入数据未通过校验", "items": problems}]
        )

    # ------------------------------------------------------------------ ground
    if not ground:
        raise _group("no_ground", "未指定接地节点：节点电压缺少参考点，无法求解")
    if ground not in seen:
        raise _group(
            "ground_not_found",
            f"接地节点“{ground}”不在节点表中",
            [{"ref": ground, "detail": "请在节点表中将其指定为接地，或改选已有节点"}],
        )

    # --------------------------- 结构诊断：浮置子网（R / V 构成导电连通图）----
    parent = {name: name for name in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for e in elements:
        if e["type"] in ("R", "V"):
            union(e["node_p"], e["node_n"])

    grounded_root = find(ground)
    subnet_map: dict[str, list[str]] = {}
    for name in nodes:
        root = find(name)
        if root != grounded_root:
            subnet_map.setdefault(root, []).append(name)

    errors: list[dict[str, Any]] = []
    if subnet_map:
        items = [{"nodes": sorted(members)} for members in subnet_map.values()]
        errors.append(
            {
                "code": "floating_subnet",
                "message": (
                    "检测到未通过电阻或电压源与接地节点连通的浮置子网，"
                    "其内节点电位无唯一解（仅靠电流源连接不算导通）"
                ),
                "items": items,
            }
        )

    # ---------------------- 结构诊断：理想电压源约束是否互相矛盾 --------------
    adj: dict[str, list[tuple[str, float, str]]] = {name: [] for name in nodes}
    for e in elements:
        if e["type"] == "V":
            p, q, val = e["node_p"], e["node_n"], e["value"]
            # 沿 p→q 的电位降落 V(p)-V(q) = E
            adj[p].append((q, val, e["id"]))
            adj[q].append((p, -val, e["id"]))

    visited: set[str] = set()
    potential: dict[str, float] = {}
    conflict_eids: set[str] = set()
    conflict_items: list[dict[str, Any]] = []

    def walk(seed: str, seed_voltage: float) -> None:
        """在仅含电压源的约束图上做 BFS，检查电位差自洽性。"""
        potential[seed] = seed_voltage
        visited.add(seed)
        queue = [seed]
        head = 0
        while head < len(queue):
            u = queue[head]
            head += 1
            for v, diff, eid in adj[u]:
                # 存储的 diff = V(u) - V(v)，故 V(v) = V(u) - diff
                expected = potential[u] - diff
                if v not in visited:
                    visited.add(v)
                    potential[v] = expected
                    queue.append(v)
                else:
                    tol = _V_ABS_TOL + _V_REL_TOL * max(
                        1.0, abs(potential[u]), abs(expected)
                    )
                    if abs(potential[v] - expected) > tol and eid not in conflict_eids:
                        conflict_eids.add(eid)
                        conflict_items.append(
                            {
                                "ref": eid,
                                "detail": (
                                    f"电压源 {eid}（{u}→{v}）要求的电位差与同组"
                                    "电压源已确定的电位不一致"
                                ),
                            }
                        )

    # 先从接地（0 V）遍历，保证与地相连的分量得到正确电位
    walk(ground, 0.0)
    for seed_node in nodes:
        if seed_node not in visited:
            walk(seed_node, 0.0)  # 浮置分量内部任取参考，仅检查自洽性

    if conflict_items:
        errors.append(
            {
                "code": "voltage_conflict",
                "message": "理想电压源之间存在互相矛盾的电位约束（例如不同电压的电压源并联）",
                "items": conflict_items,
            }
        )

    if errors:
        raise CircuitError(errors)

    # -------------------------------------------------------------- 组装 MNA --
    non_ground = [n for n in nodes if n != ground]
    index = {name: i for i, name in enumerate(non_ground)}
    v_sources = [e for e in elements if e["type"] == "V"]
    n = len(non_ground)
    k = len(v_sources)
    size = n + k

    A = np.zeros((size, size), dtype=np.float64)
    b = np.zeros(size, dtype=np.float64)

    def gi(name: str):
        return index.get(name)  # 接地节点返回 None（对应行/列直接消去）

    for e in elements:
        p, q, val = e["node_p"], e["node_n"], e["value"]
        ip, iq = gi(p), gi(q)
        if e["type"] == "R":
            g = 1.0 / val
            if ip is not None:
                A[ip, ip] += g
            if iq is not None:
                A[iq, iq] += g
            if ip is not None and iq is not None:
                A[ip, iq] -= g
                A[iq, ip] -= g
        elif e["type"] == "I":
            # 正电流 p -> n：p 节点注入为 -J，n 节点为 +J
            if ip is not None:
                b[ip] -= val
            if iq is not None:
                b[iq] += val
        # 电压源在下面统一盖印

    for j, e in enumerate(v_sources):
        row = n + j
        ip, iq = gi(e["node_p"]), gi(e["node_n"])
        # 支路电流 i 定义为 p -> n，KCL 在 p 端计入 +i
        if ip is not None:
            A[row, ip] = 1.0
            A[ip, row] = 1.0
        if iq is not None:
            A[row, iq] = -1.0
            A[iq, row] = -1.0
        b[row] = e["value"]

    warnings: list[str] = []
    node_voltage = {ground: 0.0}

    if size > 0:
        try:
            x = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            raise _group(
                "singular",
                "线性方程组奇异且无法通过结构诊断定位原因，请检查元件连接是否存在病态回路",
            )
        try:
            cond = float(np.linalg.cond(A))
            if math.isfinite(cond) and cond > _COND_WARN:
                warnings.append(
                    f"系数矩阵条件数约为 {cond:.3e}，网络接近奇异，数值结果可能不稳定"
                )
        except Exception:  # noqa: BLE001 - 条件数仅用于提示
            pass

        for name in non_ground:
            v = float(x[index[name]])
            node_voltage[name] = 0.0 if abs(v) < 1e-12 else v
        vs_current = {e["id"]: float(x[n + j]) for j, e in enumerate(v_sources)}
    else:
        vs_current = {}

    # --------------------------------------------- 支路电流与功率（关联参考方向）
    branches: list[dict[str, Any]] = []
    total_absorbed = 0.0
    total_abs = 0.0
    for e in elements:
        vp = node_voltage[e["node_p"]]
        vn = node_voltage[e["node_n"]]
        if e["type"] == "R":
            current = (vp - vn) / e["value"]
        elif e["type"] == "I":
            current = e["value"]
        else:
            current = vs_current[e["id"]]
        if abs(current) < 1e-12:
            current = 0.0
        power = (vp - vn) * current  # P>0 吸收，P<0 发出
        if abs(power) < 1e-15:
            power = 0.0
        total_absorbed += power
        total_abs += abs(power)
        branches.append(
            {
                "id": e["id"],
                "type": e["type"],
                "node_p": e["node_p"],
                "node_n": e["node_n"],
                "value": e["value"],
                "current": current,
                "power": power,
            }
        )

    error_abs = abs(total_absorbed)
    error_rel = error_abs / total_abs if total_abs > 0.0 else 0.0
    supplied = sum(-br["power"] for br in branches if br["power"] < 0)
    absorbed = sum(br["power"] for br in branches if br["power"] > 0)

    return {
        "ok": True,
        "ground": ground,
        "node_voltages": [{"id": name, "voltage": node_voltage[name]} for name in nodes],
        "branches": branches,
        "power_balance": {
            "supplied": supplied,
            "absorbed": absorbed,
            "sum": total_absorbed,
            "error_abs": error_abs,
            "error_rel": error_rel,
        },
        "warnings": warnings,
    }
