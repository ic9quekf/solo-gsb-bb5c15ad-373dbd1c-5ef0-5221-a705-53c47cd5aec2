# 直流网络校核器

一个用于直流电阻网络建模与校核的 Web 应用。前端为原生 HTML/CSS/JS，后端使用
**Python FastAPI + NumPy**，通过**改进节点分析（Modified Nodal Analysis, MNA）**
组装并求解线性方程。

## 功能

- 最多录入 **30 个节点**，以及电阻（R）、独立电流源（I）、独立电压源（V）三类元件，
  可指定任意节点为接地（电位 0 V）。
- 服务端组装 MNA 增广矩阵并求解，返回：
  - 各**节点电压**（含接地节点）；
  - 各**支路方向电流**（支路正方向取 `p → n`，负值时前端反色并标出实际方向）；
  - 全网**功率平衡**信息：总发出 / 总吸收功率、绝对与相对误差，达标时高亮“校核通过”。
- 对无法得到唯一解的网络**不崩溃**，而是区分并列出相关节点 / 元件：
  - **无接地**或接地节点不在节点表中；
  - **浮置子网**：与接地之间仅通过电流源连接（或完全悬空）的连通片，列出其中节点；
  - **理想电压源互相矛盾**：如不同电压的理想电压源并联，列出冲突元件。
- 前端提供三个示例一键载入（正常网络 / 浮置子网 / 电压源冲突）、表格化编辑、
  提交前输入校验、结果与问题行高亮。
- **单参数扫描**：从元件表选择一个元件，给出起值、止值与采样点数（2～101），
  后端在网络副本上逐点等间距替换该元件参数，复用同一套 MNA 求解与结构诊断。
  - 扫描**不会改写当前电路表**；单个采样点求解失败时保留该点的错误原因，
    不中断整次扫描（曲线在失败点处断开）。
  - 结果区用折线图展示指定**节点电压**或**支路电流**随参数的变化，
    图下逐点列出采样值、观测值与成功 / 失败状态。
  - 一次请求返回全部点的全部节点电压与支路电流，切换观测量 / 目标只做本地重绘，
    **不会重复请求**后端。
  - 非法范围（起值或止值不是有限数、起值与止值相同）、电阻起值或止值非正、
    采样点数不在 2～101、扫描元件不存在或编号重复等，均在前后端给出明确提示。
- **两节点端口等效（戴维南）**：从现有节点表选择两个不同节点作为端口的
  正端 `p` 与负端 `n`，后端分两阶段计算并返回：
  - **开路电压 V_oc**：直接求解原网络（端口不挂元件，天然开路），
    取 `V_oc = V(p) − V(n)`；
  - **等效电阻 R_eq**：在网络副本上将独立电压源置零（0 V，短路）、
    独立电流源置零（0 A，开路），再**从 n 向 p 注入 1 A 测试电流**
    （向 p 注入 +1 A、从 n 抽出 1 A），取 `R_eq = V(p) − V(n)`（Ω）。
  - 结果卡显示**端口方向（p → n）、开路电压与等效电阻**及等效电路说明；
    等效结果与接地节点的选择无关。
  - 任一步出现**浮置子网 / 电压源冲突 / 奇异 / 无接地**时，错误分组原样返回
    并带上 `stage`（`open_circuit` 或 `equivalent_resistance`）标记，
    前端按阶段展示，**不展示任何半截伪结果**。
  - 计算在网络副本上进行，不改写元件表；**节点表发生变更（增删 / 改名）后，
    旧的端口等效结果自动失效隐藏**，需重新计算。
