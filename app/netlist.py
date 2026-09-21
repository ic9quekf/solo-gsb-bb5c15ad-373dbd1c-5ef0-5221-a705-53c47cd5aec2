"""SPICE 网表互换（导入解析 / 规范导出）。

导入文本按行解析，语法约定：

- **空行**与首尾空白忽略；
- 以 ``*`` 开头的行是**整行注释**；
- ``;`` 起到行尾为**行尾注释**；
- 元件行固定四个字段：``名称 正端 负端 数值``；
- 元件名称首字母（大小写不敏感）给出类型，仅支持 ``R / I / V``；
- 数值接受科学计数法（``1e3``、``2.5e-2``）与工程倍率后缀
  ``p=1e-12``、``n=1e-9``、``u=1e-6``、``m=1e-3``（**毫**）、
  ``k=1e3``、``meg=1e6``、``g=1e9``；``m`` 一律按 1e-3 解析
  （SPICE 惯例中兆用 ``meg``，而非 ``M``）。

导入时**逐行**收集错误并标明行号（重复编号 / 字段数量 / 类型 /
数值 / 非法自环等），任何一个错误都会整单拒绝（由调用方保证不
改写当前电路）。解析成功后节点表**从元件端点推导**（按首次出现
排序），并要求端点中存在节点 ``0`` 作为接地。

导出按元件表顺序生成规范网表；当前接地不是 ``0`` 时把接地节点
改名为 ``0``，若节点表中另有名为 ``0`` 的节点则拒绝导出（无法
无冲突地改名）。导出文本再次导入可还原**等价**电路（同名节点、
同序元件、数值完全一致，仅接地编号被规范为 ``0``）。
"""

from __future__ import annotations

import math
import re
from typing import Any

from .solver import (
    MAX_ELEMENTS,
    MAX_NODES,
    _ID_MAX,
    CircuitError,
    parse_network,
)

GROUND_NODE = "0"

# 数值：数（支持科学计数法）+ 可选工程倍率后缀（大小写不敏感）。
# 注意交替顺序：meg 必须在 m 之前，否则会先匹配成“毫”；
# 兆的标准写法是 meg（M/m 永远是 1e-3 毫）。
_NUMBER_RE = re.compile(
    r"^(?P<num>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<suffix>[mM][eE][gG]|[pPnNuUmMkKgG])?$"
)
_SCALE = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,   # 毫（SPICE 中 M 不是兆，兆须写 meg）
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
}
_ELEMENT_TYPES = ("R", "I", "V")


def netlist_error(code: str, message: str, items: list[dict[str, Any]]) -> CircuitError:
    return CircuitError([{"code": code, "message": message, "items": items}])


def parse_value(token: str) -> float:
    """解析一个 SPICE 数值词法单元，失败时抛 :class:`ValueError`。"""
    m = _NUMBER_RE.match(token)
    if not m:
        raise ValueError(f"数值“{token}”无法识别（支持科学计数法与 p/n/u/m/k/meg/g 后缀）")
    try:
        value = float(m.group("num"))
    except ValueError as exc:  # 正则已约束，理论上不会到这里
        raise ValueError(f"数值“{token}”不是合法数字") from exc
    suffix = (m.group("suffix") or "").lower()
    if suffix:
        value *= _SCALE[suffix]
    return value


def _line_ref(lineno: int, name: str = "") -> str:
    return f"第 {lineno} 行" + (f"（{name}）" if name else "")


