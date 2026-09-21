/* 直流网络校核器前端（原生 JS） */
"use strict";

const MAX_NODES = 30;
const MAX_ELEMENTS = 90;
const SWEEP_MIN_POINTS = 2;
const SWEEP_MAX_POINTS = 101;
const TYPE_LABEL = { R: "电阻", I: "电流源", V: "电压源" };
const TYPE_UNIT = { R: "Ω", I: "A", V: "V" };
const POWER_OK_REL = 1e-6; // 相对误差阈值（百万分之一）

let state = freshState();
// 最近一次扫描的完整响应（含全部点的节点电压 / 支路电流），切换观测量只读取它
let sweepResult = null;
let sweepView = { quantity: "voltage", target: null };
// 最近一次端口等效结果；节点表结构变更后立即失效（置 null）
let theveninResult = null;
// 最近一次成功的求解结果：敏感度分析的目标下拉由此填充，节点表变更后失效
let lastSolve = null;
// 最近一次敏感度分析结果；节点表结构变更后立即失效（置 null）
let sensitivityResult = null;
// 敏感度目标下拉当前选择（跨目标类型切换时尽量保留同编号）
let sensView = { quantity: "voltage", target: null };
// 端口下拉当前值（随节点表重建而保留 / 兜底）
let portSel = { p: "1", n: "0" };
// 上一次渲染端口下拉时的节点表签名，用于识别“节点变更”
let nodeSig = "";

function freshState() {
  return {
    ground: "0",
    nodes: [{ id: "0" }, { id: "1" }],
    elements: [
      { id: "V1", type: "V", node_p: "1", node_n: "0", value: "10" },
      { id: "R1", type: "R", node_p: "1", node_n: "0", value: "10" },
    ],
  };
}

const SAMPLES = {
  1: {
    ground: "0",
    nodes: [{ id: "0" }, { id: "1" }, { id: "2" }, { id: "3" }],
    elements: [
      { id: "V1", type: "V", node_p: "1", node_n: "0", value: "10" },
      { id: "R1", type: "R", node_p: "1", node_n: "2", value: "10" },
      { id: "R2", type: "R", node_p: "2", node_n: "0", value: "20" },
      { id: "R3", type: "R", node_p: "2", node_n: "3", value: "30" },
      { id: "I1", type: "I", node_p: "3", node_n: "0", value: "0.2" },
    ],
  },
  "2": {
    ground: "0",
    nodes: [{ id: "0" }, { id: "1" }, { id: "2" }, { id: "3" }],
    elements: [
      { id: "V1", type: "V", node_p: "1", node_n: "0", value: "10" },
      { id: "R1", type: "R", node_p: "1", node_n: "0", value: "10" },
      { id: "V2", type: "V", node_p: "2", node_n: "3", value: "5" },
      { id: "R2", type: "R", node_p: "2", node_n: "3", value: "100" },
    ],
  },
  "3": {
    ground: "0",
    nodes: [{ id: "0" }, { id: "1" }],
    elements: [
      { id: "V1", type: "V", node_p: "1", node_n: "0", value: "12" },
      { id: "V2", type: "V", node_p: "1", node_n: "0", value: "5" },
    ],
  },
};

/* ------------------------------ DOM 辅助 ------------------------------ */
const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, children = []) => {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  });
  children.forEach((c) => node.appendChild(c));
  return node;
};

/* ------------------------------ 表格渲染 ------------------------------ */
function nodeIds() {
  return state.nodes.map((n) => n.id);
}

function setSelectOptions(select, values, current) {
  // 重建某个 <select> 的选项并保留当前值；不会重建文本输入框，故不影响键入
  select.innerHTML = "";
  values.forEach((v) => select.appendChild(el("option", { value: v, text: v })));
  select.value = current && values.includes(current) ? current : "";
}

function renderGroundSelect() {
  if (!nodeIds().includes(state.ground)) state.ground = nodeIds()[0] || "";
  setSelectOptions($("#ground-select"), nodeIds(), state.ground);
}

/* ------------------------------ 端口等效 ------------------------------ */
function currentNodeSig() {
  return JSON.stringify(nodeIds());
}

function fillPortSelect(select, current) {
  // 与元件端点下拉一致：“—”空选项 + 缺失节点红字保留
  select.innerHTML = "";
  select.appendChild(el("option", { value: "", text: "—" }));
  state.nodes.forEach((n) =>
    select.appendChild(el("option", { value: n.id, text: n.id }))
  );
  if (current && !nodeIds().includes(current)) {
    const opt = el("option", { value: current, text: current + "（缺失）" });
    opt.style.color = "var(--err)";
    select.appendChild(opt);
  }
  select.value = current || "";
}

function renderPortSelects() {
  if (currentNodeSig() !== nodeSig) {
    // 节点表结构（数量 / 编号 / 顺序）发生变化：旧的端口等效 / 敏感度结果失效
    invalidateThevenin();
    invalidateSensitivity();
    nodeSig = currentNodeSig();
  }
  // 端点缺失时保留下拉中的红字“（缺失）”选项，不静默改值（与元件端点下拉一致）
  fillPortSelect($("#port-p"), portSel.p);
  fillPortSelect($("#port-n"), portSel.n);
}

function invalidateThevenin() {
  if (theveninResult === null) return;
  theveninResult = null;
  $("#thevenin-result").classList.add("hidden");
  const fb = $("#thevenin-feedback");
  fb.classList.add("hidden");
  fb.innerHTML = "";
}

/* ------------------------------ 元件敏感度分析 ------------------------------ */
function setSensitivityFormEnabled(enabled) {
  // 求解成功前 / 节点表变更后：目标下拉与提交按钮不可用
  const disabled = !enabled;
  $("#sens-quantity").disabled = disabled;
  $("#sens-target").disabled = disabled;
  $("#sens-ratio").disabled = disabled;
  $("#btn-sensitivity").disabled = disabled;
  if (!enabled) {
    $("#sens-target").innerHTML = "";
  }
}

function resetSensitivityTargets() {
  // 按当前目标类型重建目标下拉（节点来自节点表，支路来自元件表）
  const sel = $("#sens-target");
  const quantity = $("#sens-quantity").value;
  $("#sens-target-label").textContent = quantity === "voltage" ? "目标节点" : "目标支路";
  sel.innerHTML = "";
  if (quantity === "voltage") {
    state.nodes.forEach((n) => sel.appendChild(el("option", { value: n.id, text: n.id })));
  } else {
    state.elements.forEach((e) =>
      sel.appendChild(
        el("option", {
          value: e.id,
          text: `${e.id || "(未命名)"}（${e.type} ${e.node_p}→${e.node_n}）`,
        })
      )
    );
  }
  const values = [...sel.options].map((o) => o.value);
  if (sensView.quantity === quantity && sensView.target && values.includes(sensView.target)) {
    sel.value = sensView.target;
  } else {
    sel.value = values[0] || "";
  }
  sensView = { quantity, target: sel.value };
}

function invalidateSensitivity() {
  // 节点表结构变更：清空旧结果，最近一次求解也不再与当前节点表对应
  sensitivityResult = null;
  lastSolve = null;
  $("#sensitivity-result").classList.add("hidden");
  const fb = $("#sensitivity-feedback");
  fb.classList.add("hidden");
  fb.innerHTML = "";
  setSensitivityFormEnabled(false);
}

function resetSensitivity() {
  // 载入示例 / 清空：彻底重置（含当前选择与输入比例）
  invalidateSensitivity();
  sensView = { quantity: "voltage", target: null };
  $("#sens-quantity").value = "voltage";
  $("#sens-ratio").value = "0.1";
}