- **元件敏感度分析**：在网络**校核求解成功**后，选择一个**节点电压**或
  **支路电流**目标，并给出**相对扰动比例**（`0.01%～10%`）。后端对元件表中的
  **每个 R / I / V 元件**，在网络副本上分别施加 `+h` 与 `−h` 的同幅相对扰动
  （参数由 `p` 变为 `p·(1±h)`），各复用一次 MNA 求解，以**中心差分**

  ```
  dF/dp ≈ ( F(p·(1+h)) − F(p·(1−h)) ) / (2·p·h)
  S     = (p / F₀) · dF/dp          # 归一化（相对）灵敏度
  ```

  计算目标对每个元件参数的灵敏度：`S` 表示元件参数每相对变化 1% 时目标值
  近似相对变化百分之几（负号为反向变化）。
  - 返回每个元件的**正/负扰动目标值、扰动后参数、有量纲导数 dF/dp、
    归一化灵敏度 S 与排名**，结果按 `|S|` 从大到小排序，失败项垫底；
  - **基准网络求解失败**（无接地 / 浮置子网 / 电压源冲突 / 奇异）时整体
    返回 `422`，错误分组带 `stage: "baseline"`；**单个元件的任一扰动失败**
    （如基准参数为 0、扰动后网络无唯一解）时只标记该元件并附带
    `side`（`plus` / `minus` / `both`）的结构化错误，**继续完成其它元件**；
  - 目标基准值 `F₀ = 0` 时无法归一化，`S` 返回 `null`、不参与排名，
    但仍给出有量纲导数；
  - 分析在深拷贝上进行，不改写元件表；**节点表发生变更（增删 / 改名）后
    旧结果自动清空**，且需重新校核求解后才能再次发起分析。
- **SPICE 网表互换**：左侧「SPICE 网表互换」卡片提供网表文本区，支持
  文本 ⇄ 表格双向转换。
  - **导入语法**：忽略空行；第一个非空白字符为 `*` 的行作整行注释；
    `;` 之后的内容作行尾注释。元件行恰为 4 个空白分隔字段：
    `名称 正端 负端 数值`，类型由名称首字母决定（`R / I / V`）。
  - **数值**接受科学计数法（`1.5e3`、`-2.2E-4`）与工程后缀
    `p=1e-12 / n=1e-9 / u=1e-6 / m=1e-3 / k=1e3 / meg=1e6 / g=1e9`
    （大小写不敏感）；**`m` 一律按 1e-3（毫）解析**，`meg` 才是兆。
  - 导入**逐行收集**重复编号、字段数量错误、未知类型、非法数值、
    电阻非正、非法自环（两端同节点）等错误，每条都标明**物理行号**；
    只要存在任一错误即整体拒绝（`422`），**不会改写当前表格电路**。
  - 成功后节点由元件端点按首次出现顺序自动推导，且**必须存在节点 `0`
    作为接地**（否则 `422 spice_no_ground`），导入结果可直接用于求解 /
    扫描 / 戴维南 / 敏感度等全部既有分析。
  - **导出**按元件表顺序生成规范网表（一行 `*` 头注释 + 每行一个元件）。
    当前接地不是 `0` 时，自动把接地节点在网表中映射为 `0`；若网络中
    **另有名为 `0` 的节点**则拒绝导出并说明冲突
    （`422 spice_ground_conflict`）。导出文本可再次导入并还原
    **等价电路**（元件、端点方向、数值、求解结果一致）。

## 元件方向约定

| 类型 | 参数含义 | 方向 / 极性 |
| ---- | -------- | ----------- |
| `R` 电阻 | 电阻值（Ω，> 0） | 支路正方向 `p → n` |
| `I` 电流源 | 电流（A） | 正值表示电流由 `p` 流向 `n` |
| `V` 电压源 | 电动势（V） | `p` 为正极、`n` 为负极，支路电流正方向 `p → n` |

支路功率按**关联参考方向**计算：`P = (V_p − V_n)·I`。`P > 0` 为吸收功率，
`P < 0` 为发出功率；全网吸收功率之和应接近 0。

## 快速启动

需要已安装 Docker（含 Compose 插件）。在项目根目录执行：

```bash
docker compose up --build
```

启动后用浏览器访问：

- **http://localhost:8000**

API：

- `GET  /api/meta`：容量与元件类型元信息（含 `sweep_min_points` /
  `sweep_max_points`，即 2 / 101，以及 `sensitivity_min_ratio` /
  `sensitivity_max_ratio`，即 `0.0001` / `0.1`）；
- `POST /api/solve`：请求体为网络 JSON（见下），成功返回 `200`，
  输入或结构问题返回 `422`，错误按 `no_ground / floating_subnet / voltage_conflict / validation`
  分组列出；
