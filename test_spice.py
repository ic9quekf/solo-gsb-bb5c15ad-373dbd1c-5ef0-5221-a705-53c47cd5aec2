"""SPICE 网表互换冒烟测试（开发用，不打进镜像）：python3 test_spice.py"""
import math
import sys

from app.solver import CircuitError, solve
from app.spice import build_netlist, parse_netlist, parse_value

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def parse_err(text):
    try:
        parse_netlist(text)
        return None
    except CircuitError as e:
        return e.errors


def export_err(payload):
    try:
        build_netlist(payload)
        return None
    except CircuitError as e:
        return e.errors


# ---------- 1. 数值解析：科学计数法 + 工程后缀（m=1e-3） ----------
check("数值 10 == 10", math.isclose(parse_value("10"), 10.0))
check("科学计数法 1.5e3", math.isclose(parse_value("1.5e3"), 1500.0))
check("科学计数法 -2.2E-4", math.isclose(parse_value("-2.2E-4"), -2.2e-4))
check("后缀 p=1e-12", math.isclose(parse_value("1p"), 1e-12))
check("后缀 n=1e-9", math.isclose(parse_value("10n"), 1e-8))
check("后缀 u=1e-6", math.isclose(parse_value("2.2u"), 2.2e-6))
check("后缀 m=1e-3（毫，非 mega）", math.isclose(parse_value("1m"), 1e-3), str(parse_value("1m")))
check("后缀 M 大写仍是 1e-3", math.isclose(parse_value("1M"), 1e-3))
check("后缀 k=1e3", math.isclose(parse_value("10k"), 1e4))
check("后缀 meg=1e6", math.isclose(parse_value("3.3meg"), 3.3e6))
check("后缀 MEG 大写", math.isclose(parse_value("3.3MEG"), 3.3e6))
check("后缀 g=1e9", math.isclose(parse_value("1g"), 1e9))
check("非法 1e3k（科学计数与后缀叠加）", parse_value("1e3k") is None)
check("非法 1x（未知后缀）", parse_value("1x") is None)
check("非法 abc", parse_value("abc") is None)
check("非法 inf", parse_value("inf") is None)
check("非法 1meg5", parse_value("1meg5") is None)
check("裸点小数 .5", math.isclose(parse_value(".5"), 0.5))

# ---------- 2. 正常导入：空行 / * 注释 / ; 行尾注释 ----------
text = "\n".join([
    "* 整行注释",
    "  * 缩进的整行注释",
    "",
    "V1 1 0 10   ; 电源",
    "R1 1 2 10k ; 上管",
    "R2 2 0 20",
    "   ",
    "I1 2 0 1m",
])
r = parse_netlist(text)
check("导入成功", r["ok"] and r["ground"] == "0")
check("节点按首次出现顺序推导", r["nodes"] == ["1", "0", "2"], str(r["nodes"]))
check("元件数量 4", len(r["elements"]) == 4)
check("10k 解析为 10000", r["elements"][1]["value"] == 10000.0)
check("1m 解析为 0.001", r["elements"][3]["value"] == 0.001)
check("空行计数 2", r["counts"]["blank"] == 2, str(r["counts"]))
check("注释行计数 2", r["counts"]["comment"] == 2, str(r["counts"]))

# 导入结果必须能被 solver 直接求解
sol = solve({"ground": r["ground"], "nodes": r["nodes"], "elements": r["elements"]})
check("导入电路可求解", sol["ok"])

# 末尾换行不产生假行号
r2 = parse_netlist("V1 1 0 10\nR1 1 0 1k\n\n")
check("末尾换行不产生假行", r2["counts"]["lines"] == 3, str(r2["counts"]))

# 仅注释 / 空白
check("仅注释 → spice_empty",
      parse_err("* only comment\n;\n  ") is not None)

