"""元件敏感度分析冒烟测试（开发用，不打进镜像）。

运行：python3 test_sensitivity.py
"""
import math
import sys

from app.sensitivity import (
    SENSITIVITY_MAX_RATIO,
    SENSITIVITY_MIN_RATIO,
    sensitivity,
)
from app.solver import CircuitError

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def expect_rejected(payload, *detail_parts):
    try:
        sensitivity(payload)
        return False, "应当被拒绝但敏感度分析成功"
    except CircuitError as e:
        codes = [g["code"] for g in e.errors]
        text = " ".join(
            [g["message"] for g in e.errors]
            + [it.get("detail", "") for g in e.errors for it in g.get("items", [])]
        )
        ok = codes == ["sensitivity_validation"] and all(p in text for p in detail_parts)
        return ok, f"{codes} / {text}"


def expect_stage(payload, code, stage):
    try:
        sensitivity(payload)
        return False, "应当失败但敏感度分析成功"
    except CircuitError as e:
        got = [(g["code"], g.get("stage")) for g in e.errors]
        return got == [(code, stage)], str(got)


def by_id(result):
    return {e["id"]: e for e in result["elements"]}


def divider(t):
    print(f"\n----- {t} -----")


# 分压器：V1=10, R1=10(1→2), R2=20(2→0)，V(2)=20/3，I(R1)=1/3
BASE = {
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
        {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10},
        {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20},
    ],
}

# ---------- 1. 节点电压目标：归一化灵敏度解析解 ----------
divider("1. 目标 V(2)：S_V1=1，S_R1=-1/3，S_R2=+1/3")
r = sensitivity({**BASE, "sensitivity": {"quantity": "voltage", "target": "2", "ratio": 1e-3}})
check("返回 ok", r["ok"] is True)
check("目标回显", r["sensitivity"]["target"] == "2"
      and r["sensitivity"]["quantity"] == "voltage", str(r["sensitivity"]))
check("基准目标值 20/3",
      math.isclose(r["sensitivity"]["target_base"], 20 / 3, abs_tol=1e-10))
e = by_id(r)
check("S(V(2), V1) ≈ 1", math.isclose(e["V1"]["sensitivity"], 1.0, rel_tol=1e-6),
      str(e["V1"]["sensitivity"]))
check("S(V(2), R1) ≈ -1/3", math.isclose(e["R1"]["sensitivity"], -1 / 3, rel_tol=1e-6),
      str(e["R1"]["sensitivity"]))
check("S(V(2), R2) ≈ +1/3", math.isclose(e["R2"]["sensitivity"], 1 / 3, rel_tol=1e-6),
      str(e["R2"]["sensitivity"]))
# 导数 dV2/dR2 = 10*R1/(R1+R2)^2 = 100/900 = 1/9
check("dV2/dR2 ≈ 1/9", math.isclose(e["R2"]["derivative"], 1 / 9, rel_tol=1e-5),
      str(e["R2"]["derivative"]))
check("中心差分用到正负两侧", all(
    math.isfinite(x["target_plus"]) and math.isfinite(x["target_minus"])
    and x["target_plus"] != x["target_minus"]
    for x in e.values() if x["status"] == "ok"))
check("扰动参数为 p·(1±h)",
      math.isclose(e["R2"]["value_plus"], 20 * 1.001)
      and math.isclose(e["R2"]["value_minus"], 20 * 0.999))
check("按 |S| 排序并排名 V1 > R2 > R1",
      [x["id"] for x in r["elements"]] == ["V1", "R2", "R1"]
      and [x["rank"] for x in r["elements"]] == [1, 2, 3],
      str([(x["id"], x["rank"]) for x in r["elements"]]))
check("计数 3/3/0", r["counts"] == {"total": 3, "ok": 3, "failed": 0})