- `POST /api/sweep`：单参数扫描。请求体与 `/api/solve` 相同，另加一个 `sweep` 段
  （见下）；**扫描参数本身非法**（元件缺失 / 重复、范围非法、电阻范围含非正值、
  点数越界等）时整体返回 `422` 且错误码为 `sweep_validation`；扫描可以开始时
  恒返回 `200`，各采样点的失败以 `status: "error"` 内联在结果中，不影响其它点。
- `POST /api/thevenin`：两节点端口戴维南等效。请求体与 `/api/solve` 相同，
  另加一个 `port` 段（见下）。**端口参数本身非法**（缺少 `port` 段、端点不在
  节点表、p 与 n 相同等）时返回 `422` 且错误码为 `port_validation`；端口合法时，
  开路电压（`open_circuit`）或等效电阻（`equivalent_resistance`）任一阶段求解
  失败，同样返回 `422`，错误分组沿用 `/api/solve` 的
  `no_ground / floating_subnet / voltage_conflict / singular / validation` 编码，
  并额外带 `stage` 字段标明失败阶段；成功返回 `200`。
- `POST /api/sensitivity`：元件敏感度分析。请求体与 `/api/solve` 相同，另加一个
  `sensitivity` 段（见下）。**敏感度参数本身非法**（缺少 `sensitivity` 段、
  目标类型不是 `voltage` / `current`、目标节点 / 支路不存在、相对扰动比例不在
  `0.0001～0.1` 等）时返回 `422` 且错误码为 `sensitivity_validation`；参数合法但
  **基准网络无唯一解**时同样返回 `422`，错误分组沿用 `/api/solve` 的编码并带
  `stage: "baseline"`；基准可解时恒返回 `200`，各元件的正/负扰动失败以
  `status: "error"` 与带 `side` 标记的错误分组内联在 `elements[]` 中，不影响其它元件。
- `POST /api/spice/import`：SPICE 网表导入。请求体 `{"netlist": "<文本>"}`；
  缺少字符串 `netlist` 字段返回 `400`。解析成功返回 `200`，响应体即标准网络 JSON
  （`ground` 固定为 `"0"`、`nodes` 由端点按首次出现顺序推导、`elements` 含解析后的
  浮点数值），另带 `counts`（总行数 / 元件数 / 节点数 / 空行数 / 注释行数）。
  网表为空时错误码 `spice_empty`；存在任一行级错误时错误码 `spice_import`，
  `items[]` 每项含 `line`（1 起始物理行号，结构级问题为 `null`）、`ref` 与 `detail`；
  缺少节点 `0` 时错误码 `spice_no_ground`。行级错误与缺接地均返回 `422`，
  **不产出电路结果，前端不得据此改写当前表格**。
- `POST /api/spice/export`：SPICE 网表导出。请求体与 `/api/solve` 的网络 JSON
  相同（必需 `ground / nodes / elements`）。成功返回 `200`：
  `{"ok": true, "netlist": "<文本>", "ground": "0",
  "ground_renamed_from": <原接地或 null>, "renamed_nodes": [...],
  "element_count": N, "omitted_isolated_nodes": [...]}`。网络结构非法
  （编号重复 / 端点缺失 / 自环 / 电阻非正 / 接地不存在 / 元件名与类型首字母
  不一致 / 接地未被任何元件引用等）时返回 `422 spice_export`；当前接地非 `0`
  但另有节点 `0` 造成映射冲突时返回 `422 spice_ground_conflict`。
  `GET /api/meta` 的 `spice` 字段同时给出接地节点名（`"0"`）与全部数值后缀倍率。

停止服务：

```bash
docker compose down
```

## 配置

- 容器内固定监听 **8000** 端口，compose 将宿主机同号端口映射进去。
  若宿主机 8000 被占用，可修改 `compose.yaml`：

  ```yaml
  ports:
    - "8080:8000"   # 宿主机端口:容器端口
  ```

- 无其它外部依赖：不使用数据库，数据只存在浏览器页面中，刷新即重置。

## 请求示例

```bash
curl -s -X POST http://localhost:8000/api/solve \
  -H 'Content-Type: application/json' \
  -d '{
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
      {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
      {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}
    ]
  }'
```

## 单参数扫描

### 页面操作

1. 按常规录入网络（可先“载入示例①”），在左下「单参数扫描」卡片的
   「扫描元件」下拉中选择元件表中的一个元件；