# ---------- 3. 逐行错误收集且带行号 ----------
bad = "\n".join([
    "V1 1 0 10",
    "R1 1 2",                 # 字段数量错误
    "X1 1 0 5",               # 类型错误
    "R2 1 0 abc",             # 数值错误
    "R3 1 1 10",              # 自环
    "V1 2 0 5",               # 重复编号
    "R4 1 0 -5",              # 电阻非正
    "R5 0 0 10k",             # 自环
])
errs = parse_err(bad)
check("坏网表整体拒绝", errs is not None and errs[0]["code"] == "spice_import")
items = errs[0]["items"]
line_map = {it["line"]: it["detail"] for it in items if it.get("line")}
check("第 2 行字段数错误", 2 in line_map and "4 个字段" in line_map[2], str(items))
check("第 3 行类型错误", 3 in line_map and "R / I / V" in line_map[3])
check("第 4 行数值错误", 4 in line_map and "数值" in line_map[4])
check("第 5 行自环", 5 in line_map and "自环" in line_map[5])
check("第 6 行重复编号", 6 in line_map and "重复" in line_map[6])
check("第 7 行电阻非正", 7 in line_map and "大于 0" in line_map[7])
check("第 8 行自环", 8 in line_map)
check("一次性收集全部 7 个错误", len(items) == 7, f"{len(items)}: {items}")

# 无节点 0
errs = parse_err("V1 1 2 10\nR1 1 2 1k")
check("缺节点 0 → spice_no_ground",
      errs is not None and [g["code"] for g in errs] == ["spice_no_ground"])

# 坏网表不应产出任何结果对象（调用方据此不改写电路）
check("坏网表无返回值", parse_err(bad) is not None)

# ---------- 4. 导出：基本往返 ----------
payload = {
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10000},
        {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20.5},
        {"id": "I1", "type": "I", "node_p": "2", "node_n": "0", "value": 0.001},
    ],
}
ex = build_netlist(payload)
lines = ex["netlist"].strip().splitlines()
check("导出首行为 * 注释", lines[0].startswith("*"))
check("导出行 V1", lines[1] == "V1 1 0 10", lines[1])
check("导出行 R1 规范数值", lines[2] == "R1 1 2 10000", lines[2])
check("导出行 R2 小数", lines[3] == "R2 2 0 20.5", lines[3])
check("导出 4 个元件", ex["element_count"] == 4)

# 再导入应还原等价电路（节点顺序 / 元件 / 数值一致）
back = parse_netlist(ex["netlist"])
check("往返节点一致", back["nodes"] == ["1", "0", "2"], str(back["nodes"]))
check("往返元件数一致", len(back["elements"]) == 4)
for a, b in zip(payload["elements"], back["elements"]):
    check(f"往返元件 {a['id']} 等价",
          a["id"] == b["id"] and a["type"] == b["type"]
          and a["node_p"] == b["node_p"] and a["node_n"] == b["node_n"]
          and math.isclose(a["value"], b["value"], rel_tol=1e-12))
s1 = solve(payload)
s2 = solve({"ground": "0", "nodes": back["nodes"], "elements": back["elements"]})
v1 = {x["id"]: x["voltage"] for x in s1["node_voltages"]}
v2 = {x["id"]: x["voltage"] for x in s2["node_voltages"]}
check("往返求解节点电压一致", all(math.isclose(v1[k], v2[k], abs_tol=1e-10) for k in v1),
      f"{v1} vs {v2}")

