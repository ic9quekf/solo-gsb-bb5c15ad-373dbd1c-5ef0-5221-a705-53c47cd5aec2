"""SPICE 网表互换冒烟测试（开发用，不打进镜像）：python3 test_netlist.py"""
import sys

from app.netlist import export_netlist, parse_netlist, parse_value
from app.solver import CircuitError

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def parse_error(text):
    try:
        parse_netlist(text)
        return None
    except CircuitError as e:
        return e.errors[0]


# ---------- 1. 数值解析：科学计数法 + 工程后缀 ----------
check("科学计数法 1e3", parse_value("1e3") == 1000.0)
check("科学计数法 -2.5E-2", parse_value("-2.5E-2") == -0.025)
check("小数 .5", parse_value(".5") == 0.5)
check("后缀 p", abs(parse_value("3.3p") - 3.3e-12) < 1e-24)
check("后缀 n", abs(parse_value("100n") - 1e-7) < 1e-20)
check("后缀 u", abs(parse_value("10u") - 1e-5) < 1e-18)
check("后缀 m 必须按 1e-3", parse_value("2m") == 2e-3)
check("后缀 M 仍为 1e-3（兆用 meg）", parse_value("2M") == 2e-3)
check("后缀 k", parse_value("1.5k") == 1500.0)
check("后缀 meg 为 1e6", parse_value("10meg") == 1e7)
check("后缀 MEG 大小写不敏感", parse_value("10MEG") == 1e7)
check("后缀 g 为 1e9", parse_value("1g") == 1e9)
check("无后缀整数", parse_value("10") == 10.0)

for bad in ["abc", "10x", "1mega", "4n7", "1.2.3", "10pF", "meg", ""]:
    try:
        parse_value(bad)
        check(f"非法数值被拒：{bad!r}", False)
    except ValueError:
        check(f"非法数值被拒：{bad!r}", True)

# ---------- 2. 注释 / 空行 / 行尾注释 ----------
r = parse_netlist("* 整行注释\nV1 1 0 10 ; 行尾\n\n  ; 只有注释\nR1 1 0 10k\r\n")
check("空行与注释被忽略", len(r["elements"]) == 2, str(r["elements"]))
check("行尾注释值正确", r["elements"][0]["value"] == 10.0)
check("CRLF 可解析", r["elements"][1]["value"] == 10000.0)

# ---------- 3. 端点推导节点 + 0 必须存在 ----------
r = parse_netlist("R1 a b 1k\nR2 b 0 2k\nV1 a 0 5\n")
check("接地推导为 0", r["ground"] == "0")
check("节点 0 排最前，其余按首次出现", r["nodes"] == ["0", "a", "b"], str(r["nodes"]))

err = parse_error("R1 a b 1k\n")
check("缺节点 0 被拒", err is not None and "节点 0" in err["items"][0]["detail"])

err = parse_error("* 只有注释\n\n")
check("空网表被拒", err is not None and err["code"] == "netlist_import")

# ---------- 4. 逐行错误收集并标行号，类型 / 字段数 / 数值 / 自环 / 重复 ----------
bad = """* c

V1 1 0 10
R1 1 1 5
X1 1 0 3
R2 1 0 abc
R3 1
V1 2 1 1
"""
err = parse_error(bad)
lines = {it["line"] for it in err["items"]}
# 4 自环 / 5 类型 / 6 数值 / 7 字段数 / 8 重复编号（行 3 的 V1 合法）
check("逐行错误标行号", lines == {4, 5, 6, 7, 8}, str(lines))
details = " ".join(it["detail"] for it in err["items"])
check("自环错误", "自环" in details)
check("字段数量错误", "字段数量" in details)
check("重复编号错误", "重复" in details)
check("错误码 netlist_import", err["code"] == "netlist_import")

err = parse_error("R1 1 0 0\nV1 1 0 5\n")
check("非正电阻按数值错误逐行报", err["items"][0]["line"] == 1 and "电阻" in err["items"][0]["detail"])

# ---------- 5. 任一行错误都不应返回可用电路（由调用方不改写状态）----------
check("有错时抛异常即不改写", parse_error(bad) is not None)

# ---------- 6. 导出：按表格顺序 + 接地改名 + 冲突拒绝 ----------
payload = {
    "ground": "gnd",
    "nodes": ["gnd", "a", "b"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "a", "node_n": "gnd", "value": 10},
        {"id": "R1", "type": "R", "node_p": "a", "node_n": "b", "value": 1500},
        {"id": "R2", "type": "R", "node_p": "b", "node_n": "gnd", "value": 0.002},
    ],
}
exp = export_netlist(payload)
check("接地改名标记", exp["renamed_from"] == "gnd" and exp["ground"] == "0")
text = exp["netlist"]
check("导出含接地 0", "V1 a 0 10" in text and "R2 b 0 0.002" in text, text)
check("导出按表格顺序", text.index("V1") < text.index("R1") < text.index("R2"))

# 导出 -> 再导入：等价电路（同节点、同元件、同值）
back = parse_netlist(text)
check("再导入接地为 0", back["ground"] == "0")
check("再导入节点", back["nodes"] == ["0", "a", "b"], str(back["nodes"]))
check(
    "再导入元件等价",
    [(e["id"], e["type"], e["node_p"], e["node_n"], e["value"]) for e in back["elements"]]
    == [("V1", "V", "a", "0", 10.0), ("R1", "R", "a", "b", 1500.0), ("R2", "R", "b", "0", 0.002)],
)

# 第二次导出幂等
exp2 = export_netlist(back)
check("重复导出幂等", exp2["netlist"] == text and exp2["renamed_from"] is None)

# 接地已经是 0：不改名
exp3 = export_netlist({
    "ground": "0", "nodes": ["0", "1"],
    "elements": [{"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}],
})
check("接地已是 0 不改名", exp3["renamed_from"] is None)

# 接地非 0 且另有节点 0：拒绝并说明冲突
conflict = {
    "ground": "gnd",
    "nodes": ["gnd", "0", "a"],
    "elements": [
        {"id": "R1", "type": "R", "node_p": "a", "node_n": "gnd", "value": 1},
        {"id": "R2", "type": "R", "node_p": "a", "node_n": "0", "value": 1},
    ],
}
try:
    export_netlist(conflict)
    check("接地改名冲突被拒", False)
except CircuitError as e:
    g = e.errors[0]
    check("接地改名冲突被拒", g["code"] == "ground_conflict" and "冲突" in g["message"])

# 导出沿用输入校验（自环 / 无接地）
try:
    export_netlist({"ground": "0", "nodes": ["0"],
                    "elements": [{"id": "R1", "type": "R", "node_p": "0", "node_n": "0", "value": 1}]})
    check("导出拒绝自环", False)
except CircuitError as e:
    check("导出拒绝自环", e.errors[0]["code"] == "validation")

# ---------- 7. 容量限制 ----------
# 端点循环取 n1..n30 与 0，避免端点同名自环干扰“元件数量”判定
big = "\n".join(f"R{i} n{(i % 30) + 1} 0 1k" for i in range(91))
err = parse_error(big)
check(
    "超过 90 元件被拒",
    err is not None and any("90" in it["detail"] for it in err["items"]),
    str(err["items"] if err else None),
)

print()
if failures:
    print(f"{len(failures)} 个用例失败: {failures}")
    sys.exit(1)
print("全部用例通过 ✔")