$("#sens-quantity").addEventListener("change", () => {
  if (!lastSolve) return;
  resetSensitivityTargets();
});
$("#sens-target").addEventListener("change", (ev) => {
  sensView.target = ev.target.value;
});

$("#port-p").addEventListener("change", (ev) => {
  portSel.p = ev.target.value;
});
$("#port-n").addEventListener("change", (ev) => {
  portSel.n = ev.target.value;
});

function fillEndpointSelect(select, current) {
  // “—”空选项 + 缺失节点以红字样式的临时选项保留，避免值被静默改掉
  select.innerHTML = "";
  select.appendChild(el("option", { value: "", text: "—" }));
  state.nodes.forEach((n) =>
    select.appendChild(el("option", { value: n.id, text: n.id }))
  );
  if (current && !nodeIds().includes(current)) {
    const opt = el("option", { value: current, text: current + "（缺失）" });
    opt.style.color = "var(--err)";
    select.appendChild(opt);
  }
  select.value = current || "";
}

function renderNodes() {
  const tbody = $("#node-table tbody");
  tbody.innerHTML = "";
  state.nodes.forEach((n, i) => {
    const input = el("input", { value: n.id, placeholder: "如 n1" });
    input.addEventListener("input", () => {
      state.nodes[i].id = input.value.trim();
      renderGroundSelect();
      refreshEndpointSelects();
      // 节点编号变化即结构变化：重建端口下拉并失效旧的等效结果
      renderPortSelects();
    });
    const del = el("button", { class: "icon-del", title: "删除", text: "✕" });
    del.addEventListener("click", () => {
      state.nodes.splice(i, 1);
      renderAll();
    });
    const tr = el("tr", { "data-id": n.id || `__row_${i}` }, [
      el("td", { text: String(i + 1) }),
      el("td", {}, [input]),
      el("td", {}, [del]),
    ]);
    tbody.appendChild(tr);
  });
  $("#node-count").textContent = `${state.nodes.length} / ${MAX_NODES}`;
  $("#btn-add-node").disabled = state.nodes.length >= MAX_NODES;
}

function renderElements() {
  const tbody = $("#element-table tbody");
  tbody.innerHTML = "";
  state.elements.forEach((e, i) => {
    // 编号
    const idInput = el("input", { value: e.id, placeholder: "如 R1" });
    idInput.addEventListener("input", () => (e.id = idInput.value.trim()));

    // 类型
    const typeSel = el("select");
    ["R", "I", "V"].forEach((t) =>
      typeSel.appendChild(el("option", { value: t, text: `${t} ${TYPE_LABEL[t]}` }))
    );
    typeSel.value = e.type;
    typeSel.addEventListener("change", () => (e.type = typeSel.value));

    // 端点
    const pSel = el("select");
    const nSel = el("select");
    fillEndpointSelect(pSel, e.node_p);
    fillEndpointSelect(nSel, e.node_n);
    pSel.addEventListener("change", () => (e.node_p = pSel.value));
    nSel.addEventListener("change", () => (e.node_n = nSel.value));

    // 参数值
    const valInput = el("input", { value: e.value, placeholder: "数值" });
    valInput.addEventListener("input", () => (e.value = valInput.value));

    const del = el("button", { class: "icon-del", title: "删除", text: "✕" });
    del.addEventListener("click", () => {
      state.elements.splice(i, 1);
      renderAll();
    });

    const tr = el("tr", { "data-id": e.id || `__erow_${i}`, "data-index": i }, [
      el("td", {}, [idInput]),
      el("td", {}, [typeSel]),
      el("td", {}, [pSel]),
      el("td", {}, [nSel]),
      el("td", {}, [valInput]),
      el("td", {}, [del]),
    ]);
    tbody.appendChild(tr);
  });
  $("#element-count").textContent = `${state.elements.length} / ${MAX_ELEMENTS}`;
  $("#btn-add-element").disabled = state.elements.length >= MAX_ELEMENTS;
}

function refreshEndpointSelects() {
  document.querySelectorAll("#element-table tbody tr").forEach((tr) => {
    const e = state.elements[Number(tr.getAttribute("data-index"))];
    if (!e) return;
    const selects = tr.querySelectorAll("select");
    fillEndpointSelect(selects[1], e.node_p);
    fillEndpointSelect(selects[2], e.node_n);
  });
}

function renderAll() {
  renderNodes();
  renderElements();
  renderGroundSelect();
  renderPortSelects();
  renderSweepElements();
  // 元件行增删后刷新敏感度支路目标（节点表变更已在 renderPortSelects 中禁用表单）
  if (lastSolve) resetSensitivityTargets();
  clearFeedback();
}

/* ------------------------------ 单参数扫描 ------------------------------ */
function renderSweepElements() {
  const sel = $("#sweep-element");
  const current = sel.value;
  sel.innerHTML = "";
  if (state.elements.length === 0) {
    sel.appendChild(el("option", { value: "", text: "（元件表为空）" }));
    sel.value = "";
    return;
  }
  state.elements.forEach((e) => {
    const label = `${e.id || "(未命名)"} · ${e.type} ${TYPE_LABEL[e.type] || ""}（${e.node_p}→${e.node_n}）`;
    sel.appendChild(el("option", { value: e.id, text: label }));
  });
  // 保留当前已选元件；失效则回落到第一项
  sel.value = current && state.elements.some((e) => e.id === current)
    ? current
    : state.elements[0].id;
}

/* ------------------------------ 新增行 ------------------------------ */
function nextNodeId() {
  const ids = new Set(nodeIds());
  let n = nodeIds().length;
  let candidate;
  do {
    n += 1;
    candidate = String(n);
  } while (ids.has(candidate));
  return candidate;
}

function nextElementId(type) {
  const ids = new Set(state.elements.map((e) => e.id));
  let n = 0;
  let candidate;
  do {
    n += 1;
    candidate = `${type}${n}`;
  } while (ids.has(candidate));
  return candidate;
}

$("#btn-add-node").addEventListener("click", () => {
  if (state.nodes.length >= MAX_NODES) return;
  state.nodes.push({ id: nextNodeId() });
  renderAll();
});

$("#btn-add-element").addEventListener("click", () => {
  if (state.elements.length >= MAX_ELEMENTS) return;
  const type = "R";
  state.elements.push({
    id: nextElementId(type),
    type,
    node_p: nodeIds()[0] || "",
    node_n: nodeIds()[1] || nodeIds()[0] || "",
    value: "1",
  });
  renderAll();
});

$("#ground-select").addEventListener("change", (ev) => {
  state.ground = ev.target.value;
});

document.querySelectorAll("[data-sample]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const s = SAMPLES[btn.getAttribute("data-sample")];
    state = {
      ground: s.ground,
      nodes: s.nodes.map((n) => ({ id: n.id })),
      elements: s.elements.map((e) => ({ ...e, value: String(e.value) })),
    };
    resetSweep();
    resetThevenin();
    resetSensitivity();
    portSel = { p: s.nodes.length > 1 ? s.nodes[1].id : "", n: s.nodes[0]?.id || "" };
    nodeSig = "";
    renderAll();
    $("#placeholder").classList.add("hidden");
  });
});

$("#btn-clear").addEventListener("click", () => {
  state = { ground: "", nodes: [], elements: [] };
  resetSweep();
  resetThevenin();
  resetSensitivity();
  portSel = { p: "", n: "" };
  nodeSig = "";
  renderAll();
  $("#result-area").classList.add("hidden");
  $("#placeholder").classList.remove("hidden");
});

function resetThevenin() {
  theveninResult = null;
  $("#thevenin-result").classList.add("hidden");
  const fb = $("#thevenin-feedback");
  fb.classList.add("hidden");
  fb.innerHTML = "";
}