def parse_netlist(text: str) -> dict[str, Any]:
    """把 SPICE 网表文本解析为本应用的网络 JSON（不含求解）。

    成功返回 ``{"ground", "nodes", "elements", "line_count"}``；
    任何一行有问题都抛出 :class:`CircuitError`（``netlist_import``），
    ``items[]`` 每条带 ``line`` 行号、``ref`` 与 ``detail``。
    """
    if not isinstance(text, str):
        raise netlist_error(
            "netlist_import",
            "SPICE 网表导入失败",
            [{"line": None, "ref": "-", "detail": "网表文本必须是字符串"}],
        )

    problems: list[dict[str, Any]] = []
    parsed: list[dict[str, Any]] = []  # 仅收集“本行无错”的元件
    seen_names: set[str] = set()      # 任意四字段行上出现过的编号（重复判定）
    node_order: list[str] = []
    node_seen: set[str] = set()
    any_element_line = False

    def register_node(name: str) -> None:
        if name not in node_seen:
            node_seen.add(name)
            node_order.append(name)

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        # 行尾注释（; 起）；整行注释（* 起）与空行在去注释后自然为空
        line = raw_line.split(";", 1)[0].strip()
        if not line:
            continue
        if raw_line.lstrip().startswith("*"):
            continue
        any_element_line = True

        tokens = line.split()
        if len(tokens) != 4:
            problems.append(
                {
                    "line": lineno,
                    "ref": _line_ref(lineno),
                    "detail": (
                        f"字段数量为 {len(tokens)}，应为 4（名称 正端 负端 数值）："
                        f"“{line}”"
                    ),
                }
            )
            continue

        name, node_p, node_n, value_tok = tokens
        etype = name[0].upper()
        row_problems: list[str] = []

        # 编号：首字母决定类型；重复编号按“名字在网表中是否已出现”逐行点名
        # （即使前一同名行本身也有错，仍然计重复，避免漏报真正的重号）
        if etype not in _ELEMENT_TYPES:
            row_problems.append(
                f"元件类型“{name[0]}”不受支持，名称须以 R / I / V 开头"
            )
        if len(name) > _ID_MAX:
            row_problems.append(f"元件编号超过 {_ID_MAX} 个字符")
        if name in seen_names:
            row_problems.append(f"元件编号“{name}”重复")
        seen_names.add(name)

        # 数值
        value: float | None = None
        try:
            value = parse_value(value_tok)
        except ValueError as exc:
            row_problems.append(str(exc))
        else:
            if not math.isfinite(value):
                row_problems.append("数值必须为有限实数")
            elif etype == "R" and value <= 0.0:
                row_problems.append("电阻值必须大于 0")

        # 端点：自环逐行检查；端点长度限制
        for endpoint, where in ((node_p, "正端"), (node_n, "负端")):
            if len(endpoint) > _ID_MAX:
                row_problems.append(f"{where}节点名“{endpoint}”超过 {_ID_MAX} 个字符")
        if node_p == node_n:
            row_problems.append(
                f"正端与负端同为“{node_p}”，元件不能构成自环"
            )

        if row_problems:
            for detail in row_problems:
                problems.append({"line": lineno, "ref": _line_ref(lineno, name), "detail": detail})
            continue

        parsed.append(
            {
                "id": name,
                "type": etype,
                "node_p": node_p,
                "node_n": node_n,
                "value": float(value),  # type: ignore[arg-type]
            }
        )
        register_node(node_p)
        register_node(node_n)

    # --------------------------------------------------------------- 全局限制
    if len(parsed) > MAX_ELEMENTS:
        problems.append(
            {
                "line": None,
                "ref": "-",
                "detail": f"元件数量为 {len(parsed)}，超过上限 {MAX_ELEMENTS}",
            }
        )
    if len(node_order) > MAX_NODES:
        problems.append(
            {
                "line": None,
                "ref": "-",
                "detail": f"从端点推导出 {len(node_order)} 个节点，超过上限 {MAX_NODES}",
            }
        )
    if not any_element_line and not problems:
        problems.append(
            {
                "line": None,
                "ref": "-",
                "detail": "网表中没有任何元件行（空行与 * 注释已忽略）",
            }
        )
    if GROUND_NODE not in node_seen:
        problems.append(
            {
                "line": None,
                "ref": GROUND_NODE,
                "detail": "端点中未出现接地节点 0：SPICE 网表必须以节点 0 作为接地",
            }
        )

    if problems:
        raise netlist_error("netlist_import", "SPICE 网表导入失败", problems)

    # 接地节点 0 排在节点表最前，其余按首次出现顺序
    nodes = [GROUND_NODE] + [n for n in node_order if n != GROUND_NODE]
    return {
        "ground": GROUND_NODE,
        "nodes": nodes,
        "elements": parsed,
        "line_count": len(text.splitlines()),
    }


def _format_value(value: float) -> str:
    """规范数值格式：repr 保证最短且可精确还原；-0 归一为 0。"""
    if value == 0.0:
        return "0"
    return repr(float(value))


def export_netlist(payload: dict[str, Any]) -> dict[str, Any]:
    """把当前网络 JSON 导出为规范 SPICE 网表文本。

    复用 :func:`parse_network` 的全部输入 / 接地校验；当前接地不是
    ``0`` 时将其改名为 ``0``，若节点表中已另有节点 ``0`` 则抛出
    ``ground_conflict`` 拒绝导出。返回
    ``{"netlist", "ground", "renamed_from"}``。
    """
    nodes, elements, ground = parse_network(payload)

    renamed_from: str | None = None
    node_map: dict[str, str] = {}
    if ground != GROUND_NODE:
        if GROUND_NODE in set(nodes):
            raise netlist_error(
                "ground_conflict",
                "无法导出 SPICE 网表：接地节点改名冲突",
                [
                    {
                        "ref": GROUND_NODE,
                        "detail": (
                            f"当前接地节点为“{ground}”，导出时需改名为 0，"
                            f"但节点表中已存在名为 0 的非接地节点；"
                            "请先把节点 0 改名或改选接地，再导出"
                        ),
                    }
                ],
            )
        renamed_from = ground
        node_map[ground] = GROUND_NODE

    lines = [
        "* SPICE netlist exported by 直流网络校核器",
        "* 每行：名称 正端 负端 数值；节点 0 为接地",
    ]
    for e in elements:
        p = node_map.get(e["node_p"], e["node_p"])
        n = node_map.get(e["node_n"], e["node_n"])
        lines.append(f"{e['id']} {p} {n} {_format_value(e['value'])}")

    return {
        "netlist": "\n".join(lines) + "\n",
        "ground": GROUND_NODE,
        "renamed_from": renamed_from,
    }