# ---------- 2. 支路电流目标 ----------
divider("2. 目标 I(R1)：S_V1=1，S_R1=-1/3，S_R2=-2/3")
r = sensitivity({**BASE, "sensitivity": {"quantity": "current", "target": "R1", "ratio": 1e-3}})
e = by_id(r)
check("基准电流 1/3", math.isclose(r["sensitivity"]["target_base"], 1 / 3, abs_tol=1e-10))
check("目标单位为 A", r["sensitivity"]["target_unit"] == "A")
check("S(I, V1) ≈ 1", math.isclose(e["V1"]["sensitivity"], 1.0, rel_tol=1e-6))
check("S(I, R1) ≈ -1/3", math.isclose(e["R1"]["sensitivity"], -1 / 3, rel_tol=1e-6))
check("S(I, R2) ≈ -2/3", math.isclose(e["R2"]["sensitivity"], -2 / 3, rel_tol=1e-6))
check("dI/dR2 ≈ -10/900", math.isclose(e["R2"]["derivative"], -10 / 900, rel_tol=1e-5),
      str(e["R2"]["derivative"]))

# ---------- 3. 扰动比例边界 ----------
divider("3. 扰动比例边界 0.01% 与 10%")
for ratio in (SENSITIVITY_MIN_RATIO, SENSITIVITY_MAX_RATIO):
    rr = sensitivity(
        {**BASE, "sensitivity": {"quantity": "voltage", "target": "2", "ratio": ratio}}
    )
    check(f"ratio={ratio:g} 全部成功",
          rr["sensitivity"]["ratio"] == ratio
          and all(x["status"] == "ok" for x in rr["elements"]))

# ---------- 4. 参数校验 ----------
divider("4. 敏感度参数校验")
ok, detail = expect_rejected({**BASE}, "缺少 sensitivity")
check("缺少 sensitivity 段被拒", ok, detail)
ok, detail = expect_rejected({**BASE, "sensitivity": {"quantity": "power", "target": "2",
                                                       "ratio": 0.01}}, "voltage")
check("非法目标类型被拒", ok, detail)
ok, detail = expect_rejected({**BASE, "sensitivity": {"quantity": "voltage", "target": "9",
                                                       "ratio": 0.01}}, "不在节点表")
check("目标节点不存在被拒", ok, detail)
ok, detail = expect_rejected({**BASE, "sensitivity": {"quantity": "current", "target": "Rx",
                                                       "ratio": 0.01}}, "不在元件表")
check("目标支路不存在被拒", ok, detail)
ok, detail = expect_rejected({**BASE, "sensitivity": {"quantity": "voltage", "target": "2",
                                                       "ratio": 1e-5}}, "0.01%")
check("比例低于 0.01% 被拒", ok, detail)
ok, detail = expect_rejected({**BASE, "sensitivity": {"quantity": "voltage", "target": "2",
                                                       "ratio": 0.5}}, "10%")
check("比例高于 10% 被拒", ok, detail)
ok, detail = expect_rejected({**BASE, "sensitivity": {"quantity": "voltage", "target": "2",
                                                       "ratio": "x"}}, "数字")
check("比例非数字被拒", ok, detail)
ok, detail = expect_rejected({**BASE, "sensitivity": {"quantity": "voltage", "target": "",
                                                       "ratio": 0.01}}, "未指定")
check("目标为空被拒", ok, detail)
ok, detail = expect_rejected({**BASE, "sensitivity": {"quantity": "voltage", "target": "2",
                                                       "ratio": True}}, "数字")
check("比例为布尔值被拒", ok, detail)

# ---------- 5. 基准网络失败：整体 422 + baseline 阶段标记 ----------
divider("5. 基准网络无唯一解：baseline 阶段错误")
ok, detail = expect_stage({**BASE, "ground": "",
                           "sensitivity": {"quantity": "voltage", "target": "2", "ratio": 0.01}},
                          "no_ground", "baseline")
check("无接地 → no_ground@baseline", ok, detail)

# ---------- 6. 零参数元件逐元件失败，其它元件继续 ----------
divider("6. 零参数元件：单项失败不影响其它元件")
zero_net = {
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "I0", "type": "I", "node_p": "1", "node_n": "0", "value": 0},
        {"id": "V0", "type": "V", "node_p": "1", "node_n": "0", "value": 0},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 5},
    ],
}
r = sensitivity({**zero_net,
                 "sensitivity": {"quantity": "voltage", "target": "1", "ratio": 0.01}})