function resetSweep() {
  sweepResult = null;
  sweepView = { quantity: "voltage", target: null };
  $("#sweep-result").classList.add("hidden");
  const fb = $("#sweep-feedback");
  fb.classList.add("hidden");
  fb.innerHTML = "";
}

/* ------------------------------ 反馈区 ------------------------------ */
function clearFeedback() {
  $("#error-panel").classList.add("hidden");
  $("#error-panel").innerHTML = "";
  $("#warning-panel").classList.add("hidden");
  $("#warning-panel").innerHTML = "";
  document.querySelectorAll("tr.row-error").forEach((tr) => tr.classList.remove("row-error"));
  $("#ground-select").style.borderColor = "";
}

function showErrors(errors) {
  const panel = $("#error-panel");
  panel.innerHTML = "";
  const badRefs = new Set();
  const floatingNodes = new Set();

  errors.forEach((g) => {
    const group = el("div", { class: "err-group" });
    group.appendChild(el("div", { class: "err-title", text: `● ${g.message}` }));
    if (g.items && g.items.length) {
      const ul = el("ul");
      g.items.forEach((item) => {
        if (item.ref) {
          badRefs.add(item.ref);
          ul.appendChild(
            el("li", {}, [
              el("span", { class: "err-ref", text: item.ref + "：" }),
              document.createTextNode(item.detail),
            ])
          );
        } else if (item.nodes) {
          item.nodes.forEach((x) => floatingNodes.add(x));
          ul.appendChild(
            el("li", {}, [
              document.createTextNode("子网节点："),
              el("span", { class: "err-nodes", text: item.nodes.join("、") }),
            ])
          );
        } else {
          ul.appendChild(el("li", { text: item.detail || JSON.stringify(item) }));
        }
      });
      group.appendChild(ul);
    }
    if (["no_ground", "ground_not_found"].includes(g.code)) {
      $("#ground-select").style.borderColor = "var(--err)";
    }
    panel.appendChild(group);
  });

  // 高亮问题节点 / 元件所在行
  const mark = (tbodyId) => {
    document.querySelectorAll(`#${tbodyId} tbody tr`).forEach((tr) => {
      const id = tr.getAttribute("data-id");
      if (badRefs.has(id) || (tbodyId === "node-table" && floatingNodes.has(id))) {
        tr.classList.add("row-error");
      }
    });
  };
  mark("node-table");
  mark("element-table");

  $("#result-area").classList.add("hidden");
  $("#placeholder").classList.add("hidden");
  panel.classList.remove("hidden");
}

function showWarnings(warnings) {
  if (!warnings || !warnings.length) return;
  const panel = $("#warning-panel");
  const group = el("div", { class: "err-group" });
  group.appendChild(el("div", { class: "err-title", text: "⚠ 数值警告" }));
  const ul = el("ul");
  warnings.forEach((w) => ul.appendChild(el("li", { text: w })));
  group.appendChild(ul);
  panel.appendChild(group);
  panel.classList.remove("hidden");
}

/* ------------------------------ 客户端预校验 ------------------------------ */
function clientValidate() {
  const items = [];
  if (state.nodes.length === 0) {
    items.push({ ref: "-", detail: "至少需要一个节点（接地节点）" });
  }
  const idSet = new Set();
  state.nodes.forEach((n) => {
    if (!n.id) items.push({ ref: "-", detail: "存在编号为空的节点行" });
    else if (idSet.has(n.id)) items.push({ ref: n.id, detail: "节点编号重复" });
    idSet.add(n.id);
  });
  if (!state.ground || !idSet.has(state.ground)) {
    items.push({ ref: "-", detail: "未指定接地节点，或接地节点不在节点表中" });
  }

  const eSet = new Set();
  state.elements.forEach((e) => {
    if (!e.id) items.push({ ref: "-", detail: "存在编号为空的元件行" });
    else if (eSet.has(e.id)) items.push({ ref: e.id, detail: "元件编号重复" });
    eSet.add(e.id);
    const ref = e.id || "-";
    const v = Number(e.value);
    if (e.value === "" || !Number.isFinite(v)) {
      items.push({ ref, detail: `参数值“${e.value}”不是有限数字` });
    } else if (e.type === "R" && v <= 0) {
      items.push({ ref, detail: "电阻值必须大于 0" });
    }
    if (!e.node_p || !idSet.has(e.node_p))
      items.push({ ref, detail: `起始端 p 节点“${e.node_p || "空"}”不在节点表中` });
    if (!e.node_n || !idSet.has(e.node_n))
      items.push({ ref, detail: `终止端 n 节点“${e.node_n || "空"}”不在节点表中` });
    if (e.node_p && e.node_n && e.node_p === e.node_n)
      items.push({ ref, detail: "元件两端不能接在同一节点上" });
  });

  if (items.length) {
    showErrors([{ code: "validation", message: "输入校验未通过", items }]);
    return false;
  }
  return true;
}

/* ------------------------------ 数字格式 ------------------------------ */
function fmt(x) {
  if (!Number.isFinite(x)) return String(x);
  if (x === 0) return "0";
  const ax = Math.abs(x);
  if (ax >= 1e6 || ax < 1e-4) return x.toExponential(4);
  return String(parseFloat(x.toPrecision(6)));
}

/* ------------------------------ 结果渲染 ------------------------------ */
function renderResults(r) {
  // 节点电压
  const vtb = $("#voltage-table tbody");
  vtb.innerHTML = "";
  r.node_voltages.forEach((row) => {
    const neg = row.voltage < 0;
    const grounded = row.id === r.ground;
    const tr = el("tr", { class: [grounded ? "row-ground" : "", neg ? "row-neg" : ""].join(" ").trim() });
    tr.appendChild(el("td", {}, [
      document.createTextNode(row.id),
      grounded ? el("span", { class: "badge", text: "⏚ 地", style: "margin-left:6px" }) : null,
    ].filter(Boolean)));
    const td = el("td");
    const span = el("span", { class: neg ? "num-neg" : "", text: fmt(row.voltage) });
    td.appendChild(span);
    tr.appendChild(td);
    vtb.appendChild(tr);
  });

  // 支路
  const btb = $("#branch-table tbody");
  btb.innerHTML = "";
  r.branches.forEach((b) => {
    const reversed = b.current < 0;
    const source = b.type !== "R";
    const tr = el("tr", { class: [source ? "row-source" : "", reversed ? "row-neg" : ""].join(" ").trim() });
    tr.appendChild(el("td", { text: b.id }));
    tr.appendChild(
      el("td", {}, [el("span", { class: `badge badge-${b.type.toLowerCase()}`, text: b.type })])
    );
    tr.appendChild(el("td", { text: `${b.node_p} → ${b.node_n}` }));
    tr.appendChild(el("td", { text: `${fmt(b.value)} ${TYPE_UNIT[b.type]}` }));
    const curTd = el("td");
    curTd.appendChild(el("span", { class: reversed ? "num-neg" : "", text: fmt(b.current) }));
    tr.appendChild(curTd);
    tr.appendChild(
      el("td", {
        text: b.current === 0 ? "—" : reversed ? `${b.node_n} → ${b.node_p}` : `${b.node_p} → ${b.node_n}`,
      })
    );
    const pTd = el("td");
    pTd.appendChild(el("span", { class: b.power < 0 ? "num-neg" : "", text: fmt(b.power) }));
    pTd.appendChild(
      document.createTextNode(b.power < 0 ? "（发出）" : b.power > 0 ? "（吸收）" : "")
    );
    tr.appendChild(pTd);
    btb.appendChild(tr);
  });

  // 功率平衡
  const pb = r.power_balance;
  const ok = pb.error_rel <= POWER_OK_REL;
  const card = $("#power-card");
  card.innerHTML = "";
  const stat = (k, v, cls = "") =>
    el("div", { class: "power-stat" }, [
      el("span", { class: "k", text: k }),
      el("span", { class: "v " + cls, text: v }),
    ]);
  card.appendChild(stat("总发出功率", fmt(pb.supplied) + " W"));
  card.appendChild(stat("总吸收功率", fmt(pb.absorbed) + " W"));
  card.appendChild(stat("绝对平衡误差", fmt(pb.error_abs) + " W", ok ? "" : "num-neg"));
  card.appendChild(stat("相对误差", fmt(pb.error_rel * 100) + " %", ok ? "" : "num-neg"));
  card.appendChild(
    el("div", { class: "power-balance-row " + (ok ? "ok" : "bad"),
      text: ok ? "✔ 功率平衡校核通过（KCL/KVL 自洽）" : "✖ 功率不平衡，结果不可信" })
  );

  $("#error-panel").classList.add("hidden");
  $("#placeholder").classList.add("hidden");
  $("#result-area").classList.remove("hidden");
}

