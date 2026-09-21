"""单参数扫描冒烟测试（开发用，不打进镜像）。

运行：python3 test_sweep.py
"""
import math
import sys

from app.solver import CircuitError
from app.sweep import SWEEP_MAX_POINTS, SWEEP_MIN_POINTS, sweep

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def expect_rejected(payload, *detail_parts):
    try:
        sweep(payload)
        return False, "应当被拒绝但扫描成功"
    except CircuitError as e:
        codes = [g["code"] for g in e.errors]
        text = " ".join(
            it.get("detail", "") for g in e.errors for it in g.get("items", [])
        )
        ok = codes == ["sweep_validation"] and all(p in text for p in detail_parts)
        return ok, f"{codes} / {text}"


def divider(t):
    print(f"\n----- {t} -----")


divider("1. 电阻线性扫描：10V 源 + 可变电阻接地，V=10、I=10/R")
payload = {
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
    ],
    "sweep": {"element_id": "R1", "start": 10.0, "end": 100.0, "points": 10},
}
r = sweep(payload)
check("扫描返回 ok", r["ok"] is True)
check("点数为 10", len(r["points"]) == 10, str(len(r["points"])))
check("端点取值正确",
      math.isclose(r["points"][0]["value"], 10.0)
      and math.isclose(r["points"][-1]["value"], 100.0),
      str((r["points"][0]["value"], r["points"][-1]["value"])))
check("等间距", all(
    math.isclose(r["points"][i]["value"], 10.0 + 10.0 * i) for i in range(10)
))
check("节点顺序固定", r["nodes"] == ["0", "1"], str(r["nodes"]))
check("全部成功", all(p["status"] == "ok" for p in r["points"]))
v1_index = r["nodes"].index("1")
r1_index = [b["id"] for b in r["branches"]].index("R1")
check("V(1) 恒为 10V", all(
    math.isclose(p["voltages"][v1_index], 10.0, abs_tol=1e-9) for p in r["points"]
))
check("I(R1)=10/R 随阻值下降", all(
    math.isclose(p["currents"][r1_index], 10.0 / p["value"], rel_tol=1e-9)
    for p in r["points"]
))
check("不改写原电路表：R1 仍为 10",
      payload["elements"][1]["value"] == 10, str(payload["elements"][1]["value"]))

divider("2. 降序扫描同样合法")
payload2 = {**payload,
            "sweep": {"element_id": "R1", "start": 100.0, "end": 10.0, "points": 5}}
r2 = sweep(payload2)
check("降序首点为止值侧", math.isclose(r2["points"][0]["value"], 100.0))
check("降序末点为起值侧", math.isclose(r2["points"][-1]["value"], 10.0))

divider("3a. 扫描范围跨零的电阻被参数校验整段拒绝（不产生采样点）")
payload3a = {
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
    ],
    "sweep": {"element_id": "R1", "start": -10.0, "end": 10.0, "points": 5},
}
ok, detail = expect_rejected(payload3a, "大于 0")
check("跨零电阻范围被拒", ok, detail)

divider("3b. 并联理想电压源：不等值报 voltage_conflict，等值矩阵奇异（singular）")
payload3b = {
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 5},
        {"id": "V2", "type": "V", "node_p": "1", "node_n": "0", "value": 5},
    ],
    "sweep": {"element_id": "V2", "start": 1.0, "end": 9.0, "points": 5},
}
r3b = sweep(payload3b)
codes = [[g["code"] for g in p["errors"]] if p["status"] == "error" else []
         for p in r3b["points"]]
check("等值点 singular、其余 voltage_conflict",
      codes[0] == ["voltage_conflict"] and codes[2] == ["singular"]
      and codes[4] == ["voltage_conflict"], str(codes))
check("所有点都失败但扫描不中断",
      all(p["status"] == "error" for p in r3b["points"]))

divider("4. 基准网络无接地：所有点失败但仍返回节点/支路清单")
payload4 = {
    "ground": "",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
    ],
    "sweep": {"element_id": "R1", "start": 1.0, "end": 10.0, "points": 3},
}
r4 = sweep(payload4)
check("三点全失败", all(p["status"] == "error" for p in r4["points"]))
check("失败点带 no_ground",
      all(p["errors"][0]["code"] == "no_ground" for p in r4["points"]))