2. 填写**起值**、**止值**与**采样点数**（整数，2～101；电阻两端点都必须为正，
   起值与止值不能相同），点击「开始参数扫描」；
3. 右侧出现扫描结果：
   - 顶部下拉可在「节点电压 / 支路电流」之间切换，并选择具体节点 / 支路，
     折线图与明细表即时重绘，**不会重新发起请求**；
   - 折线仅连接成功点，失败采样点在图底以红色 ✕ 标出；
   - 下方明细表逐点给出扫描参数值、当前观测量数值与状态，失败点同时列出
     该点的错误原因（与 `/api/solve` 的错误分组一致）。

扫描期间元件表保持原样；修改电路后需重新点击「开始参数扫描」。

### 请求体与返回结构

在网络 JSON 中追加 `sweep` 段即可：

```bash
curl -s -X POST http://localhost:8000/api/sweep \
  -H 'Content-Type: application/json' \
  -d '{
    "ground": "0",
    "nodes": ["0", "1"],
    "elements": [
      {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
      {"id": "R1", "type": "R", "node_p": "1", "node_n": "0", "value": 10}
    ],
    "sweep": {"element_id": "R1", "start": 10, "end": 100, "points": 11}
  }'
```

返回（`200`）：

```json
{
  "ok": true,
  "sweep": {"element_id": "R1", "type": "R", "unit": "Ω",
            "start": 10.0, "end": 100.0, "points": 11},
  "nodes": ["0", "1"],
  "branches": [{"id": "V1", "type": "V", "node_p": "1", "node_n": "0"}, ...],
  "points": [
    {"index": 0, "value": 10.0, "status": "ok",
     "voltages": [0.0, 10.0], "currents": [-1.0, 1.0]},
    {"index": 5, "value": 55.0, "status": "error",
     "errors": [{"code": "...", "message": "...", "items": []}]}
  ]
}
```

- `points[].voltages` / `points[].currents` 分别与顶层 `nodes` / `branches`
  **按下标对齐**，因此前端切换观测量无需再次请求；失败点只有 `errors`，
  不含这两个数组。
- 当所有采样点都失败时（例如基准网络无接地），`nodes` / `branches`
  由请求体兜底推导，便于前端仍能切换查看目标。
- 扫描参数非法时返回 `422`：

```json
{"ok": false,
 "errors": [{"code": "sweep_validation",
             "message": "扫描参数未通过校验",
             "items": [{"ref": "采样点数", "detail": "采样点数必须在 2～101 之间（当前：102）"}]}]}
```

### 校验规则汇总

| 情形 | 处理 |
| ---- | ---- |
| 扫描元件为空 / 不在元件表 / 编号重复 | 拒绝成扫（`422 sweep_validation`） |
| 起值或止值不是有限实数 | 拒绝成扫 |
| 起值与止值相同 | 拒绝成扫 |
| 扫描电阻且起值或止值 ≤ 0（含跨零区间） | 拒绝成扫 |
| 采样点数不是整数或不在 2～101 | 拒绝成扫 |
| 基准网络或某个取值点结构矛盾 / 数值奇异 | 该点 `status:"error"` 并保留错误，其余点照常求解 |

## 两节点端口等效（戴维南）

### 页面操作

1. 按常规录入网络（可先“载入示例①”），在左下「两节点端口等效（戴维南）」卡片的
   两个下拉中分别选择端口**正端 p** 与**负端 n**（必须是节点表中两个不同节点）；
2. 点击「计算端口等效」，右侧结果卡显示：
   - **端口方向** `p → n`；
   - **开路电压 V_oc (V)**：原网络在该端口的开路电位差 `V(p) − V(n)`；
   - **等效电阻 R_eq (Ω)**：独立源全部置零后，从 n 向 p 注入 1 A 测试电流
     （电流方向 n→p，即向 p 注入 +1 A、从 n 抽出 1 A）
     得到的端口电位差 `V(p) − V(n)`；
   - 一行等效电路文字说明（理想电压源串联等效电阻）。
3. 开路或测试阶段任一阶段失败（无接地、浮置子网、电压源冲突、矩阵奇异）时，
   结果卡不出现，反馈面板按**阶段**（阶段 1 开路电压 / 阶段 2 等效电阻）
   展示与 `/api/solve` 一致的结构化错误，不会给出伪结果。
