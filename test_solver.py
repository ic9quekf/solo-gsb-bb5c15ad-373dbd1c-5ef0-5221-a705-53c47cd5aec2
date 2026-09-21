"""求解器数值正确性与结构诊断冒烟测试（开发用，不打进镜像）。"""
import math
import sys

from app.solver import CircuitError, solve

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def codes(payload):
    try:
        solve(payload)
        return None, None
    except CircuitError as e:
        return [g["code"] for g in e.errors], e.errors


# ---------- 1. 简单分压：10V 源串联 10Ω+20Ω 接地 ----------
r = solve({
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10},
        {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20},
    ],
})
v = {x["id"]: x["voltage"] for x in r["node_voltages"]}
check("分压 V1=10", math.isclose(v["1"], 10.0, abs_tol=1e-10))
check("分压 V2=6.6667", math.isclose(v["2"], 20 / 3, abs_tol=1e-10), str(v["2"]))
b = {x["id"]: x for x in r["branches"]}
check("串联电流 1/3 A", math.isclose(b["R1"]["current"], 1 / 3, abs_tol=1e-10),
      str(b["R1"]["current"]))
check("电压源电流 -1/3 A（实际 n→p）",
      math.isclose(b["V1"]["current"], -1 / 3, abs_tol=1e-10), str(b["V1"]["current"]))
check("功率平衡误差≈0", r["power_balance"]["error_abs"] < 1e-9,
      str(r["power_balance"]["error_abs"]))
check("电压源发出功率", b["V1"]["power"] < 0 and math.isclose(b["V1"]["power"], -10 / 3, abs_tol=1e-9))

# ---------- 2. 示例①：含电流源的桥 ----------
r = solve({
    "ground": "0",
    "nodes": ["0", "1", "2", "3"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10},
        {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20},
        {"id": "R3", "type": "R", "node_p": "2", "node_n": "3", "value": 30},
        {"id": "I1", "type": "I", "node_p": "3", "node_n": "0", "value": 0.2},
    ],
})
v = {x["id"]: x["voltage"] for x in r["node_voltages"]}
# 手算: KCL@3: (V3-V2)/30+0.2=0 → V3=V2-6
#      KCL@2: (V2-10)/10+V2/20+(V2-V3)/30=0 → V2=16/3, V3=-2/3
check("示例① V2=16/3", math.isclose(v["2"], 16 / 3, abs_tol=1e-9), str(v["2"]))
check("示例① V3=-2/3", math.isclose(v["3"], -2 / 3, abs_tol=1e-9), str(v["3"]))
check("示例① 功率平衡", r["power_balance"]["error_abs"] < 1e-8,
      str(r["power_balance"]["error_abs"]))

# ---------- 3. 纯电阻 + 电流源 ----------
r = solve({
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "I1", "type": "I", "node_p": "1", "node_n": "0", "value": 2},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 5},
    ],
})
v = {x["id"]: x["voltage"] for x in r["node_voltages"]}
check("电流源负载 V1=-10", math.isclose(v["1"], -10.0, abs_tol=1e-10), str(v["1"]))
check("电流源负载功率平衡", r["power_balance"]["error_abs"] < 1e-9)

# ---------- 4. 无接地 ----------
c, _ = codes({"ground": "", "nodes": ["0", "1"],
              "elements": [{"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}]})
check("无接地报 no_ground", c == ["no_ground"], str(c))

# ---------- 5. 接地节点不在表中 ----------
c, _ = codes({"ground": "9", "nodes": ["0", "1"],
              "elements": [{"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}]})
check("错误接地报 ground_not_found", c == ["ground_not_found"], str(c))

# ---------- 6. 浮置子网：2,3 只含 V+R，与地无连接 ----------
c, errs = codes({
    "ground": "0",
    "nodes": ["0", "1", "2", "3"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "V2", "type": "V", "node_p": "2", "node_n": "3", "value": 5},
        {"id": "R2", "type": "R", "node_p": "2", "node_n": "3", "value": 100},
    ],
})
check("浮置子网报 floating_subnet", c == ["floating_subnet"], str(c))
check("浮置子网列出节点 2,3",
      errs[0]["items"][0]["nodes"] == ["2", "3"], str(errs[0]["items"]))

# ---------- 7. 仅电流源相连的节点也算浮置 ----------
c, errs = codes({
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 5},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "I1", "type": "I", "node_p": "2", "node_n": "1", "value": 1},
    ],
})
check("电流源悬空节点报 floating_subnet", c == ["floating_subnet"], str(c))
check("悬空节点为 2", errs[0]["items"][0]["nodes"] == ["2"], str(errs[0]["items"]))

# ---------- 8. 电压源矛盾：12V 与 5V 并联 ----------
c, errs = codes({
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 12},
        {"id": "V2", "type": "V", "node_p": "1", "node_n": "0", "value": 5},
    ],
})
check("电压源并联冲突", c == ["voltage_conflict"], str(c))
refs = {i["ref"] for i in errs[0]["items"]}
check("冲突元件被点名", "V2" in refs, str(refs))

# ---------- 9. 方向相反但数值一致的电压源不冲突（串联回路） ----------
# V1: 1-0 = 10; V2: 0-2 = -4  => V2 正极 0、负极 2，即 V(2)=4
r = solve({
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "V2", "type": "V", "node_p": "0", "node_n": "2", "value": -4},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10},
    ],
})
v = {x["id"]: x["voltage"] for x in r["node_voltages"]}
check("一致电压源回路 V1=10 V2=4",
      math.isclose(v["1"], 10) and math.isclose(v["2"], 4), str(v))
check("一致回路功率平衡", r["power_balance"]["error_abs"] < 1e-9)

# ---------- 10. 串联电阻回路中的 KVL 冲突（10V + 5V 同向，无电阻） ----------
c, _ = codes({
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "V2", "type": "V", "node_p": "0", "node_n": "1", "value": 5},
    ],
})
check("KVL 回路冲突", c == ["voltage_conflict"], str(c))

# ---------- 11. 输入校验 ----------
c, _ = codes({"ground": "0", "nodes": ["0", "0"], "elements": []})
check("重复节点", c == ["validation"], str(c))
c, _ = codes({"ground": "0", "nodes": ["0"],
              "elements": [{"id": "R1", "type": "R", "node_p": "0", "node_n": "0", "value": 1}]})
check("自环元件", c == ["validation"], str(c))
c, _ = codes({"ground": "0", "nodes": ["0"],
              "elements": [{"id": "R1", "type": "R", "node_p": "0", "node_n": "0", "value": 0}]})
check("零/负电阻", c == ["validation"], str(c))
c, _ = codes({"ground": "0", "nodes": ["0"], "elements": []})
check("仅接地节点可解", c is None, str(c))

# ---------- 12. 超过 30 节点 ----------
nodes = [str(i) for i in range(31)]
c, _ = codes({"ground": "0", "nodes": nodes, "elements": []})
check("31 节点被拒", c == ["validation"], str(c))

# ---------- 13. 0V 电压源（理想导线）不矛盾 ----------
r = solve({
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "W1", "type": "V", "node_p": "1", "node_n": "2", "value": 0},
        {"id": "R1", "type": "R", "node_p": "2", "node_n": "0", "value": 10},
    ],
})
v = {x["id"]: x["voltage"] for x in r["node_voltages"]}
check("0V 源短接 V1=V2=10", math.isclose(v["2"], 10.0, abs_tol=1e-10), str(v))

print()
if failures:
    print(f"{len(failures)} 个用例失败: {failures}")
    sys.exit(1)
print("全部用例通过 ✔")