check("兜底清单含节点与支路",
      r4["nodes"] == ["0", "1"] and [b["id"] for b in r4["branches"]] == ["V1", "R1"],
      str(r4["nodes"]) + str(r4["branches"]))

divider("5. 扫描参数校验")
base = {
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
    ],
}

ok, detail = expect_rejected({**base, "sweep": {"element_id": "Rx", "start": 1,
                                                "end": 10, "points": 5}}, "不在元件表中")
check("元件不存在被拒", ok, detail)

ok, detail = expect_rejected({**base, "sweep": {"element_id": "R1", "start": 1,
                                                "end": 1, "points": 5}}, "不能相同")
check("起值=止值被拒", ok, detail)

ok, detail = expect_rejected({**base, "sweep": {"element_id": "R1", "start": 0,
                                                "end": 10, "points": 5}}, "大于 0")
check("电阻起点非正被拒", ok, detail)

ok, detail = expect_rejected({**base, "sweep": {"element_id": "R1", "start": -10,
                                                "end": 10, "points": 5}}, "大于 0")
check("电阻范围跨零被拒", ok, detail)

ok, detail = expect_rejected({**base, "sweep": {"element_id": "R1", "start": 10,
                                                "end": 1, "points": SWEEP_MIN_POINTS - 1}},
                             f"{SWEEP_MIN_POINTS}～{SWEEP_MAX_POINTS}")
check(f"点数 {SWEEP_MIN_POINTS - 1}（下限）被拒", ok, detail)

ok, detail = expect_rejected({**base, "sweep": {"element_id": "R1", "start": 1,
                                                "end": 10, "points": SWEEP_MAX_POINTS + 1}},
                             f"{SWEEP_MIN_POINTS}～{SWEEP_MAX_POINTS}")
check(f"点数 {SWEEP_MAX_POINTS + 1}（上限）被拒", ok, detail)

ok, detail = expect_rejected({**base, "sweep": {"element_id": "R1", "start": 1,
                                                "end": 10, "points": 5.0}}, "整数")
check("非整数点数被拒", ok, detail)

ok, detail = expect_rejected({**base, "sweep": {"element_id": "R1", "start": "x",
                                                "end": 10, "points": 5}}, "起值")
check("起值非数字被拒", ok, detail)

dup = {**base, "elements": [
    {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
    {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10},
    {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 20},
], "sweep": {"element_id": "R1", "start": 1, "end": 10, "points": 5}}
ok, detail = expect_rejected(dup, "重复")
check("元件编号重复被拒", ok, detail)

ok, detail = expect_rejected({**base}, "缺少 sweep")
check("缺少 sweep 段被拒", ok, detail)

divider("6. 边界点数合法（2 与 101）")
r_lo = sweep({**base, "sweep": {"element_id": "R1", "start": 1, "end": 2,
                                "points": SWEEP_MIN_POINTS}})
check("2 点扫描", len(r_lo["points"]) == 2)
r_hi = sweep({**base, "sweep": {"element_id": "R1", "start": 1, "end": 2,
                                "points": SWEEP_MAX_POINTS}})
check("101 点扫描", len(r_hi["points"]) == 101)

divider("7. 电流源扫描：纯电阻网络 V=I·R 线性")
payload7 = {
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "I1", "type": "I", "node_p": "1", "node_n": "0", "value": 2},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 5},
    ],
    "sweep": {"element_id": "I1", "start": -2.0, "end": 2.0, "points": 5},
}
r7 = sweep(payload7)
check("电流源允许扫到负值/跨零", all(p["status"] == "ok" for p in r7["points"]))
check("V=I·R 线性关系", all(
    math.isclose(p["voltages"][1], -p["value"] * 5.0, abs_tol=1e-9)
    for p in r7["points"]
), str([(p["value"], p["voltages"][1]) for p in r7["points"]]))

print()
if failures:
    print(f"{len(failures)} 个用例失败: {failures}")
    sys.exit(1)
print("全部用例通过 ✔")