4. **节点表一旦变更**（添加 / 删除节点、修改节点编号、载入示例、清空），
   旧结果立即失效隐藏；仅修改元件参数 / 接地不影响已选端口，可直接重新计算。

### 请求体与返回结构

在网络 JSON 中追加 `port` 段：

```bash
curl -s -X POST http://localhost:8000/api/thevenin \
  -H 'Content-Type: application/json' \
  -d '{
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
      {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
      {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10},
      {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20}
    ],
    "port": {"node_p": "2", "node_n": "0"}
  }'
```

返回（`200`）：

```json
{
  "ok": true,
  "port": {"node_p": "2", "node_n": "0"},
  "open_circuit_voltage": 6.666666666666667,
  "equivalent_resistance": 6.666666666666664,
  "test_current": 1.0,
  "warnings": []
}
```

（上例：开路为 10 V 在 10 Ω + 20 Ω 上的分压 20/3 V；独立源置零后 V1 短路，
R_eq 为 10 Ω ∥ 20 Ω = 20/3 Ω。）

- 交换 p、n 会使 `open_circuit_voltage` 反号，`equivalent_resistance` 不变。
- 端口取在理想电压源两端时，`equivalent_resistance` 为 `0`。
- 计算在**深拷贝**上进行：原网络中的独立源数值不会被置零，1 A 测试电流源
  （内部编号 `__port_Itest__`，与用户元件撞号时自动避让）也不会残留在元件表中。
- 开路电压阶段的数值警告（如条件数过大）经 `warnings` 返回。

端口参数非法时返回 `422 port_validation`：

```json
{"ok": false,
 "errors": [{"code": "port_validation",
             "message": "端口参数未通过校验",
             "items": [{"ref": "2", "detail": "端口的 p、n 端必须选择不同节点（不能对同一节点取等效）"}]}]}
```

网络在某个阶段无唯一解时，错误分组与 `/api/solve` 完全相同，只是多了 `stage`：

```json
{"ok": false,
 "errors": [{"code": "floating_subnet",
             "stage": "open_circuit",
             "message": "检测到未通过电阻或电压源与接地节点连通的浮置子网……",
             "items": [{"nodes": ["2", "3"]}]}]}
```

### 校验 / 失败规则汇总

| 情形 | 处理 |
| ---- | ---- |
| 缺少 `port` 段 / p 或 n 为空 | 拒绝计算（`422 port_validation`） |
| p 或 n 不在节点表 | 拒绝计算（`port_validation`，指名缺失节点） |
| p 与 n 为同一节点 | 拒绝计算（`port_validation`） |
| 原网络无接地 / 浮置子网 / 电压源冲突 / 奇异 / 输入非法 | `422`，原错误分组 + `stage:"open_circuit"` |
| 置零加测试源后的网络无唯一解（防御性，正常无源网络应可解） | `422`，原错误分组 + `stage:"equivalent_resistance"` |

## 元件敏感度分析

### 页面操作

1. 按常规录入网络（可先“载入示例①”），先点击 **▶ 校核求解** 并成功——
   求解成功后左下「元件敏感度分析」卡片的目标下拉才会启用；
2. 在「目标类型」中选择 **节点电压 (V)** 或 **支路电流 (A)**，并在右侧下拉中
   选定具体**目标节点**或**目标支路**（支路以“编号 + p→n”标注，与支路电流表一致）；
3. 填写**相对扰动比例**（百分数，`0.01%～10%`，默认 `0.1%`；正负两侧同幅），
   点击「计算元件敏感度」。后端对每个 R / I / V 元件各求解两次（共约 2N+1 次 MNA），
   右侧结果表给出：
   - **排名**（按 `|归一化灵敏度|` 从大到小，仅成功且可归一化的元件有排名）；
   - 元件编号 / 类型 / 基准参数；
   - **负扰动目标值 / 正扰动目标值**（副行标出该侧扰动后的参数值 `p·(1∓h)`）；
   - **导数 dF/dp**（中心差分，量纲为“目标单位 / 参数单位”）；
   - **归一化灵敏度 S**：参数每相对变化 1% 时目标近似相对变化的百分数，
     并附按 `|S|` 归一的横向条形（蓝色为正、橙色为负）；
   - 状态与失败原因：失败元件整行标红，错误信息带**正扰动 / 负扰动**侧标记，
     成功元件继续给出完整数值。
