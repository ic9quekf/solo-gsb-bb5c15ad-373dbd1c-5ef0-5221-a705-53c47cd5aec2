"""直流网络校核器 FastAPI 入口。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .sensitivity import (
    SENSITIVITY_MAX_RATIO,
    SENSITIVITY_MIN_RATIO,
    sensitivity,
)
from .solver import CircuitError, MAX_ELEMENTS, MAX_NODES, solve
from .spice import SUFFIX_FACTORS, build_netlist, parse_netlist
from .sweep import SWEEP_MAX_POINTS, SWEEP_MIN_POINTS, sweep
from .thevenin import thevenin_equivalent

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="直流网络校核器", version="1.4.0")


async def _read_json_object(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """读取请求体并要求顶层为 JSON 对象，失败时返回 (None, 错误响应)。"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return None, JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [
                    {"code": "bad_json", "message": "请求体不是合法的 JSON", "items": []}
                ],
            },
        )
    if not isinstance(payload, dict):
        return None, JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [
                    {"code": "bad_payload", "message": "请求体应为对象", "items": []}
                ],
            },
        )
    return payload, None


@app.post("/api/solve")
async def api_solve(request: Request) -> JSONResponse:
    payload, bad = await _read_json_object(request)
    if bad is not None:
        return bad

    try:
        result = solve(payload)
    except CircuitError as exc:
        # 422：网络可解析但无法得到唯一物理解（输入校验 / 结构矛盾）
        return JSONResponse(status_code=422, content=exc.to_dict())
    return JSONResponse(status_code=200, content=result)


@app.post("/api/sweep")
async def api_sweep(request: Request) -> JSONResponse:
    payload, bad = await _read_json_object(request)
    if bad is not None:
        return bad

    try:
        result = sweep(payload)
    except CircuitError as exc:
        # 422：扫描参数（元件 / 范围 / 点数）非法，无法开始扫描
        return JSONResponse(status_code=422, content=exc.to_dict())
    return JSONResponse(status_code=200, content=result)


@app.post("/api/thevenin")
async def api_thevenin(request: Request) -> JSONResponse:
    payload, bad = await _read_json_object(request)
    if bad is not None:
        return bad

    try:
        result = thevenin_equivalent(payload)
    except CircuitError as exc:
        # 422：端口参数非法，或开路电压 / 等效电阻某一阶段无唯一解（分组带 stage）
        return JSONResponse(status_code=422, content=exc.to_dict())
    return JSONResponse(status_code=200, content=result)


@app.post("/api/sensitivity")
async def api_sensitivity(request: Request) -> JSONResponse:
    payload, bad = await _read_json_object(request)
    if bad is not None:
        return bad

    try:
        result = sensitivity(payload)
    except CircuitError as exc:
        # 422：敏感度参数非法（sensitivity_validation），或基准网络无唯一解
        # （沿用 /api/solve 的错误分组并带 stage:"baseline"）；
        # 单个元件扰动失败不报错，内联在 200 响应的 elements[].errors 中
        return JSONResponse(status_code=422, content=exc.to_dict())
    return JSONResponse(status_code=200, content=result)


@app.post("/api/spice/import")
async def api_spice_import(request: Request) -> JSONResponse:
    payload, bad = await _read_json_object(request)
    if bad is not None:
        return bad

    netlist_text = payload.get("netlist")
    if not isinstance(netlist_text, str):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "bad_field",
                    "message": "缺少字符串类型的 netlist 字段（SPICE 网表文本）",
                    "items": [],
                }],
            },
        )

    try:
        result = parse_netlist(netlist_text)
    except CircuitError as exc:
        # 422：网表可读取但存在行级错误 / 缺少节点 0 接地；前端不应用结果改写电路
        return JSONResponse(status_code=422, content=exc.to_dict())
    return JSONResponse(status_code=200, content=result)


@app.post("/api/spice/export")
async def api_spice_export(request: Request) -> JSONResponse:
    payload, bad = await _read_json_object(request)
    if bad is not None:
        return bad

    try:
        result = build_netlist(payload)
    except CircuitError as exc:
        # 422：网络结构非法，或接地→0 映射与既有节点 0 冲突
        return JSONResponse(status_code=422, content=exc.to_dict())
    return JSONResponse(status_code=200, content=result)


@app.get("/api/meta")
async def api_meta() -> dict[str, Any]:
    return {
        "max_nodes": MAX_NODES,
        "max_elements": MAX_ELEMENTS,
        "sweep_min_points": SWEEP_MIN_POINTS,
        "sweep_max_points": SWEEP_MAX_POINTS,
        "sensitivity_min_ratio": SENSITIVITY_MIN_RATIO,
        "sensitivity_max_ratio": SENSITIVITY_MAX_RATIO,
        "element_types": [
            {"type": "R", "name": "电阻", "unit": "Ω"},
            {"type": "I", "name": "独立电流源", "unit": "A"},
            {"type": "V", "name": "独立电压源", "unit": "V"},
        ],
        "spice": {
            "ground_node": "0",
            "value_suffixes": {k: v for k, v in SUFFIX_FACTORS.items()},
        },
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
