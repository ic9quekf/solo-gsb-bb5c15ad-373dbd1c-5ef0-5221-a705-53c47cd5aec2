"""两节点端口戴维南等效冒烟测试（开发用，不打进镜像）。

运行：python3 test_thevenin.py
"""
import math
import sys

from app.solver import CircuitError
from app.thevenin import thevenin_equivalent

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def codes(payload):
    try:
        thevenin_equivalent(payload)
        return None, None
    except CircuitError as e:
        return [(g["code"], g.get("stage")) for g in e.errors], e.errors


# 示例①网络：V2=16/3、V3=-2/3
BASE = {
    "ground": "0",
    "nodes": ["0", "1", "2", "3"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10},
        {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20},
        {"id": "R3", "type": "R", "node_p": "2", "node_n": "3", "value": 30},
        {"id": "I1", "type": "I", "node_p": "3", "node_n": "0", "value": 0.2},
    ],
}

# ---------- 1. 端口 (3,0)：V_oc=-2/3，R_eq=30+(10||20)=110/3 ----------
r = thevenin_equivalent({**BASE, "port": {"node_p": "3", "node_n": "0"}})
check("返回 ok", r["ok"] is True)
check("端口回显 3→0", r["port"] == {"node_p": "3", "node_n": "0"}, str(r["port"]))
check("V_oc(3,0)=-2/3", math.isclose(r["open_circuit_voltage"], -2 / 3, abs_tol=1e-9),
      str(r["open_circuit_voltage"]))
check("R_eq(3,0)=110/3", math.isclose(r["equivalent_resistance"], 110 / 3, abs_tol=1e-9),
      str(r["equivalent_resistance"]))
check("测试电流 1A", r["test_current"] == 1.0)

# ---------- 2. 端口反向 (0,3)：V_oc 反号，R_eq 相同 ----------
r = thevenin_equivalent({**BASE, "port": {"node_p": "0", "node_n": "3"}})
check("反向端口 V_oc=+2/3", math.isclose(r["open_circuit_voltage"], 2 / 3, abs_tol=1e-9),
      str(r["open_circuit_voltage"]))
check("反向端口 R_eq 相同", math.isclose(r["equivalent_resistance"], 110 / 3, abs_tol=1e-9))

# ---------- 3. 端口 (2,0)：悬空的 R3 支路不贡献电阻，R_eq=10||20=20/3 ----------
r = thevenin_equivalent({**BASE, "port": {"node_p": "2", "node_n": "0"}})
check("V_oc(2,0)=16/3", math.isclose(r["open_circuit_voltage"], 16 / 3, abs_tol=1e-9),
      str(r["open_circuit_voltage"]))
check("R_eq(2,0)=20/3（悬空电阻不计）",
      math.isclose(r["equivalent_resistance"], 20 / 3, abs_tol=1e-9),
      str(r["equivalent_resistance"]))

# ---------- 4. 理想电压源端口：R_eq=0 ----------
r = thevenin_equivalent({
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
    ],
    "port": {"node_p": "1", "node_n": "0"},
})
check("理想电压源端口 V_oc=10", math.isclose(r["open_circuit_voltage"], 10.0, abs_tol=1e-9))
check("理想电压源端口 R_eq=0", r["equivalent_resistance"] == 0.0,
      str(r["equivalent_resistance"]))

# ---------- 5. 纯电流源 + 并联电阻：V 置零开路、I 置零开路，只剩 R ----------
r = thevenin_equivalent({
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "I1", "type": "I", "node_p": "1", "node_n": "0", "value": 2},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 5},
    ],
    "port": {"node_p": "1", "node_n": "0"},
})
# I 元件正值电流 p→n，p 端注入 -J：V(1) = -2*5 = -10
check("电流源负载 V_oc=-10", math.isclose(r["open_circuit_voltage"], -10.0, abs_tol=1e-9),
      str(r["open_circuit_voltage"]))
check("电流源开路后 R_eq=5", math.isclose(r["equivalent_resistance"], 5.0, abs_tol=1e-9),
      str(r["equivalent_resistance"]))

# ---------- 6. 结果与接地选择无关（网络全连通时换地，端口量不变） ----------
r_g3 = thevenin_equivalent({**BASE, "ground": "3",
                            "port": {"node_p": "3", "node_n": "0"}})
check("换地后 V_oc 不变",
      math.isclose(r_g3["open_circuit_voltage"], -2 / 3, abs_tol=1e-9),
      str(r_g3["open_circuit_voltage"]))
check("换地后 R_eq 不变",
      math.isclose(r_g3["equivalent_resistance"], 110 / 3, abs_tol=1e-9),
      str(r_g3["equivalent_resistance"]))

# ---------- 7. 不改写调用方电路表（独立源数值与测试源隔离） ----------
payload = {**BASE, "port": {"node_p": "3", "node_n": "0"}}
snapshot = [(e["id"], e["value"]) for e in payload["elements"]]
thevenin_equivalent(payload)
check("元件表数值不被改写",
      [(e["id"], e["value"]) for e in payload["elements"]] == snapshot)
