"""批量导入专用接口：初始导入、替代导入、跳过行补交回填与冲突裁决。

状态流转与业务判断全部放在 BatchLoadService，路由层只做参数装配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import (
    AdjudicatePayload,
    InitialImportPayload,
    ReplacementImportPayload,
    ResubmitPayload,
)
from app.services import service_for
from app.services.quality_audit import QualityAuditService

router = APIRouter(prefix="/api/batch_load", tags=["批量导入-跳过行补交"])


def _batch_service():
    return service_for("batch_load")


@router.post("/imports/initial")
def initial_import(payload: InitialImportPayload) -> dict[str, Any]:
    """初始导入：好行写入图谱，坏行隔离并生成审计整改待办。"""
    return _batch_service().initial_import(payload)


@router.post("/imports/replacement")
def replacement_import(payload: ReplacementImportPayload) -> dict[str, Any]:
    """替代值导入：另一文件先写入同键数据，并核销对应隔离问题。"""
    return _batch_service().replacement_import(payload)


@router.post("/resubmits")
def resubmit(payload: ResubmitPayload) -> dict[str, Any]:
    """上传隔离行修订文件进行补交回填（同内容文件重复提交只生效一次）。"""
    return _batch_service().resubmit(payload)


@router.get("/resubmits")
def list_resubmits(task_id: str | None = None) -> dict[str, Any]:
    service = _batch_service()
    return {"items": service.list_resubmit_files(task_id)}


@router.get("/resubmits/{file_record_id}/rows")
def list_resubmit_rows(file_record_id: int) -> dict[str, Any]:
    return {"items": _batch_service().list_resubmit_rows(file_record_id)}


@router.get("/quarantine")
def list_quarantine(task_id: str | None = None, status: str | None = None) -> dict[str, Any]:
    """隔离行清单：身份字段含原文件行号，可回指初始导入的坏行。"""
    return {"items": _batch_service().list_quarantine(task_id, status)}


@router.post("/quarantine/{quarantine_id}/adjudicate")
def adjudicate(quarantine_id: int, payload: AdjudicatePayload) -> dict[str, Any]:
    """对冲突待裁决的补交变更做人工裁决。"""
    quarantine, message = _batch_service().adjudicate(
        quarantine_id, payload.decision.strip(), payload.remark)
    if quarantine is None:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "entry": quarantine}


@router.get("/graph/facts")
def list_facts(target_graph: str | None = None, entity_key: str | None = None) -> dict[str, Any]:
    """图谱生效数据：每条属性带当前版本与实际取值来源。"""
    return {"items": _batch_service().list_facts(target_graph, entity_key)}


@router.get("/graph/facts/versions")
def fact_versions(target_graph: str, entity_key: str, field: str) -> dict[str, Any]:
    """单个生效属性的版本流水，核对每个版本的实际来源文件与行号。"""
    return {"items": _batch_service().fact_versions(target_graph, entity_key, field)}


@router.get("/rectifications")
def list_rectifications(
    task_id: str | None = None,
    resolution: str | None = None,
    include_closed: bool = True,
) -> dict[str, Any]:
    """质量审计整改待办：区分未修正 / 补交采用 / 替代行解决 / 冲突待裁决。"""
    audit = service_for("quality_audit")
    assert isinstance(audit, QualityAuditService)
    return {"items": audit.list_rectifications(
        task_id=task_id, resolution=resolution, include_closed=include_closed)}


@router.get("/rectifications/summary")
def rectification_summary(task_id: str | None = None) -> dict[str, Any]:
    audit = service_for("quality_audit")
    assert isinstance(audit, QualityAuditService)
    return audit.rectification_summary(task_id)