/* ------------------------------ 提交求解 ------------------------------ */
$("#btn-solve").addEventListener("click", async () => {
  clearFeedback();
  if (!clientValidate()) return;

  const payload = {
    ground: state.ground,
    nodes: state.nodes.map((n) => n.id),
    elements: state.elements.map((e) => ({
      id: e.id,
      type: e.type,
      node_p: e.node_p,
      node_n: e.node_n,
      value: Number(e.value),
    })),
  };

  let resp;
  try {
    resp = await fetch("/api/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    showErrors([{ code: "network", message: `网络请求失败：${err}`, items: [] }]);
    return;
  }

  let data;
  try {
    data = await resp.json();
  } catch (err) {
    showErrors([{ code: "server", message: "服务端返回了无法解析的内容", items: [] }]);
    return;
  }

  if (resp.ok && data.ok) {
    renderResults(data);
    showWarnings(data.warnings);
    // 求解成功：启用敏感度表单并按最近一次求解填充目标下拉
    lastSolve = data;
    $("#sens-quantity").value = sensView.quantity || "voltage";
    setSensitivityFormEnabled(true);
    resetSensitivityTargets();
  } else {
    showErrors(data.errors || [{ code: "server", message: "求解失败", items: [] }]);
    // 求解失败时旧结果仍按“节点表变更”规则处理，这里仅禁用敏感度入口
    lastSolve = null;
    setSensitivityFormEnabled(false);
  }
});

/* ------------------------------ 两节点端口等效 ------------------------------ */
const PORT_STAGE_LABEL = {
  open_circuit: "阶段 1 · 求开路电压",
  equivalent_resistance: "阶段 2 · 求等效电阻（独立源置零 + 1 A 测试电流）",
};

function showTheveninFeedback(errors) {
  const fb = $("#thevenin-feedback");
  fb.innerHTML = "";
  (errors || []).forEach((g) => {
    const group = el("div", { class: "err-group" });
    const tag = g.stage && PORT_STAGE_LABEL[g.stage]
      ? `［${PORT_STAGE_LABEL[g.stage]}］`
      : "";
    group.appendChild(el("div", { class: "err-title", text: `● ${tag}${g.message}` }));
    if (g.items && g.items.length) {
      const ul = el("ul");
      g.items.forEach((item) => {
        if (item.ref) {
          ul.appendChild(
            el("li", {}, [
              el("span", { class: "err-ref", text: item.ref + "：" }),
              document.createTextNode(item.detail),
            ])
          );
        } else if (item.nodes) {
          ul.appendChild(
            el("li", {}, [
              document.createTextNode("子网节点："),
              el("span", { class: "err-nodes", text: item.nodes.join("、") }),
            ])
          );
        } else {
          ul.appendChild(el("li", { text: item.detail || JSON.stringify(item) }));
        }
      });
      group.appendChild(ul);
    }
    fb.appendChild(group);
  });
  $("#thevenin-result").classList.add("hidden");
  fb.classList.remove("hidden");
}

function clearTheveninFeedback() {
  const fb = $("#thevenin-feedback");
  fb.classList.add("hidden");
  fb.innerHTML = "";
}

function renderTheveninResult(r) {
  const p = r.port.node_p;
  const n = r.port.node_n;
  const card = $("#thevenin-card");
  card.innerHTML = "";

  const stat = (k, v, cls = "") =>
    el("div", { class: "power-stat" }, [
      el("span", { class: "k", text: k }),
      el("span", { class: "v " + cls, text: v }),
    ]);

  card.appendChild(
    stat("端口方向", `${p} → ${n}`, "")
  );
  card.appendChild(
    stat("开路电压 V_oc", `${fmt(r.open_circuit_voltage)} V`,
      r.open_circuit_voltage < 0 ? "num-neg" : "")
  );
  card.appendChild(
    stat("等效电阻 R_eq", `${fmt(r.equivalent_resistance)} Ω`, "")
  );
  const vocNeg = r.open_circuit_voltage < 0;
  card.appendChild(
    el("div", { class: "thv-eq" }, [
      document.createTextNode("等效电路：端口 "),
      el("b", { text: `${p} → ${n}` }),
      document.createTextNode(" 间为 "),
      el("b", { class: vocNeg ? "num-neg" : "",
        text: `${fmt(r.open_circuit_voltage)} V` }),
      document.createTextNode(
        vocNeg
          ? ` 理想电压源（实际正极在 n 端 ${n}）串联 `
          : " 理想电压源（正极在 p 端）串联 "
      ),
      el("b", { text: `${fmt(r.equivalent_resistance)} Ω` }),
      document.createTextNode("（1 A 测试电流由 p 端注入网络、从 n 端抽出）。"),
    ])
  );

  if (r.warnings && r.warnings.length) {
    const ul = el("ul", { class: "thv-warn" });
    r.warnings.forEach((w) => ul.appendChild(el("li", { text: "⚠ " + w })));
    card.appendChild(ul);
  }

  $("#thevenin-feedback").classList.add("hidden");
  $("#thevenin-result").classList.remove("hidden");
}

$("#btn-thevenin").addEventListener("click", async () => {
  clearTheveninFeedback();

  const ids = nodeIds();
  const localProblems = [];
  // 只做端口层面的本地预检；网络本身的合法性交给后端，保证错误带阶段标记
  if (!portSel.p) localProblems.push("未选择端口正端 p");
  if (!portSel.n) localProblems.push("未选择端口负端 n");
  if (portSel.p && !ids.includes(portSel.p))
    localProblems.push(`端口正端“${portSel.p}”不在节点表中`);
  if (portSel.n && !ids.includes(portSel.n))
    localProblems.push(`端口负端“${portSel.n}”不在节点表中`);
  if (portSel.p && portSel.n && portSel.p === portSel.n)
    localProblems.push("端口的 p、n 端必须选择不同节点");
  if (localProblems.length) {
    showTheveninFeedback([
      { code: "port_validation", message: "端口参数未通过校验：" + localProblems.join("；"), items: [] },
    ]);
    return;
  }

  const btn = $("#btn-thevenin");
  btn.disabled = true;
  btn.textContent = "计算中…";
  try {
    const payload = buildPayload();
    payload.port = { node_p: portSel.p, node_n: portSel.n };

    let resp;
    try {
      resp = await fetch("/api/thevenin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      showTheveninFeedback([{ code: "network", message: `网络请求失败：${err}`, items: [] }]);
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch (err) {
      showTheveninFeedback([{ code: "server", message: "服务端返回了无法解析的内容", items: [] }]);
      return;
    }

    if (resp.ok && data.ok) {
      theveninResult = data;
      renderTheveninResult(data);
    } else {
      theveninResult = null;
      showTheveninFeedback(
        data.errors || [{ code: "server", message: "端口等效计算失败", items: [] }]
      );
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "🔌 计算端口等效";
  }
});

/* ------------------------------ 元件敏感度分析 ------------------------------ */
const SENS_STAGE_LABEL = {
  baseline: "基准网络求解",
};
const SENS_SIDE_LABEL = { plus: "正扰动 +h", minus: "负扰动 −h", both: "正/负扰动" };

function showSensitivityFeedback(errors) {
  const fb = $("#sensitivity-feedback");
  fb.innerHTML = "";
  (errors || []).forEach((g) => {
    const group = el("div", { class: "err-group" });
    const tag = g.stage && SENS_STAGE_LABEL[g.stage] ? `［${SENS_STAGE_LABEL[g.stage]}］` : "";
    group.appendChild(el("div", { class: "err-title", text: `● ${tag}${g.message}` }));
    if (g.items && g.items.length) {
      const ul = el("ul");
      g.items.forEach((item) => {
        if (item.ref) {
          ul.appendChild(
            el("li", {}, [
              el("span", { class: "err-ref", text: item.ref + "：" }),
              document.createTextNode(item.detail),
            ])
          );
        } else if (item.nodes) {
          ul.appendChild(
            el("li", {}, [
              document.createTextNode("子网节点："),
              el("span", { class: "err-nodes", text: item.nodes.join("、") }),
            ])
          );
        } else {
          ul.appendChild(el("li", { text: item.detail || JSON.stringify(item) }));
        }
      });
      group.appendChild(ul);
    }
    fb.appendChild(group);
  });
  $("#sensitivity-result").classList.add("hidden");
  fb.classList.remove("hidden");
}

function clearSensitivityFeedback() {
  const fb = $("#sensitivity-feedback");
  fb.classList.add("hidden");
  fb.innerHTML = "";
}

function validateSensitivityForm() {
  const items = [];
  const quantity = $("#sens-quantity").value;
  const target = $("#sens-target").value;
  const ratioPctRaw = $("#sens-ratio").value;
  const ratioPct = Number(ratioPctRaw);

  if (!lastSolve) items.push("请先点击“校核求解”并成功求解当前网络");
  if (quantity !== "voltage" && quantity !== "current")
    items.push("目标类型必须是节点电压或支路电流");
  if (!target) {
    items.push(quantity === "current" ? "请选择目标支路" : "请选择目标节点");
  } else if (quantity === "voltage" && !nodeIds().includes(target)) {
    items.push(`目标节点“${target}”不在节点表中`);
  } else if (quantity === "current" && !state.elements.some((e) => e.id === target)) {
    items.push(`目标支路“${target}”不在元件表中`);
  }
  if (ratioPctRaw === "" || !Number.isFinite(ratioPct)) {
    items.push(`相对扰动比例“${ratioPctRaw}”必须是数字`);
  } else if (ratioPct < 0.01 || ratioPct > 10) {
    items.push("相对扰动比例必须在 0.01%～10% 之间");
  }

  if (items.length) {
    showSensitivityFeedback([
      { code: "sensitivity_validation", message: "敏感度参数未通过校验：" + items.join("；"), items: [] },
    ]);
    return null;
  }
  return { quantity, target, ratio: ratioPct / 100 };
}

$("#btn-sensitivity").addEventListener("click", async () => {
  clearSensitivityFeedback();
  const spec = validateSensitivityForm();
  if (!spec) return;

  const btn = $("#btn-sensitivity");
  btn.disabled = true;
  btn.textContent = "计算中（每个元件需求解两次）…";
  try {
    const payload = buildPayload();
    payload.sensitivity = spec;

    let resp;
    try {
      resp = await fetch("/api/sensitivity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      showSensitivityFeedback([{ code: "network", message: `网络请求失败：${err}`, items: [] }]);
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch (err) {
      showSensitivityFeedback([{ code: "server", message: "服务端返回了无法解析的内容", items: [] }]);
      return;
    }

    if (resp.ok && data.ok) {
      sensitivityResult = data;
      renderSensitivityResult(data);
    } else {
      sensitivityResult = null;
      $("#sensitivity-result").classList.add("hidden");
      showSensitivityFeedback(
        data.errors || [{ code: "server", message: "敏感度分析失败", items: [] }]
      );
    }
  } finally {
    btn.disabled = !lastSolve;
    btn.textContent = "🎯 计算元件敏感度";
  }
});

function renderSensitivityResult(r) {
  const meta = r.sensitivity;
  const ratioPct = meta.ratio * 100;
  const failedCount = r.counts.failed;
  $("#sensitivity-meta").textContent =
    `目标：${meta.target_name} = ${fmt(meta.target_base)} ${meta.target_unit}　` +
    `扰动 ±${fmt(ratioPct)}%　共 ${r.counts.total} 个元件` +
    (failedCount ? `（成功 ${r.counts.ok}，失败 ${failedCount}）` : "");

  // 概要行
  const summary = $("#sensitivity-summary");
  summary.innerHTML = "";
  const chip = (k, v, cls = "") =>
    el("span", { class: "sens-chip" }, [
      el("span", { class: "k", text: k }),
      el("span", { class: "v " + cls, text: v }),
    ]);
  summary.appendChild(chip("分析目标", `${meta.target_name}`));
  summary.appendChild(chip("基准值 F₀", `${fmt(meta.target_base)} ${meta.target_unit}`));
  summary.appendChild(chip("相对扰动 h", `±${fmt(ratioPct)}%`));
  summary.appendChild(
    chip("成功 / 失败", `${r.counts.ok} / ${failedCount}`, failedCount ? "num-neg" : "")
  );

  const tbody = $("#sensitivity-table tbody");
  tbody.innerHTML = "";
  const ranked = r.elements.filter((e) => e.status === "ok" && e.sensitivity !== null);
  const maxAbs = ranked.reduce((m, e) => Math.max(m, Math.abs(e.sensitivity)), 0);

  r.elements.forEach((e) => {
    const tr = el("tr", { class: e.status === "ok" ? "" : "row-fail" });

    // 排名
    tr.appendChild(el("td", { text: e.rank === null ? "—" : String(e.rank) }));
    // 元件
    const idTd = el("td");
    idTd.appendChild(el("b", { text: e.id }));
    idTd.appendChild(el("div", { class: "sens-sub", text: `${e.node_p} → ${e.node_n}` }));
    tr.appendChild(idTd);
    // 类型
    tr.appendChild(
      el("td", {}, [el("span", { class: `badge badge-${e.type.toLowerCase()}`, text: e.type })])
    );
    // 基准参数
    tr.appendChild(el("td", { text: `${fmt(e.base_value)} ${TYPE_UNIT[e.type] || ""}`.trim() }));

    if (e.status !== "ok") {
      // 失败项：扰动值 / 导数 / 灵敏度列合并展示“—”，最后一列列结构化原因
      tr.appendChild(el("td", { class: "num-neg", text: "—" }));
      tr.appendChild(el("td", { class: "num-neg", text: "—" }));
      tr.appendChild(el("td", { class: "num-neg", text: "—" }));
      tr.appendChild(el("td", { class: "num-neg", text: "—" }));
      const stTd = el("td");
      stTd.appendChild(
        el("span", { class: `status-tag status-err`, text: `失败${e.side ? `（${SENS_SIDE_LABEL[e.side] || e.side}）` : ""}` })
      );
      const ul = el("ul", { class: "point-errors" });
      (e.errors || []).forEach((g) => {
        const sideTag = g.side && SENS_SIDE_LABEL[g.side] ? `［${SENS_SIDE_LABEL[g.side]}］` : "";
        if (g.items && g.items.length) {
          g.items.forEach((item) =>
            ul.appendChild(
              el("li", {
                text: sideTag + (item.ref ? `${item.ref}：${item.detail}` : item.detail || g.message),
              })
            )
          );
        } else {
          ul.appendChild(el("li", { text: sideTag + g.message }));
        }
      });
      stTd.appendChild(ul);
      tr.appendChild(stTd);
      tbody.appendChild(tr);
      return;
    }

    // 负扰动 / 正扰动目标值（标题附带扰动后的参数值）
    const valUnit = TYPE_UNIT[e.type] || "";
    const minusTd = el("td");
    minusTd.appendChild(
      el("span", { class: e.target_minus < 0 ? "num-neg" : "", text: fmt(e.target_minus) })
    );
    minusTd.title = `参数 p(1−h) = ${fmt(e.value_minus)} ${valUnit}`;
    minusTd.appendChild(el("div", { class: "sens-sub", text: `p=${fmt(e.value_minus)} ${valUnit}` }));
    tr.appendChild(minusTd);

    const plusTd = el("td");
    plusTd.appendChild(
      el("span", { class: e.target_plus < 0 ? "num-neg" : "", text: fmt(e.target_plus) })
    );
    plusTd.title = `参数 p(1+h) = ${fmt(e.value_plus)} ${valUnit}`;
    plusTd.appendChild(el("div", { class: "sens-sub", text: `p=${fmt(e.value_plus)} ${valUnit}` }));
    tr.appendChild(plusTd);

    // 导数（有量纲：目标单位 / 参数单位）
    tr.appendChild(
      el("td", {
        class: e.derivative < 0 ? "num-neg" : "",
        text: `${fmt(e.derivative)} ${meta.target_unit}/${valUnit || "?"}`,
      })
    );

    // 归一化灵敏度：数值 + 横向条形
    const sTd = el("td");
    if (e.sensitivity === null) {
      sTd.appendChild(el("span", { class: "num-neg", text: "—" }));
      sTd.appendChild(el("div", { class: "sens-sub", text: "目标基准为 0，仅可看导数" }));
    } else {
      const s = e.sensitivity;
      sTd.appendChild(
        el("span", {
          class: "sens-val " + (s < 0 ? "num-neg" : ""),
          text: fmt(s),
        })
      );
      const widthPct = maxAbs > 0 ? Math.max(2, Math.abs(s) / maxAbs * 100) : 0;
      const bar = el("div", { class: "sens-bar" }, [
        el("div", {
          class: "sens-bar-fill " + (s < 0 ? "sens-bar-neg" : "sens-bar-pos"),
          style: `width:${widthPct.toFixed(1)}%`,
        }),
      ]);
      sTd.appendChild(bar);
      sTd.appendChild(
        el("div", { class: "sens-sub", text: `参数 +1% → 目标约 ${fmt(s)}%` })
      );
    }
    tr.appendChild(sTd);

    tr.appendChild(
      el("td", {}, [el("span", { class: "status-tag status-ok", text: "成功" })])
    );
    tbody.appendChild(tr);
  });

  $("#sensitivity-feedback").classList.add("hidden");
  $("#sensitivity-result").classList.remove("hidden");
}

/* ------------------------------ 单参数扫描 ------------------------------ */
function showSweepError(message) {
  const fb = $("#sweep-feedback");
  fb.innerHTML = "";
  fb.appendChild(el("div", { class: "err-title", text: `● ${message}` }));
  fb.classList.remove("hidden");
}

function clearSweepFeedback() {
  const fb = $("#sweep-feedback");
  fb.classList.add("hidden");
  fb.innerHTML = "";
}

function buildPayload() {
  return {
    ground: state.ground,
    nodes: state.nodes.map((n) => n.id),
    elements: state.elements.map((e) => ({
      id: e.id,
      type: e.type,
      node_p: e.node_p,
      node_n: e.node_n,
      value: Number(e.value),
    })),
  };
}

// 把后端错误分组拍平成一行行文本，用于扫描反馈与采样点明细
function flattenErrors(errors) {
  const lines = [];
  (errors || []).forEach((g) => {
    if (g.items && g.items.length) {
      g.items.forEach((item) => lines.push(errorItemText(g, item)));
    } else {
      lines.push(g.message);
    }
  });
  return lines;
}

function errorItemText(group, item) {
  if (item.ref) return `${item.ref}：${item.detail}`;
  if (item.nodes) return `${group.message}：子网节点 ${item.nodes.join("、")}`;
  return item.detail || group.message;
}

function validateSweepForm() {
  const items = [];
  const elementId = $("#sweep-element").value;
  const elem = state.elements.find((e) => e.id === elementId);
  if (!elementId || !elem) items.push("请先在元件表中选择一个扫描元件");

  const startRaw = $("#sweep-start").value;
  const endRaw = $("#sweep-end").value;
  const pointsRaw = $("#sweep-points").value;
  const start = Number(startRaw);
  const end = Number(endRaw);
  const points = Number(pointsRaw);

  if (startRaw === "" || !Number.isFinite(start)) items.push(`起值“${startRaw}”不是有限数字`);
  if (endRaw === "" || !Number.isFinite(end)) items.push(`止值“${endRaw}”不是有限数字`);
  if (Number.isFinite(start) && Number.isFinite(end) && start === end)
    items.push("起值与止值不能相同，扫描区间至少包含两个不同的采样值");
  if (
    elem && elem.type === "R" && Number.isFinite(start) && Number.isFinite(end)
    && (start <= 0 || end <= 0)
  ) {
    items.push(`电阻扫描范围包含非正阻值：起值（${startRaw}）与止值（${endRaw}）都必须大于 0`);
  }
  if (pointsRaw === "" || !Number.isInteger(points))
    items.push(`采样点数“${pointsRaw}”必须是整数`);
  else if (points < SWEEP_MIN_POINTS || points > SWEEP_MAX_POINTS)
    items.push(`采样点数必须在 ${SWEEP_MIN_POINTS}～${SWEEP_MAX_POINTS} 之间（当前：${points}）`);

  if (items.length) {
    showSweepError("扫描参数未通过校验：" + items.join("；"));
    return null;
  }
  return { element_id: elementId, start, end, points };
}

$("#btn-sweep").addEventListener("click", async () => {
  clearSweepFeedback();
  const spec = validateSweepForm();
  if (!spec) return;

  const btn = $("#btn-sweep");
  btn.disabled = true;
  btn.textContent = "扫描中…";
  try {
    const payload = buildPayload();
    payload.sweep = spec;

    let resp;
    try {
      resp = await fetch("/api/sweep", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      showSweepError(`网络请求失败：${err}`);
      return;
    }

    let data;
    try {
      data = await resp.json();
    } catch (err) {
      showSweepError("服务端返回了无法解析的内容");
      return;
    }

    if (resp.ok && data.ok) {
      sweepResult = data;
      // 沿用上一次的观测量类型；目标若已不在清单中则重选
      sweepView = { quantity: sweepView.quantity, target: null };
      $("#sweep-quantity").value = sweepView.quantity;
      renderSweepResult();
    } else {
      const lines = flattenErrors(data.errors);
      showSweepError(lines.length ? lines.join("；") : "扫描请求被拒绝");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "📈 开始参数扫描";
  }
});

/* ------------------------------ 扫描结果渲染 ------------------------------ */
$("#sweep-quantity").addEventListener("change", (ev) => {
  if (!sweepResult) return;
  sweepView.quantity = ev.target.value;
  sweepView.target = null;
  // 只重渲染，不重新请求
  renderSweepResult();
});

$("#sweep-target").addEventListener("change", (ev) => {
  if (!sweepResult) return;
  sweepView.target = Number(ev.target.value);
  drawSweepChart();
  renderSweepTable();
});

function targetList() {
  return sweepView.quantity === "voltage" ? sweepResult.nodes : sweepResult.branches;
}

function targetLabel(index) {
  const list = targetList();
  if (sweepView.quantity === "voltage") {
    return `节点 ${list[index]} 电压`;
  }
  const b = list[index];
  return `支路 ${b.id} 电流（${b.node_p}→${b.node_n}）`;
}

function targetUnit() {
  return sweepView.quantity === "voltage" ? "V" : "A";
}

function pointValue(rec, index) {
  const arr = sweepView.quantity === "voltage" ? rec.voltages : rec.currents;
  return arr ? arr[index] : null;
}

function renderSweepResult() {
  const meta = sweepResult.sweep;
  $("#sweep-param-head").textContent = `${meta.element_id} 参数值 (${meta.unit || "—"})`;
  $("#sweep-value-head").textContent =
    sweepView.quantity === "voltage" ? "节点电压 (V)" : "支路电流 (A)";
  $("#sweep-target-label").textContent = sweepView.quantity === "voltage" ? "节点" : "支路";

  // 重建目标下拉
  const sel = $("#sweep-target");
  sel.innerHTML = "";
  const list = targetList();
  list.forEach((item, i) => {
    const text = sweepView.quantity === "voltage"
      ? item
      : `${item.id}（${item.type} ${item.node_p}→${item.node_n}）`;
    sel.appendChild(el("option", { value: String(i), text }));
  });
  if (sweepView.target === null || sweepView.target >= list.length) {
    sweepView.target = 0;
  }
  sel.value = String(sweepView.target);

  const okCount = sweepResult.points.filter((p) => p.status === "ok").length;
  const failCount = sweepResult.points.length - okCount;
  $("#sweep-meta").textContent =
    `${meta.element_id}：${fmt(meta.start)} → ${fmt(meta.end)} ${meta.unit || ""}，` +
    `${meta.points} 点（成功 ${okCount}${failCount ? `，失败 ${failCount}` : ""}）`;

  drawSweepChart();
  renderSweepTable();
  $("#sweep-result").classList.remove("hidden");
}

function tickFmt(x) {
  if (!Number.isFinite(x)) return String(x);
  if (Math.abs(x) < 1e-13) return "0";
  const ax = Math.abs(x);
  if (ax >= 1e5 || ax < 1e-3) return x.toExponential(2);
  return String(parseFloat(x.toPrecision(4)));
}

// 生成约 5 个覆盖 lo..hi 的“好看”刻度
function niceTicks(lo, hi) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo === hi) return [lo];
  const span = hi - lo;
  const step0 = span / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const ticks = [];
  for (let t = Math.ceil(lo / step - 1e-9); t * step <= hi + 1e-9 * span; t += 1) {
    ticks.push(t * step);
  }
  return ticks;
}

function padDomain(lo, hi) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (lo === hi) {
    const d = Math.abs(lo) || 1;
    return [lo - d * 0.1, hi + d * 0.1];
  }
  const pad = (hi - lo) * 0.05;
  return [lo - pad, hi + pad];
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function drawSweepChart() {
  const box = $("#sweep-chart");
  box.innerHTML = "";
  const W = 680;
  const H = 360;
  const M = { top: 34, right: 24, bottom: 52, left: 72 };
  const pw = W - M.left - M.right;
  const ph = H - M.top - M.bottom;

  const records = sweepResult.points;
  const xs = records.map((r) => r.value);
  const ys = records.map((r) => pointValue(r, sweepView.target));
  const okYs = ys.filter((y) => y !== null && Number.isFinite(y));

  if (okYs.length === 0) {
    box.appendChild(el("div", {
      class: "chart-empty",
      text: "所有采样点均求解失败，无可绘制曲线；失败原因见下方采样点明细。",
    }));
    return;
  }

  const [xLo0, xHi0] = [Math.min(...xs), Math.max(...xs)];
  const [yLo0, yHi0] = [Math.min(...okYs), Math.max(...okYs)];
  const [xLo, xHi] = padDomain(xLo0, xHi0);
  const [yLo, yHi] = padDomain(yLo0, yHi0);
  const sx = (x) => M.left + ((x - xLo) / (xHi - xLo)) * pw;
  const sy = (y) => M.top + ph - ((y - yLo) / (yHi - yLo)) * ph;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "sweep-chart", role: "img" });

  // y 向网格线 + 刻度
  niceTicks(yLo, yHi).forEach((t) => {
    const y = sy(t);
    svg.appendChild(svgEl("line", {
      x1: M.left, y1: y, x2: W - M.right, y2: y,
      class: t === 0 && yLo < 0 && yHi > 0 ? "zero" : "gridline",
    }));
    const txt = svgEl("text", { x: M.left - 7, y: y + 3.5, "text-anchor": "end" });
    txt.textContent = tickFmt(t);
    svg.appendChild(txt);
  });

  // x 轴刻度
  niceTicks(xLo, xHi).forEach((t) => {
    const x = sx(t);
    svg.appendChild(svgEl("line", { x1: x, y1: M.top, x2: x, y2: M.top + ph, class: "gridline" }));
    const txt = svgEl("text", { x, y: M.top + ph + 15, "text-anchor": "middle" });
    txt.textContent = tickFmt(t);
    svg.appendChild(txt);
  });

  // 坐标轴
  svg.appendChild(svgEl("line", {
    x1: M.left, y1: M.top + ph, x2: W - M.right, y2: M.top + ph, class: "axis",
  }));
  svg.appendChild(svgEl("line", {
    x1: M.left, y1: M.top, x2: M.left, y2: M.top + ph, class: "axis",
  }));

  // 标题
  const meta = sweepResult.sweep;
  const title = svgEl("text", { x: M.left, y: 17, class: "title" });
  title.textContent = `${targetLabel(sweepView.target)} 随 ${meta.element_id} 参数变化`;
  svg.appendChild(title);

  // x 轴标题
  const xTitle = svgEl("text", { x: M.left + pw / 2, y: H - 10, "text-anchor": "middle" });
  xTitle.textContent = `${meta.element_id} 参数值${meta.unit ? ` (${meta.unit})` : ""}`;
  svg.appendChild(xTitle);

  // y 轴标题（竖排）
  const yTitle = svgEl("text", {
    x: 17, y: M.top + ph / 2, "text-anchor": "middle",
    transform: `rotate(-90 17 ${M.top + ph / 2})`,
  });
  yTitle.textContent = sweepView.quantity === "voltage" ? "节点电压 (V)" : "支路电流 (A)";
  svg.appendChild(yTitle);

  // 折线（仅成功点参与，失败处自然断开）
  let d = "";
  let pen = false;
  records.forEach((r) => {
    const y = pointValue(r, sweepView.target);
    if (y === null || !Number.isFinite(y)) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${sx(r.value).toFixed(2)},${sy(y).toFixed(2)} `;
    pen = true;
  });
  svg.appendChild(svgEl("path", { d: d.trim(), class: "curve" }));

  // 数据点
  records.forEach((r) => {
    const y = pointValue(r, sweepView.target);
    const cx = sx(r.value);
    if (y === null || !Number.isFinite(y)) {
      // 失败点画在图底部，用 ✕ 标记
      const cy = M.top + ph - 4;
      svg.appendChild(svgEl("line", {
        x1: cx - 4, y1: cy - 4, x2: cx + 4, y2: cy + 4,
        stroke: "var(--err)", "stroke-width": 1.8,
      }));
      svg.appendChild(svgEl("line", {
        x1: cx - 4, y1: cy + 4, x2: cx + 4, y2: cy - 4,
        stroke: "var(--err)", "stroke-width": 1.8,
      }));
      return;
    }
    const cy = sy(y);
    const tip = svgEl("title");
    tip.textContent =
      `${meta.element_id}=${fmt(r.value)}${meta.unit ? ` ${meta.unit}` : ""}\n` +
      `${targetLabel(sweepView.target)} = ${fmt(y)} ${targetUnit()}`;
    const dot = svgEl("circle", { cx, cy, r: 3.4, class: "pt-ok" });
    dot.appendChild(tip);
    svg.appendChild(dot);
  });

  box.appendChild(svg);
}

function renderSweepTable() {
  const tbody = $("#sweep-table tbody");
  tbody.innerHTML = "";
  const meta = sweepResult.sweep;
  sweepResult.points.forEach((r) => {
    const y = pointValue(r, sweepView.target);
    const ok = r.status === "ok";
    const tr = el("tr", { class: ok ? "" : "row-fail" });
    tr.appendChild(el("td", { text: String(r.index + 1) }));
    tr.appendChild(el("td", { text: `${fmt(r.value)} ${meta.unit || ""}`.trim() }));

    const valTd = el("td");
    if (ok && y !== null && Number.isFinite(y)) {
      valTd.appendChild(
        el("span", { class: y < 0 ? "num-neg" : "", text: `${fmt(y)} ${targetUnit()}` })
      );
    } else {
      valTd.appendChild(el("span", { class: "num-neg", text: "—" }));
      const ul = el("ul", { class: "point-errors" });
      flattenErrors(r.errors).forEach((line) => ul.appendChild(el("li", { text: line })));
      valTd.appendChild(ul);
    }
    tr.appendChild(valTd);

    const stTd = el("td");
    stTd.appendChild(
      el("span", {
        class: `status-tag ${ok ? "status-ok" : "status-err"}`,
        text: ok ? "成功" : "失败",
      })
    );
    tr.appendChild(stTd);
    tbody.appendChild(tr);
  });
}

/* ------------------------------ SPICE 网表互换 ------------------------------ */
const SPICE_SAMPLE = [
  "* 分压电路：10 V 电压源 + 10 kΩ / 20 kΩ（节点 0 接地）",
  "V1 1 0 10",
  "R1 1 2 10k",
  "R2 2 0 20k ; 行尾注释：下管",
  "I1 2 0 1m   ; m 按 1e-3（毫）解析",
  "R3 2 3 3.3meg",
  "V2 3 0 -1.5e0",
].join("\n");

function showSpiceFeedback(kind, errors, summary) {
  const fb = $("#spice-feedback");
  fb.innerHTML = "";
  fb.classList.remove("hidden", "spice-ok");
  const titleColor = kind === "ok" ? "var(--ok)" : "var(--err)";

  if (summary) {
    const head = el("div", { class: "err-title", text: `● ${summary}` });
    head.style.color = titleColor;
    fb.appendChild(head);
  }

  (errors || []).forEach((g) => {
    const group = el("div", { class: "err-group" });
    group.appendChild(el("div", { class: "err-title line-title", text: `● ${g.message}` }));
    if (g.items && g.items.length) {
      const ul = el("ul");
      g.items.forEach((item) => {
        const li = el("li");
        if (item.line != null) {
          li.appendChild(el("span", { class: "spice-line-ref", text: `第 ${item.line} 行：` }));
        } else if (item.ref) {
          li.appendChild(el("span", { class: "err-ref", text: item.ref + "：" }));
        }
        li.appendChild(document.createTextNode(item.detail || JSON.stringify(item)));
        ul.appendChild(li);
      });
      group.appendChild(ul);
    }
    fb.appendChild(group);
  });
  if (kind === "ok") fb.classList.add("spice-ok");
}

function clearSpiceFeedback() {
  const fb = $("#spice-feedback");
  fb.classList.add("hidden");
  fb.classList.remove("spice-ok");
  fb.innerHTML = "";
}

$("#btn-spice-sample").addEventListener("click", () => {
  $("#spice-text").value = SPICE_SAMPLE;
  clearSpiceFeedback();
});

$("#btn-spice-import").addEventListener("click", async () => {
  clearSpiceFeedback();
  const text = $("#spice-text").value;
  if (!text.trim()) {
    showSpiceFeedback("err", [{ code: "spice_import", message: "网表文本为空", items: [] }]);
    return;
  }

  let resp;
  try {
    resp = await fetch("/api/spice/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ netlist: text }),
    });
  } catch (err) {
    showSpiceFeedback("err", [{ code: "network", message: `网络请求失败：${err}`, items: [] }]);
    return;
  }

  let data;
  try {
    data = await resp.json();
  } catch (err) {
    showSpiceFeedback("err", [{ code: "server", message: "服务端返回了无法解析的内容", items: [] }]);
    return;
  }

  if (!resp.ok || !data.ok) {
    // 任一错误都不改写当前电路：state 原封不动
    showSpiceFeedback("err", data.errors || [{ code: "server", message: "导入失败", items: [] }]);
    return;
  }

  // 成功：整体替换当前电路（接地固定为推导要求的 0）
  state = {
    ground: data.ground,
    nodes: data.nodes.map((id) => ({ id })),
    elements: data.elements.map((e) => ({ ...e, value: String(e.value) })),
  };
  resetSweep();
  resetThevenin();
  resetSensitivity();
  portSel = {
    p: data.nodes.length > 1 ? (data.nodes.find((n) => n !== "0") || data.nodes[0]) : "",
    n: "0",
  };
  nodeSig = "";
  renderAll();
  $("#result-area").classList.add("hidden");
  $("#placeholder").classList.add("hidden");

  const c = data.counts;
  showSpiceFeedback(
    "ok",
    [],
    `✔ 导入成功：${c.elements} 个元件、${c.nodes} 个节点（接地 0）；` +
    `忽略 ${c.blank} 个空行、${c.comment} 行注释。当前表格已替换为导入的电路。`
  );
});

$("#btn-spice-export").addEventListener("click", async () => {
  clearSpiceFeedback();
  const payload = buildPayload();

  let resp;
  try {
    resp = await fetch("/api/spice/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    showSpiceFeedback("err", [{ code: "network", message: `网络请求失败：${err}`, items: [] }]);
    return;
  }

  let data;
  try {
    data = await resp.json();
  } catch (err) {
    showSpiceFeedback("err", [{ code: "server", message: "服务端返回了无法解析的内容", items: [] }]);
    return;
  }

  if (!resp.ok || !data.ok) {
    showSpiceFeedback("err", data.errors || [{ code: "server", message: "导出失败", items: [] }]);
    return;
  }

  $("#spice-text").value = data.netlist;
  const notes = [];
  if (data.ground_renamed_from) {
    notes.push(`接地节点“${data.ground_renamed_from}”已在网表中映射为 0`);
  }
  if (data.omitted_isolated_nodes && data.omitted_isolated_nodes.length) {
    notes.push(`未被任何元件引用的孤立节点已省略：${data.omitted_isolated_nodes.join("、")}`);
  }
  notes.push("导出文本可直接再次导入并还原等价电路");
  showSpiceFeedback("ok", [], `✔ 导出成功：${data.element_count} 行元件。${notes.join("；")}。`);
});

renderAll();
