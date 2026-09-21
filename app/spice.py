"""SPICE 网表互换：文本网表 ↔ 应用内部的网络 JSON。

支持的网表语法（每行一条记录）：

* **空行**：直接忽略；
* **整行注释**：第一个非空白字符为 ``*`` 的行整行忽略；
* **行尾注释**：``;`` 之后的内容一律忽略（剥离后只剩空白也算空行）；
* **元件行**：恰好 4 个空白分隔字段——
  ``名称 正端节点 负端节点 数值``。

元件类型由名称首字母决定（大小写不敏感），仅支持 ``R / I / V``。
数值接受科学计数法（``1.5e3`` / ``-2.2E-4``）以及工程后缀
``p / n / u / m / k / meg / g``（大小写不敏感），倍率如下：

=========  ==========
后缀        倍率
p          1e-12
n          1e-9
u          1e-6
m          1e-3   （注意：m 按毫解析，**不**是 SPICE 传统的 mega）
k          1e3
meg        1e6
g          1e9
=========  ==========

导入时逐行收集错误（重复编号 / 字段数量 / 类型 / 数值 / 非法自环等），
全部带物理行号；只要存在任一错误就抛出 :class:`CircuitError`，
由调用方保证**不**用半截结果改写当前电路。全部通过后再从元件端点
按首次出现顺序推导节点表，并要求端点中存在节点 ``0`` 作为接地。

导出按元件表顺序生成规范网表：当前接地不是 ``0`` 时，把接地节点
（及其在端点中的引用）重命名为 ``0``；若网络中另有节点名为 ``0``
则无法映射，拒绝导出并说明冲突。导出文本可再次导入并还原等价电路。
"""

from __future__ import annotations

import math
import re
from typing import Any

from .solver import MAX_ELEMENTS, MAX_NODES, _ID_MAX, CircuitError

# 工程后缀（大小写不敏感）→ 倍率。m 明确按 1e-3（毫）解析。
SUFFIX_FACTORS: dict[str, float] = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
}

# 裸数值（含科学计数法）或“数值 + 单个工程后缀”。
# 科学计数法与工程后缀互斥：1e3k 这类写法不被接受。
_VALUE_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
    r"|^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(p|n|u|m|k|meg|g)$",
    re.IGNORECASE,
)


def parse_value(token: str) -> float | None:
    """把 SPICE 数值记号解析为 float；无法识别时返回 ``None``。

    成功解析出非有限数（``inf`` / ``nan``）同样视为非法、返回 ``None``。
    """
    m = _VALUE_RE.match(token)
    if m is None:
        return None
    suffix = m.group(1)
    if suffix is None:
        try:
            value = float(token)
        except ValueError:
            return None
    else:
        number_part = token[: -len(suffix)]
        try:
            value = float(number_part) * SUFFIX_FACTORS[suffix.lower()]
        except ValueError:
            return None
    if not math.isfinite(value):
        return None
    return value


def _parse_error(items: list[dict[str, Any]]) -> CircuitError:
    return CircuitError(
        [{"code": "spice_import", "message": "SPICE 网表导入失败：存在无法解析的行", "items": items}]
    )