4. 顶部概要行显示分析目标、基准值 F₀、扰动比例与成功 / 失败计数；
   **节点表一旦变更**（添加 / 删除节点、修改节点编号、载入示例、清空），
   旧结果立即清空、表单回到禁用状态，需重新校核求解；仅修改元件参数 / 类型 / 端点
   或接地选择不会自动清空，但需重新求解后再分析。

### 计算定义

对元件参数 `p`、目标量 `F`，取相对扰动步长 `h = ratio`，中心差分：

```
dF/dp ≈ ( F(p·(1+h)) − F(p·(1−h)) ) / (2·p·h)
S     = (p / F₀) · dF/dp = (F+ − F−) / (2·h·F₀)
```

- `S > 0`：参数增大时目标增大；`S < 0`：参数增大时目标减小。
  例如分压器 `V₂ = V₁·R₂/(R₁+R₂)`：`S(V₂,R₁) = −R₁/(R₁+R₂)`、
  `S(V₂,R₂) = +R₁/(R₁+R₂)`、`S(V₂,V₁) = 1`。
- `F₀ = 0`（如目标取接地节点电压）时 `S` 无定义，返回 `null` 且不排名，
  仅展示有量纲导数；该类元件排在有灵敏度的元件之后、失败元件之前。
- 元件基准参数为 0（零值电流源 / 电压源）时无法定义相对扰动，该元件直接标记
  `status:"error"`（`code:"zero_parameter"`、`side:"both"`），其余元件照常分析。
- 所有计算在网络**深拷贝**上进行，元件表数值不会被改写。

### 请求体与返回结构

在网络 JSON 中追加 `sensitivity` 段：

```bash
curl -s -X POST http://localhost:8000/api/sensitivity \
  -H 'Content-Type: application/json' \
  -d '{
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
      {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
      {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10},
      {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20}
    ],
    "sensitivity": {"quantity": "voltage", "target": "2", "ratio": 0.001}
  }'
```

返回（`200`，节选）：

```json
{
  "ok": true,
  "sensitivity": {
    "quantity": "voltage", "target": "2",
    "target_name": "节点 2 电压", "target_unit": "V",
    "target_base": 6.666666666666667, "ratio": 0.001
  },
  "elements": [
    {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "index": 0,
     "base_value": 10.0, "status": "ok",
     "value_minus": 9.99, "value_plus": 10.01,
     "target_minus": 6.66, "target_plus": 6.673333333333332,
     "derivative": 0.6666666666665932, "sensitivity": 0.99999999999989,
     "rank": 1}
  ],
  "counts": {"total": 3, "ok": 3, "failed": 0},
  "warnings": []
}
```

- `elements[]` 已按 `|sensitivity|` 降序排列，`rank` 从 1 开始连续编号；
  失败元件与 `sensitivity: null` 的元件 `rank` 为 `null`。
- 成功元件含 `base_value / value_minus / value_plus / target_minus /
  target_plus / derivative / sensitivity` 全部数值字段。
- 失败元件形如：

```json
{"id": "I0", "type": "I", "node_p": "1", "node_n": "0", "index": 0,
 "base_value": 0.0, "status": "error", "side": "both", "rank": null,
 "errors": [{"code": "zero_parameter", "side": "both",
             "message": "元件 I0 的基准参数值为 0，无法施加相对扰动……", "items": []}]}
```

- 敏感度参数非法时返回 `422 sensitivity_validation`：

```json
{"ok": false,
 "errors": [{"code": "sensitivity_validation",
             "message": "敏感度参数未通过校验",
             "items": [{"ref": "相对扰动比例",
                        "detail": "相对扰动比例必须在 0.01%～10% 之间（当前：50%）"}]}]}
```

- 基准网络无唯一解时返回 `422`，错误分组与 `/api/solve` 完全相同，只是多了
  `stage: "baseline"`：

```json
{"ok": false,
 "errors": [{"code": "no_ground", "stage": "baseline",
             "message": "未指定接地节点：节点电压缺少参考点，无法求解", "items": []}]}
```

### 校验 / 失败规则汇总