check("测试电流源不残留在元件表",
      all(not e["id"].startswith("__port") for e in payload["elements"]))

# ---------- 8. 端口参数校验 ----------
c, _ = codes(BASE)
check("缺少 port 段报 port_validation", c == [("port_validation", None)], str(c))

c, _ = codes({**BASE, "port": {"node_p": "2", "node_n": "2"}})
check("p、n 相同被拒", c == [("port_validation", None)], str(c))

c, errs = codes({**BASE, "port": {"node_p": "9", "node_n": "0"}})
check("p 不在节点表被拒", c == [("port_validation", None)], str(c))
check("指名缺失节点", any(i.get("ref") == "9" for i in errs[0]["items"]),
      str(errs[0]["items"]))

c, _ = codes({**BASE, "port": {"node_p": "", "node_n": "0"}})
check("p 为空被拒", c == [("port_validation", None)], str(c))

c, _ = codes({**BASE, "port": {"node_p": "1", "node_n": ""}})
check("n 为空被拒", c == [("port_validation", None)], str(c))

c, _ = codes({**BASE, "port": {}})
check("空 port 对象被拒", c == [("port_validation", None)], str(c))

# ---------- 9. 阶段 1 失败：错误分组带 open_circuit 阶段标记 ----------
c, _ = codes({**BASE, "ground": "", "port": {"node_p": "1", "node_n": "0"}})
check("无接地 → 阶段1 no_ground", c == [("no_ground", "open_circuit")], str(c))

float_net = {
    "ground": "0",
    "nodes": ["0", "1", "2", "3"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "V2", "type": "V", "node_p": "2", "node_n": "3", "value": 5},
        {"id": "R2", "type": "R", "node_p": "2", "node_n": "3", "value": 100},
    ],
}
c, _ = codes({**float_net, "port": {"node_p": "2", "node_n": "3"}})
check("浮置子网 → 阶段1 floating_subnet", c == [("floating_subnet", "open_circuit")], str(c))

conflict_net = {
    "ground": "0", "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 12},
        {"id": "V2", "type": "V", "node_p": "1", "node_n": "0", "value": 5},
    ],
}
c, _ = codes({**conflict_net, "port": {"node_p": "1", "node_n": "0"}})
check("电压源冲突 → 阶段1 voltage_conflict", c == [("voltage_conflict", "open_circuit")], str(c))

# ---------- 10. 输入校验错误同样归在阶段 1（端口本身没问题） ----------
bad = {"ground": "0", "nodes": ["0", "0"], "elements": [],
       "port": {"node_p": "0", "node_n": "0"}}
c, _ = codes(bad)
check("重复节点先报 port_validation（p==n）", c == [("port_validation", None)], str(c))

bad2 = {"ground": "0", "nodes": ["0", "0"], "elements": [],
        "port": {"node_p": "0", "node_n": "1"}}  # 1 不在（重复后只保留一个）节点表
c, _ = codes(bad2)
check("端口缺节点优先于网络校验", c[0][0] == "port_validation", str(c))

# ---------- 11. 与扫描 / 求解一致：等值电压源并联原网络奇异 ----------
singular_net = {
    "ground": "0", "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 5},
        {"id": "V2", "type": "V", "node_p": "1", "node_n": "0", "value": 5},
    ],
}
c, _ = codes({**singular_net, "port": {"node_p": "1", "node_n": "0"}})
check("等值电压源并联 → 阶段1 singular", c == [("singular", "open_circuit")], str(c))

# ---------- 12. 测试源编号与用户元件撞号时自动避让 ----------
collision = {
    "ground": "0", "nodes": ["0", "1", "2"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10},
        {"id": "__port_Itest__", "type": "R", "node_p": "2", "node_n": "0", "value": 4},
    ],
    "port": {"node_p": "2", "node_n": "0"},
}
r = thevenin_equivalent(collision)
# 阶段2：V1 短路（1=地），端口 2-0 间为 10Ω ∥ 4Ω = 20/7 Ω；
# 若测试源撞号未避让，solve 会判元件编号重复而报 validation
check("测试源撞号自动避让，R_eq=10∥4=20/7",
      math.isclose(r["equivalent_resistance"], 20 / 7, abs_tol=1e-9),
      str(r["equivalent_resistance"]))
check("V_oc 分压=10*4/14",
      math.isclose(r["open_circuit_voltage"], 40 / 14, abs_tol=1e-9),
      str(r["open_circuit_voltage"]))
check("用户撞号元件不受影响",
      collision["elements"][2]["id"] == "__port_Itest__"
      and collision["elements"][2]["value"] == 4)

print()
if failures:
    print(f"{len(failures)} 个用例失败: {failures}")
    sys.exit(1)
print("全部用例通过 ✔")