def parse_netlist(text: Any) -> dict[str, Any]:
    """解析 SPICE 网表文本，返回可直接装入前端状态的网络字典。

    成功返回::

        {"ok": True, "ground": "0", "nodes": [...], "elements": [...],
         "counts": {"lines": N, "elements": M, "blank": b, "comment": c}}

    任何一行出错都会收集（带 1 起始物理行号），最终抛出
    :class:`CircuitError`；节点推导与接地校验在所有行通过后才进行。
    """
    if not isinstance(text, str):
        raise _parse_error([{"ref": "-", "line": None,
                             "detail": "网表内容必须是文本字符串"}])

    items: list[dict[str, Any]] = []
    elements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    node_order: list[str] = []
    node_seen: set[str] = set()

    n_blank = 0
    n_comment = 0
    # splitlines() 天然处理行尾换行："a\nb\n" → ['a','b']（无幻影空行），
    # "a\nb\n\n" → ['a','b','']（第三个空行真实存在），行号与编辑器一致。
    lines = text.splitlines()

    def add_node(name: str) -> None:
        if name not in node_seen:
            node_seen.add(name)
            node_order.append(name)

    for line_no, raw_line in enumerate(lines, start=1):
        ref = f"第 {line_no} 行"

        # 整行注释：第一个非空白字符为 *
        if raw_line.lstrip().startswith("*"):
            n_comment += 1
            continue

        # 行尾注释：; 之后全部剥离
        line = raw_line.split(";", 1)[0].strip()
        if not line:
            n_blank += 1
            continue

        fields = line.split()
        if len(fields) != 4:
            items.append({
                "ref": ref, "line": line_no,
                "detail": f"元件行应有 4 个字段（名称 正端 负端 数值），"
                          f"实际为 {len(fields)} 个：“{line}”",
            })
            continue

        name, np_node, nn_node, value_token = fields
        etype = name[:1].upper()
        row_ok = True

        if not name:  # 理论不可达：split 已保证非空，保留防御
            items.append({"ref": ref, "line": line_no, "detail": "元件名称为空"})
            row_ok = False
        elif name in seen_ids:
            items.append({"ref": name, "line": line_no, "detail": f"元件编号“{name}”重复"})
            row_ok = False
        elif len(name) > _ID_MAX:
            items.append({"ref": name, "line": line_no,
                         "detail": f"元件编号“{name}”超过 {_ID_MAX} 个字符"})
            row_ok = False

        if etype not in ("R", "I", "V"):
            items.append({
                "ref": name or ref, "line": line_no,
                "detail": f"未知元件类型“{etype}”（名称“{name}”首字母），仅支持 R / I / V",
            })
            row_ok = False

        value = parse_value(value_token)
        if value is None:
            items.append({
                "ref": name or ref, "line": line_no,
                "detail": (f"数值“{value_token}”无法识别：应为数字（可带科学计数法）"
                           "或 p/n/u/m/k/meg/g 工程后缀，且必须为有限实数"),
            })
            row_ok = False
        elif etype == "R" and value <= 0.0:
            items.append({
                "ref": name or ref, "line": line_no,
                "detail": f"电阻值必须大于 0（当前：{value_token} → {value:g} Ω）",
            })
            row_ok = False

        for endpoint, which in ((np_node, "正端"), (nn_node, "负端")):
            if len(endpoint) > _ID_MAX:
                items.append({
                    "ref": name or ref, "line": line_no,
                    "detail": f"{which}节点名“{endpoint}”超过 {_ID_MAX} 个字符",
                })
                row_ok = False

        if np_node and nn_node and np_node == nn_node:
            items.append({
                "ref": name or ref, "line": line_no,
                "detail": f"元件两端不能接在同一节点“{np_node}”上（非法自环）",
            })
            row_ok = False

        if not row_ok:
            continue

        seen_ids.add(name)
        add_node(np_node)
        add_node(nn_node)
        elements.append({
            "id": name,
            "type": etype,
            "node_p": np_node,
            "node_n": nn_node,
            "value": value,
        })

    if len(elements) > MAX_ELEMENTS:
        items.append({
            "ref": "-", "line": None,
            "detail": f"元件数量为 {len(elements)}，超过上限 {MAX_ELEMENTS}",
        })
    if len(node_order) > MAX_NODES:
        items.append({
            "ref": "-", "line": None,
            "detail": f"由端点推导出的节点数量为 {len(node_order)}，超过上限 {MAX_NODES}",
        })

    if items:
        raise _parse_error(items)

    if not elements:
        raise CircuitError([{
            "code": "spice_empty",
            "message": "SPICE 网表为空：未解析到任何元件行",
            "items": [{"ref": "-", "line": None,
                       "detail": "请至少提供一条 R / I / V 元件行（空行与 * 注释行会被忽略）"}],
        }])

    # 节点 0 必须存在并作为接地
    if "0" not in node_seen:
        raise CircuitError([{
            "code": "spice_no_ground",
            "message": "网表中不存在节点 0：SPICE 网表要求节点 0 作为接地",
            "items": [{
                "ref": "-", "line": None,
                "detail": "请让至少一个元件的端点接到节点 0；当前端点节点为 "
                          + "、".join(node_order),
            }],
        }])

    return {
        "ok": True,
        "ground": "0",
        "nodes": list(node_order),
        "elements": elements,
        "counts": {
            "lines": len(lines),
            "elements": len(elements),
            "nodes": len(node_order),
            "blank": n_blank,
            "comment": n_comment,
        },
    }