| 情形 | 处理 |
| ---- | ---- |
| 缺少 `sensitivity` 段 | 拒绝分析（`422 sensitivity_validation`） |
| `quantity` 不是 `voltage` / `current` | 拒绝分析 |
| `target` 为空，或目标节点 / 支路不在网络中 | 拒绝分析（指名目标） |
| `ratio` 不是有限数字（含布尔值） | 拒绝分析 |
| `ratio` 超出 `0.0001～0.1`（即 0.01%～10%） | 拒绝分析 |
| 基准网络无接地 / 浮置子网 / 电压源冲突 / 奇异 / 输入非法 | `422`，原错误分组 + `stage:"baseline"` |
| 元件基准参数为 0，无法相对扰动 | 该元件 `status:"error"`（`zero_parameter`，`side:"both"`），其它继续 |
| 元件正 / 负扰动后网络无唯一解 | 该元件 `status:"error"`，错误分组带 `side:"plus"` / `"minus"`，其它继续 |
| 目标基准值 F₀ = 0 | 元件照常成功，`sensitivity:null`、不排名，仅返回导数 |

## SPICE 网表互换

### 页面操作

1. 在左侧「SPICE 网表互换」卡片的文本区粘贴或编写网表（也可点「填入示例网表」）；
2. 点击 **⇩ 导入网表到表格**：解析成功后节点表 / 元件表 / 接地选择整体替换为
   导入的电路（接地固定为节点 `0`），旧的扫描 / 戴维南 / 敏感度结果自动失效；
   只要存在任一错误，右侧反馈面板按**行号**逐条列出问题，**当前表格保持不变**；
3. 编辑表格后点击 **⇧ 按当前表格生成网表**：文本区显示规范网表；接地不是 `0`
   时自动映射，存在冲突或结构问题时给出说明、不覆盖文本区。
4. 导出的文本直接再次导入，可得到与原电路**求解等价**的网络（节点编号顺序可能
   因端点推导而变化，但元件、方向与数值不变）。

### 网表语法

```
* 以星号开头的行为整行注释（允许前导空白）
V1 1 0 10          ; 名称 正端 负端 数值；分号后为行尾注释
R1 1 2 10k
R2 2 0 4.7k        ; 下管
I1 2 0 1m          ; m = 1e-3（毫）
```

- 空行（含仅空白的行）忽略；`;` 后的内容全部忽略；
- 元件行必须恰好 4 个空白分隔字段；类型由名称首字母决定，仅支持 `R / I / V`
  （大小写不敏感）；电阻值必须为正，电流源 / 电压源数值可正可负；
- 数值语法：十进制数（可带科学计数法 `e/E`），或十进制数紧跟**一个**工程后缀：

  | 后缀 | p | n | u | m | k | meg | g |
  | ---- | -- | -- | -- | -- | -- | --- | -- |
  | 倍率 | 1e-12 | 1e-9 | 1e-6 | **1e-3** | 1e3 | 1e6 | 1e9 |

  后缀大小写不敏感；科学计数法与后缀不能叠加（`1e3k` 非法）；
- 节点名取自端点记号（长度 ≤ 24），按首次出现顺序推导；**必须出现节点 `0`**
  并作为接地；元件两端不能为同一节点（非法自环）；元件编号不可重复；
  元件最多 90 个、推导出的节点最多 30 个。

### 请求示例

```bash
curl -s -X POST http://localhost:8000/api/spice/import \
  -H 'Content-Type: application/json' \
  -d '{"netlist": "* demo\nV1 1 0 10\nR1 1 2 10k\nR2 2 0 20k\n"}'
```

成功返回（`200`）：

```json
{
  "ok": true,
  "ground": "0",
  "nodes": ["1", "0", "2"],
  "elements": [
    {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10.0},
    {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10000.0},
    {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20000.0}
  ],
  "counts": {"lines": 4, "elements": 3, "nodes": 3, "blank": 0, "comment": 1}
}
```

存在行级错误时返回 `422 spice_import`，逐条带行号（一次返回全部错误）：

```json
{"ok": false,
 "errors": [{"code": "spice_import",
             "message": "SPICE 网表导入失败：存在无法解析的行",
             "items": [
               {"ref": "第 2 行", "line": 2,
                "detail": "元件行应有 4 个字段（名称 正端 负端 数值），实际为 3 个：“R1 1 2”"},
               {"ref": "X1", "line": 3,
                "detail": "未知元件类型“X”（名称“X1”首字母），仅支持 R / I / V"}]}]}
```