e = by_id(r)
check("零电流源失败", e["I0"]["status"] == "error"
      and e["I0"]["side"] == "both"
      and e["I0"]["errors"][0]["code"] == "zero_parameter",
      str(e["I0"]))
check("零电压源失败", e["V0"]["status"] == "error"
      and e["V0"]["errors"][0]["side"] == "both")
check("电阻项照常成功", e["R1"]["status"] == "ok")
check("失败项无灵敏度字段", "sensitivity" not in e["I0"]
      and "derivative" not in e["I0"])
check("计数 1 成功 2 失败", r["counts"] == {"total": 3, "ok": 1, "failed": 2})
check("失败项排名为空且排到末尾",
      [x["id"] for x in r["elements"]] == ["R1", "I0", "V0"]
      and [x["rank"] for x in r["elements"]] == [None, None, None],
      str([(x["id"], x["rank"]) for x in r["elements"]]))

# ---------- 7. 目标基准值为 0（接地节点）：sensitivity=null，导数保留 ----------
divider("7. 接地节点目标 F₀=0：不可归一化，仅给导数")
r = sensitivity({**BASE, "sensitivity": {"quantity": "voltage", "target": "0", "ratio": 0.01}})
check("基准值为 0", r["sensitivity"]["target_base"] == 0.0)
check("全部元件成功但无排名",
      all(x["status"] == "ok" and x["rank"] is None and x["sensitivity"] is None
          for x in r["elements"]),
      str([(x["status"], x["rank"], x["sensitivity"]) for x in r["elements"]]))
check("导数仍有限", all(math.isfinite(x["derivative"]) for x in r["elements"]))

# ---------- 8. 不改写调用方电路表 ----------
divider("8. 深拷贝隔离")
payload = {**BASE, "sensitivity": {"quantity": "voltage", "target": "2", "ratio": 0.1}}
snapshot = [(x["id"], x["value"]) for x in payload["elements"]]
sensitivity(payload)
check("元件参数不被改写",
      [(x["id"], x["value"]) for x in payload["elements"]] == snapshot)
check("元件数量不变", len(payload["elements"]) == 3)

# ---------- 9. 电流源网络：目标对电流源参数为线性，灵敏度 ±1 / 0 ----------
divider("9. 纯电流源 + 电阻：I 元件灵敏度")
i_net = {
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
        {"id": "I1", "type": "I", "node_p": "1", "node_n": "0", "value": 2},
        {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 5},
    ],
}
r = sensitivity({**i_net,
                 "sensitivity": {"quantity": "current", "target": "R1", "ratio": 1e-3}})
e = by_id(r)
# I(R1) = -I1：对 I1 灵敏度为 1（同号），与 R1 无关 → 0
check("S(I_R1, I1) ≈ 1", math.isclose(e["I1"]["sensitivity"], 1.0, rel_tol=1e-6),
      str(e["I1"]["sensitivity"]))
check("S(I_R1, R1) ≈ 0", abs(e["R1"]["sensitivity"]) < 1e-9,
      str(e["R1"]["sensitivity"]))

# ---------- 10. 大比例扰动（10%）结果仍是有限实数并返回两侧目标值 ----------
divider("10. 10% 大扰动")
r = sensitivity({**BASE, "sensitivity": {"quantity": "voltage", "target": "2",
                                          "ratio": SENSITIVITY_MAX_RATIO}})
e = by_id(r)
# R1 ±10% → V2 = 10*20/(R1'+20)，R1'=9 → 200/29，R1'=11 → 200/31
check("正扰动（R1 变大→分压变小）", math.isclose(e["R1"]["target_plus"], 200 / 31, rel_tol=1e-12),
      str(e["R1"]["target_plus"]))
check("负扰动（R1 变小→分压变大）", math.isclose(e["R1"]["target_minus"], 200 / 29, rel_tol=1e-12),
      str(e["R1"]["target_minus"]))

# ---------- 11. 警告透传 ----------
divider("11. 基准网络数值警告透传")
check("正常网络无警告", r.get("warnings", []) == [])

print()
if failures:
    print(f"{len(failures)} 个用例失败: {failures}")
    sys.exit(1)
print("全部用例通过 ✔")