def _export_error(items: list[dict[str, str]]) -> CircuitError:
    return CircuitError(
        [{"code": "spice_export", "message": "SPICE 网表导出失败", "items": items}]
    )


def _format_value(value: float) -> str:
    """规范数值格式：往返一致、尽量短；负零归零，整数去掉 ``.0``。"""
    if value == 0.0:
        return "0"
    text = repr(float(value))
    if text.endswith(".0"):
        text = text[:-2]
    return text


def build_netlist(payload: dict[str, Any]) -> dict[str, Any]:
    """把内部网络 JSON 导出为规范 SPICE 网表文本。

    严格校验网络结构（元件字段 / 端点归属 / 自环 / 电阻正值 / 接地存在），
    并保证导出结果可被 :func:`parse_netlist` 重新导入为等价电路：

    * 接地已是 ``0``：直接输出；
    * 接地不是 ``0`` 且不存在名为 ``0`` 的节点：接地在网表中重命名为 ``0``；
    * 接地不是 ``0`` 但网络中另有节点 ``0``：冲突，拒绝导出。

    端点从未引用的孤立节点不会出现在网表中（SPICE 节点由连接推导）。
    """
    problems: list[dict[str, str]] = []

    if not isinstance(payload, dict):
        raise _export_error([{"ref": "-", "detail": "请求体应为对象"}])

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        problems.append({"ref": "-", "detail": "节点表缺失或格式错误"})
        raw_nodes = []
    raw_elements = payload.get("elements")
    if not isinstance(raw_elements, list):
        problems.append({"ref": "-", "detail": "元件表缺失或格式错误"})
        raw_elements = []

    nodes: list[str] = []
    node_seen: set[str] = set()
    for i, raw in enumerate(raw_nodes):
        name = str(raw).strip() if raw is not None else ""
        if not name:
            problems.append({"ref": f"节点行 {i + 1}", "detail": "节点编号为空"})
        elif name in node_seen:
            problems.append({"ref": name, "detail": "节点编号重复"})
        else:
            node_seen.add(name)
            nodes.append(name)

    ground = str(payload.get("ground") or "").strip()
    if not ground:
        problems.append({"ref": "-", "detail": "未指定接地节点，无法导出网表"})
    elif not node_seen:
        problems.append({"ref": ground, "detail": f"接地节点“{ground}”不在节点表中"})
    elif ground not in node_seen:
        problems.append({"ref": ground, "detail": f"接地节点“{ground}”不在节点表中"})

    if len(raw_elements) > MAX_ELEMENTS:
        problems.append({"ref": "-", "detail": f"元件数量超过上限 {MAX_ELEMENTS}"})

    elements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    used_nodes: set[str] = set()
    for i, raw in enumerate(raw_elements):
        row_ref = f"元件行 {i + 1}"
        if not isinstance(raw, dict):
            problems.append({"ref": row_ref, "detail": "元件数据格式错误"})
            continue
        eid = str(raw.get("id") or "").strip()
        etype = str(raw.get("type") or "").strip().upper()
        np_node = str(raw.get("node_p") or "").strip()
        nn_node = str(raw.get("node_n") or "").strip()
        ref = eid or row_ref
        row_ok = True

        if not eid:
            problems.append({"ref": row_ref, "detail": "元件编号为空"})
            row_ok = False
        elif eid in seen_ids:
            problems.append({"ref": eid, "detail": "元件编号重复"})
            row_ok = False
        if etype not in ("R", "I", "V"):
            problems.append({"ref": ref, "detail": f"未知元件类型“{etype}”，应为 R / I / V"})
            row_ok = False

        if isinstance(raw.get("value"), bool) or not isinstance(raw.get("value"), (int, float)):
            problems.append({"ref": ref, "detail": f"参数值“{raw.get('value')}”不是数字"})
            row_ok = False
            value = 0.0
        else:
            value = float(raw["value"])
            if not math.isfinite(value):
                problems.append({"ref": ref, "detail": "参数值必须为有限实数"})
                row_ok = False
            elif etype == "R" and value <= 0.0:
                problems.append({"ref": ref, "detail": "电阻值必须大于 0"})
                row_ok = False

        if not np_node or np_node not in node_seen:
            problems.append({"ref": ref, "detail": f"正端节点“{np_node}”不在节点表中"})
            row_ok = False
        if not nn_node or nn_node not in node_seen:
            problems.append({"ref": ref, "detail": f"负端节点“{nn_node}”不在节点表中"})
            row_ok = False
        if np_node and nn_node and np_node == nn_node:
            problems.append({"ref": ref, "detail": "元件两端不能接在同一节点上（非法自环）"})
            row_ok = False

        # 网表元件名必须以类型字母开头，否则再导入时类型会变
        if eid and eid[:1].upper() != etype:
            problems.append({
                "ref": eid,
                "detail": f"元件编号“{eid}”必须以类型字母 {etype} 开头，"
                          "否则导出的网表无法往返还原",
            })
            row_ok = False

        if row_ok:
            seen_ids.add(eid)
            used_nodes.add(np_node)
            used_nodes.add(nn_node)
            elements.append({
                "id": eid, "type": etype, "node_p": np_node,
                "node_n": nn_node, "value": value,
            })

    # 接地必须实际参与连接，否则再导入后节点 0 会消失（无法还原接地）
    if ground and node_seen and ground in node_seen and ground not in used_nodes:
        problems.append({
            "ref": ground,
            "detail": "接地节点未被任何元件端点引用：SPICE 节点由端点推导，"
                       "导出后再导入将丢失接地，请先让接地节点参与连接",
        })

    if problems:
        raise _export_error(problems)

    # ------------------------------------------------------- 接地 → 0 的映射
    rename: dict[str, str] = {}
    if ground != "0":
        if "0" in node_seen:
            raise CircuitError([{
                "code": "spice_ground_conflict",
                "message": (
                    f"接地冲突：当前接地节点为“{ground}”，导出时需映射为节点 0，"
                    "但网络中已存在另一个名为“0”的节点，映射后二者将合并"
                ),
                "items": [{
                    "ref": "0",
                    "detail": "请先把现有节点 0 改名（或直接把接地指定为节点 0）后再导出",
                }],
            }])
        rename[ground] = "0"

    def mapped(name: str) -> str:
        return rename.get(name, name)

    body_lines = [
        "* 直流网络校核器导出的 SPICE 网表（R/I/V：名称 正端 负端 数值；节点 0 为接地）",
    ]
    for e in elements:
        body_lines.append(
            f"{e['id']} {mapped(e['node_p'])} {mapped(e['node_n'])} {_format_value(e['value'])}"
        )
    netlist = "\n".join(body_lines) + "\n"

    isolated = [n for n in nodes if n not in used_nodes]
    return {
        "ok": True,
        "netlist": netlist,
        "ground": "0",
        "ground_renamed_from": ground if ground != "0" else None,
        "renamed_nodes": [{"from": k, "to": v} for k, v in rename.items()],
        "element_count": len(elements),
        "omitted_isolated_nodes": isolated,
    }