# 科学计数法数值导出后仍可导入
ex2 = build_netlist({
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [{"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 1.5e-9}],
})
back2 = parse_netlist(ex2["netlist"])
check("极小值往返", math.isclose(back2["elements"][0]["value"], 1.5e-9, rel_tol=1e-12),
      ex2["netlist"])

# ---------- 5. 导出：接地非 0 的映射与冲突 ----------
payload_g = {
    "ground": "gnd",
    "nodes": ["gnd", "a"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "a", "node_n": "gnd", "value": 5},
        {"id": "R1", "type": "R", "node_p": "a", "node_n": "gnd", "value": 100},
    ],
}
exg = build_netlist(payload_g)
check("接地映射返回重命名信息", exg["ground_renamed_from"] == "gnd", str(exg))
check("网表中接地写作 0", "V1 a 0 5" in exg["netlist"], exg["netlist"])
bg = parse_netlist(exg["netlist"])
check("映射后再导入接地为 0", bg["ground"] == "0" and bg["nodes"] == ["a", "0"], str(bg["nodes"]))
sg = solve({"ground": "gnd", "nodes": ["gnd", "a"],
            "elements": payload_g["elements"]})
sbg = solve({"ground": "0", "nodes": bg["nodes"], "elements": bg["elements"]})
check("接地映射前后电压等价",
      math.isclose(sg["node_voltages"][1]["voltage"],
                   next(x["voltage"] for x in sbg["node_voltages"] if x["id"] == "a"),
                   abs_tol=1e-10))

# 另有节点 0：拒绝并说明冲突
payload_conflict = {
    "ground": "gnd",
    "nodes": ["gnd", "0", "a"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "a", "node_n": "gnd", "value": 5},
        {"id": "R1", "type": "R", "node_p": "a", "node_n": "0", "value": 100},
        {"id": "R2", "type": "R", "node_p": "gnd", "node_n": "0", "value": 100},
    ],
}
errs = export_err(payload_conflict)
check("接地冲突 → spice_ground_conflict",
      errs is not None and errs[0]["code"] == "spice_ground_conflict", str(errs))

# 接地已是 0 且另有节点不冲突
ex0 = build_netlist({
    "ground": "0",
    "nodes": ["0", "a"],
    "elements": [{"id": "R1", "type": "R", "node_p": "a", "node_n": "0", "value": 10}],
})
check("接地为 0 不重命名", ex0["ground_renamed_from"] is None)

# ---------- 6. 导出端结构校验 ----------
check("缺接地拒绝", export_err({
    "ground": "", "nodes": ["0", "1"],
    "elements": [{"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}],
}) is not None)
check("自环拒绝导出", export_err({
    "ground": "0", "nodes": ["0", "1"],
    "elements": [{"id": "R1", "type": "R", "node_p": "1", "node_n": "1", "value": 10}],
}) is not None)
check("元件名与类型首字母不符拒绝", export_err({
    "ground": "0", "nodes": ["0", "1"],
    "elements": [{"id": "X1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}],
}) is not None)
check("接地孤立（未被引用）拒绝", export_err({
    "ground": "gnd", "nodes": ["gnd", "0", "1"],
    "elements": [{"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
                 {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}],
}) is not None)

# 孤立非接地节点：省略并在响应中注明
ex_iso = build_netlist({
    "ground": "0",
    "nodes": ["0", "1", "9"],
    "elements": [{"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}],
})
check("孤立节点被注明省略", ex_iso["omitted_isolated_nodes"] == ["9"],
      str(ex_iso["omitted_isolated_nodes"]))
check("孤立节点不进网表", "9" not in ex_iso["netlist"])

# ---------- 7. 复杂往返：含全部后缀 / 负值源 / 多节点 ----------
complex_text = "\n".join([
    "* complex",
    "V1 1 0 12",
    "R1 1 2 4k7" if False else "R1 1 2 4.7k",
    "R2 2 0 100n ;",
    "I1 3 2 -2.2u",
    "V2 3 0 1meg",
    "R3 2 3 0.5",
])
# 100n 作为电阻虽小但为正，可解析；1meg 电压源 = 1e6 V
rc = parse_netlist(complex_text)
exc2 = build_netlist({"ground": "0", "nodes": rc["nodes"], "elements": rc["elements"]})
rc2 = parse_netlist(exc2["netlist"])
check("复杂网表往返元件数", len(rc2["elements"]) == len(rc["elements"]))
for a, b in zip(rc["elements"], rc2["elements"]):
    check(f"复杂往返 {a['id']}",
          a["type"] == b["type"] and a["node_p"] == b["node_p"]
          and a["node_n"] == b["node_n"] and math.isclose(a["value"], b["value"], rel_tol=1e-12),
          f"{a} vs {b}")

print()
if failures:
    print(f"{len(failures)} 项失败：{failures}")
    sys.exit(1)
print("全部 SPICE 测试通过。")
