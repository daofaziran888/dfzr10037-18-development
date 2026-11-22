"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class ImportLine(BaseModel):
    line_no: int = Field(ge=1)
    key: str = Field(min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)
    # 显式隔离：登记导入任务时已经判定为坏行的行；缺省时由校验规则判定
    quarantine: bool = False
    reason: str | None = None


class InitialImportPayload(BaseModel):
    task_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    target_graph: str = Field(min_length=1)
    key_field: str = Field(default="实体编号")
    lines: list[ImportLine]


class ReplacementImportPayload(BaseModel):
    task_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    target_graph: str = Field(min_length=1)
    key_field: str = Field(default="实体编号")
    lines: list[ImportLine]


class ResubmitRow(BaseModel):
    key: str = Field(min_length=1)
    values: dict[str, Any]


class ResubmitPayload(BaseModel):
    task_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1)
    key_field: str = Field(default="实体编号")
    rows: list[ResubmitRow]


class AdjudicatePayload(BaseModel):
    decision: str  # adopt：采用补交值 / reject：驳回，维持替代行生效值
    remark: str | None = None
