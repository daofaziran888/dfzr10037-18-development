"""质量审计领域服务：审计报告骨架之外，承接批量导入跳过行的整改核销。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.batch_load import (
    Q_ADOPTED,
    Q_CONFLICT,
    Q_OPEN,
    Q_REPLACED,
    T_RECTIFICATIONS,
)
from app.store import store


class QualityAuditService(BaseModuleService):
    MODULE = "quality_audit"
    ENTITY = "审计报告"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["报告编号", "审计范围", "实体准确率", "关系完整率", "冗余度", "更新时效", "合规结论", "审计时间"]
    REQUIRED_FIELDS = ["报告编号", "审计范围", "实体准确率"]
    STATUS_ORDER = ["待审计", "审计中", "已通过", "需整改"]
    ACTION_RULES = {"发起审计": "审计中", "确认通过": "已通过", "指派整改": "需整改"}
    NEGATIVE_ACTIONS = []

    def list_rectifications(
        self,
        *,
        task_id: str | None = None,
        resolution: str | None = None,
        include_closed: bool = True,
    ) -> list[dict[str, Any]]:
        """审计整改待办：由补交结果实时同步，按核销口径区分来源。"""
        rows = list(store.domain_rows(T_RECTIFICATIONS))
        if task_id:
            rows = [row for row in rows if row["task_id"] == task_id]
        if resolution:
            rows = [row for row in rows if row["resolution"] == resolution]
        if not include_closed:
            rows = [row for row in rows if not row.get("closed")]
        return rows

    def rectification_summary(self, task_id: str | None = None) -> dict[str, Any]:
        """核销统计：未修正、补交采用、替代行解决、冲突待裁决分别计数。

        冲突待裁决的补交行未真正进入生效数据，不计入已修正口径。
        """
        rows = self.list_rectifications(task_id=task_id)
        return {
            "total": len(rows),
            Q_OPEN: sum(1 for row in rows if row["resolution"] == Q_OPEN),
            Q_ADOPTED: sum(1 for row in rows if row["resolution"] == Q_ADOPTED),
            Q_REPLACED: sum(1 for row in rows if row["resolution"] == Q_REPLACED),
            Q_CONFLICT: sum(1 for row in rows if row["resolution"] == Q_CONFLICT),
            "closed": sum(1 for row in rows if row.get("closed")),
            "open": sum(1 for row in rows if not row.get("closed")),
        }