导出：

```bash
curl -s -X POST http://localhost:8000/api/spice/export \
  -H 'Content-Type: application/json' \
  -d '{
    "ground": "0",
    "nodes": ["0", "1", "2"],
    "elements": [
      {"id": "V1", "type": "V", "node_p": "1", "node_n": "0", "value": 10},
      {"id": "R1", "type": "R", "node_p": "1", "node_n": "2", "value": 10000},
      {"id": "R2", "type": "R", "node_p": "2", "node_n": "0", "value": 20000}
    ]
  }'
```

返回（`200`）：

```json
{"ok": true,
 "ground": "0",
 "ground_renamed_from": null,
 "renamed_nodes": [],
 "element_count": 3,
 "omitted_isolated_nodes": [],
 "netlist": "* 直流网络校核器导出的 SPICE 网表（R/I/V：名称 正端 负端 数值；节点 0 为接地）\nV1 1 0 10\nR1 1 2 10000\nR2 2 0 20000\n"}
```

### 校验 / 失败规则汇总

| 情形 | 处理 |
| ---- | ---- |
| 请求体缺少字符串 `netlist` / 不是合法 JSON 对象 | `400`（`bad_field` / `bad_json`） |
| 元件行字段数 ≠ 4 | `422 spice_import`，带行号 |
| 名称首字母不是 R / I / V（或名称非法） | `422 spice_import`，带行号 |
| 数值无法识别 / 非有限数 / 电阻 ≤ 0 | `422 spice_import`，带行号 |
| 元件编号重复（逐字比较） | `422 spice_import`，带行号 |
| 正端与负端为同一节点（非法自环） | `422 spice_import`，带行号 |
| 端点不含节点 `0` | `422 spice_no_ground` |
| 网表中没有任何元件行 | `422 spice_empty` |
| 超过 90 元件 / 30 节点 | `422 spice_import`（结构级条目） |
| 导出网络结构非法（重复 / 缺失 / 自环 / 电阻非正等） | `422 spice_export` |
| 元件名首字母与其类型不符（无法往返） | `422 spice_export` |
| 接地为空 / 不在节点表 / 未被任何元件引用 | `422 spice_export` |
| 接地非 `0` 且网络中另有节点 `0` | `422 spice_ground_conflict`，拒绝映射 |
| 未被任何元件端点引用的孤立节点 | 导出时省略，并在 `omitted_isolated_nodes` 注明 |

## 本地开发（不使用 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## 目录结构

```
.
├── app/
│   ├── main.py            # FastAPI 路由（solve / sweep / thevenin / sensitivity / spice 导入导出）与静态文件
│   ├── solver.py          # MNA 组装、结构诊断与求解
│   ├── sweep.py           # 单参数扫描：校验 + 逐点复用 solve
│   ├── thevenin.py        # 两节点端口戴维南等效：开路电压 + 置零注入测试电流
│   ├── sensitivity.py     # 元件敏感度：逐元件 ±h 扰动 + 中心差分归一化灵敏度
│   ├── spice.py           # SPICE 网表解析（注释 / 工程后缀 / 逐行错误）与规范导出（接地 0 映射）
│   └── static/
│       ├── index.html     # 页面结构（含扫描、敏感度、端口等效、SPICE 互换表单与结果区）
│       ├── style.css      # 样式与高亮（含扫描曲线、敏感度条形、端口等效、SPICE 面板样式）
│       └── app.js         # 表格编辑、校验、单次求解、扫描、敏感度、端口等效与 SPICE 交互
├── test_solver.py         # 求解器冒烟测试：python3 test_solver.py
├── test_sweep.py          # 扫描冒烟测试：python3 test_sweep.py
├── test_thevenin.py       # 端口等效冒烟测试：python3 test_thevenin.py
├── test_sensitivity.py    # 敏感度冒烟测试：python3 test_sensitivity.py
├── test_spice.py          # SPICE 网表互换冒烟测试：python3 test_spice.py
├── Dockerfile
├── compose.yaml
└── requirements.txt
```
