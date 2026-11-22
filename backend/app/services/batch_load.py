"""批量导入领域服务。

在通用任务骨架之外，承载跳过行隔离与补交回填的真实业务差异：

- 初始导入的坏行进入隔离表，隔离身份保留「原文件行号」，可回指；
- 生效图谱按「实体键 + 属性」存事实，每次写入追加版本并记录来源文件/行号；
- 补交文件按内容指纹去重，同一份补交文件重复提交只生效一次；
- 补交值与已生效的替代行属性冲突时不覆盖生效数据，冲突进入待裁决，
  但补交变更完整保留，隔离问题可先行关闭；
- 隔离行状态与质量审计整改项联动，区分未修正 / 补交采用 / 替代行解决 / 冲突待裁决。
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# ==== 领域专用表名（store._domain_tables，不进入 22 个资源模块） ====
T_QUARANTINE = "bl_quarantine_rows"        # 隔离行（跳过行）
T_GRAPH_FACTS = "bl_graph_facts"           # 图谱生效事实（实体键 + 属性）
T_FACT_VERSIONS = "bl_fact_versions"       # 生效事实的版本与取值来源（追加）
T_RESUBMIT_FILES = "bl_resubmit_files"     # 补交文件登记（内容指纹幂等）
T_RESUBMIT_ROWS = "bl_resubmit_rows"       # 补交行处理明细
T_RECTIFICATIONS = "bl_rectifications"     # 质量审计整改项

# 隔离行状态
Q_OPEN = "未修正"
Q_ADOPTED = "补交采用"
Q_REPLACED = "替代行解决"
Q_CONFLICT = "冲突待裁决"
Q_RESOLVED_STATUSES = {Q_ADOPTED, Q_REPLACED}

# 补交行处理结果
R_ADOPTED = "补交采用"
R_CONFLICT = "冲突待裁决"
R_REPLACED = "替代行解决"
R_DUPLICATE = "重复提交未生效"
R_UNMATCHED = "无匹配隔离行"

# 裁决决定
D_ADOPT = "adopt"
D_REJECT = "reject"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    """补交文件内容指纹：规范化后哈希，改名重传同内容视为同一份文件。"""
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BatchLoadService(BaseModuleService):
    MODULE = "batch_load"
    ENTITY = "导入任务"
    OPERATION_TERM = "导入"
    LIST_FIELDS = ["任务编号", "数据文件", "目标图谱", "映射规则", "校验模式", "导入行数", "跳过行数", "导入时间"]
    REQUIRED_FIELDS = ["任务编号", "数据文件", "目标图谱"]
    STATUS_ORDER = ["待执行", "导入中", "已完成", "部分跳过"]
    ACTION_RULES = {"启动导入": "导入中", "暂停导入": "已完成", "标记跳过": "部分跳过"}
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _fact_key(self, target_graph: str, entity_key: str) -> str:
        return f"{target_graph}::{entity_key}"

    def _write_fact(
        self,
        *,
        task_id: str,
        target_graph: str,
        entity_key: str,
        field: str,
        value: Any,
        source_file: str,
        source_line: int,
        source_kind: str,
    ) -> dict[str, Any]:
        """写一条生效属性，并追加只增不改的版本记录，保证取值来源可核对。"""
        fk = self._fact_key(target_graph, entity_key)
        facts = store.domain_rows(T_GRAPH_FACTS)
        fact = next(
            (item for item in facts if item["fact_key"] == fk and item["field"] == field),
            None,
        )
        version_no = 1
        if fact is None:
            fact = {
                "id": store.next_id(T_GRAPH_FACTS),
                "fact_key": fk,
                "target_graph": target_graph,
                "entity_key": entity_key,
                "field": field,
                "value": value,
                "version": version_no,
                "source_file": source_file,
                "source_line": source_line,
                "source_kind": source_kind,
                "updated_at": _now(),
            }
            facts.append(fact)
        else:
            version_no = int(fact.get("version", 1)) + 1
            fact.update({
                "value": value,
                "version": version_no,
                "source_file": source_file,
                "source_line": source_line,
                "source_kind": source_kind,
                "updated_at": _now(),
            })
        store.domain_rows(T_FACT_VERSIONS).append({
            "id": store.next_id(T_FACT_VERSIONS),
            "fact_key": fk,
            "target_graph": target_graph,
            "entity_key": entity_key,
            "field": field,
            "value": value,
            "version": version_no,
            "source_file": source_file,
            "source_line": source_line,
            "source_kind": source_kind,
            "written_at": _now(),
        })
        return fact

    def _current_fact(self, target_graph: str, entity_key: str, field: str) -> dict[str, Any] | None:
        fk = f"{target_graph}::{entity_key}"
        for item in store.domain_rows(T_GRAPH_FACTS):
            if item["fact_key"] == fk and item["field"] == field:
                return item
        return None

    def _latest_quarantine(self, task_id: str, entity_key: str) -> dict[str, Any] | None:
        """取该实体键最近一条隔离行。

        已核销（替代行解决/补交采用）的隔离行也允许补交命中：
        若补交值与图谱生效值冲突，重新打开为冲突待裁决并保留变更。
        """
        candidates = [
            row for row in store.domain_rows(T_QUARANTINE)
            if row["task_id"] == task_id and row["entity_key"] == entity_key
        ]
        return candidates[-1] if candidates else None

    def _sync_rectification(self, quarantine: dict[str, Any]) -> dict[str, Any] | None:
        """隔离行状态变化后同步审计整改项，审计核销口径与隔离状态保持一致。"""
        rects = store.domain_rows(T_RECTIFICATIONS)
        rect = next(
            (item for item in rects if item["quarantine_id"] == quarantine["id"]),
            None,
        )
        if rect is None:
            return None
        rect["resolution"] = quarantine["status"]
        rect["closed"] = quarantine["status"] in Q_RESOLVED_STATUSES
        rect["updated_at"] = _now()
        return rect

    def _set_quarantine_status(self, quarantine: dict[str, Any], status: str) -> None:
        quarantine["status"] = status
        quarantine["updated_at"] = _now()
        self._sync_rectification(quarantine)

    # ------------------------------------------------------------------
    # 初始导入：好行进图谱，坏行进隔离并生成审计整改待办
    # ------------------------------------------------------------------
    def initial_import(self, payload: Any) -> dict[str, Any]:
        imported: list[dict[str, Any]] = []
        quarantined: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for line in payload.lines:
            entity_key = str(line.key).strip()
            values = dict(line.values)
            reason = (line.reason or "").strip()
            # 缺省校验：显式标记隔离、缺少键值或属性值为空都判为坏行
            invalid = line.quarantine or not entity_key or any(
                str(v).strip() == "" for v in values.values()
            )
            if invalid and not reason:
                reason = "必填属性为空" if not line.quarantine else "上游校验未通过"

            if invalid:
                q_row = {
                    "id": store.next_id(T_QUARANTINE),
                    "task_id": payload.task_id,
                    "target_graph": payload.target_graph,
                    "entity_key": entity_key or f"line-{line.line_no}",
                    "key_field": payload.key_field,
                    "source_file": payload.file_name,
                    "origin_line_no": line.line_no,  # 隔离身份回指原文件行号
                    "raw_values": copy.deepcopy(values),
                    "reason": reason,
                    "status": Q_OPEN,
                    "created_at": _now(),
                    "updated_at": _now(),
                }
                store.domain_rows(T_QUARANTINE).append(q_row)
                store.domain_rows(T_RECTIFICATIONS).append({
                    "id": store.next_id(T_RECTIFICATIONS),
                    "task_id": payload.task_id,
                    "quarantine_id": q_row["id"],
                    "entity_key": q_row["entity_key"],
                    "origin_line_no": line.line_no,
                    "source_file": payload.file_name,
                    "resolution": Q_OPEN,
                    "closed": False,
                    "created_at": _now(),
                    "updated_at": _now(),
                })
                quarantined.append(q_row)
                continue

            seen_keys.add(entity_key)
            written = [
                self._write_fact(
                    task_id=payload.task_id,
                    target_graph=payload.target_graph,
                    entity_key=entity_key,
                    field=field,
                    value=value,
                    source_file=payload.file_name,
                    source_line=line.line_no,
                    source_kind="初始导入",
                )
                for field, value in values.items()
            ]
            imported.append({"line_no": line.line_no, "key": entity_key, "fields": len(written)})
        return {
            "ok": True,
            "task_id": payload.task_id,
            "file_name": payload.file_name,
            "imported_rows": len(imported),
            "quarantined_rows": len(quarantined),
            "imported": imported,
            "quarantined": quarantined,
        }

    # ------------------------------------------------------------------
    # 替代值导入：另一个文件先把同键数据写进图谱，并核销对应隔离问题
    # ------------------------------------------------------------------
    def replacement_import(self, payload: Any) -> dict[str, Any]:
        written: list[dict[str, Any]] = []
        resolved: list[dict[str, Any]] = []
        for line in payload.lines:
            entity_key = str(line.key).strip()
            if not entity_key or any(str(v).strip() == "" for v in line.values.values()):
                # 替代文件自身的坏行不允许进入图谱，直接忽略并在结果中说明
                continue
            for field, value in line.values.items():
                self._write_fact(
                    task_id=payload.task_id,
                    target_graph=payload.target_graph,
                    entity_key=entity_key,
                    field=field,
                    value=value,
                    source_file=payload.file_name,
                    source_line=line.line_no,
                    source_kind="替代导入",
                )
            written.append({"line_no": line.line_no, "key": entity_key, "fields": len(line.values)})

            quarantine = self._latest_quarantine(payload.task_id, entity_key)
            if quarantine is not None and quarantine["status"] == Q_OPEN:
                # 替代行已让该实体在图谱中生效，隔离问题按「替代行解决」核销，
                # 不把它误记成补交修正。
                self._set_quarantine_status(quarantine, Q_REPLACED)
                resolved.append({
                    "quarantine_id": quarantine["id"],
                    "entity_key": entity_key,
                    "origin_line_no": quarantine["origin_line_no"],
                    "resolution": Q_REPLACED,
                })
        return {
            "ok": True,
            "task_id": payload.task_id,
            "file_name": payload.file_name,
            "written_rows": len(written),
            "resolved_quarantine": resolved,
            "written": written,
        }

    # ------------------------------------------------------------------
    # 补交上传：修订后的隔离行回填，幂等 + 冲突保留待裁决
    # ------------------------------------------------------------------
    def resubmit(self, payload: Any) -> dict[str, Any]:
        normalized = [{"key": str(row.key).strip(), "values": dict(row.values)} for row in payload.rows]
        fingerprint = _fingerprint(normalized)

        # 幂等：同一任务下相同内容的补交文件只生效一次
        for record in store.domain_rows(T_RESUBMIT_FILES):
            if record["task_id"] == payload.task_id and record["fingerprint"] == fingerprint:
                record["resubmit_count"] = int(record.get("resubmit_count", 1)) + 1
                record["last_submitted_at"] = _now()
                return {
                    "ok": True,
                    "duplicate": True,
                    "message": "该补交文件内容已提交过，本次重复提交未再生效应",
                    "file_record_id": record["id"],
                    "fingerprint": fingerprint,
                    "rows": [
                        copy.deepcopy(item)
                        for item in store.domain_rows(T_RESUBMIT_ROWS)
                        if item["file_record_id"] == record["id"]
                    ],
                    "summary": record["summary"],
                }

        file_record = {
            "id": store.next_id(T_RESUBMIT_FILES),
            "task_id": payload.task_id,
            "file_name": payload.file_name,
            "key_field": payload.key_field,
            "fingerprint": fingerprint,
            "resubmit_count": 1,
            "submitted_at": _now(),
            "last_submitted_at": _now(),
            "summary": {},
        }
        store.domain_rows(T_RESUBMIT_FILES).append(file_record)

        summary = {R_ADOPTED: 0, R_CONFLICT: 0, R_REPLACED: 0, R_UNMATCHED: 0}
        row_results: list[dict[str, Any]] = []
        for item in normalized:
            entity_key = item["key"]
            values = item["values"]
            quarantine = self._latest_quarantine(payload.task_id, entity_key)
            if quarantine is None:
                result = R_UNMATCHED
                detail = {"conflict_fields": [], "adopted_fields": [], "kept_fields": []}
            else:
                detail = self._apply_resubmit_row(file_record, quarantine, payload, entity_key, values)
                result = detail["result"]
            summary[result] = summary.get(result, 0) + 1
            row_results.append({
                "id": store.next_id(T_RESUBMIT_ROWS),
                "file_record_id": file_record["id"],
                "task_id": payload.task_id,
                "entity_key": entity_key,
                "quarantine_id": quarantine["id"] if quarantine else None,
                "origin_line_no": quarantine["origin_line_no"] if quarantine else None,
                "submitted_values": copy.deepcopy(values),
                "result": result,
                "detail": detail,
                "submitted_at": _now(),
            })
        store.domain_rows(T_RESUBMIT_ROWS).extend(row_results)
        file_record["summary"] = summary
        return {
            "ok": True,
            "duplicate": False,
            "file_record_id": file_record["id"],
            "fingerprint": fingerprint,
            "rows": row_results,
            "summary": summary,
        }

    def _apply_resubmit_row(
        self,
        file_record: dict[str, Any],
        quarantine: dict[str, Any],
        payload: Any,
        entity_key: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        target_graph = quarantine["target_graph"]
        adopted_fields: list[str] = []
        conflict_fields: list[str] = []
        kept_fields: list[str] = []  # 与替代值一致，无需写入
        pending_changes: list[dict[str, Any]] = []

        for field, submitted_value in values.items():
            current = self._current_fact(target_graph, entity_key, field)
            if current is None:
                # 图谱里还没有该属性：补交值直接生效
                self._write_fact(
                    task_id=payload.task_id,
                    target_graph=target_graph,
                    entity_key=entity_key,
                    field=field,
                    value=submitted_value,
                    source_file=payload.file_name,
                    source_line=quarantine["origin_line_no"],
                    source_kind="补交回填",
                )
                adopted_fields.append(field)
                continue

            if current["value"] == submitted_value:
                # 与已生效值一致，没有写入动作，来源仍可由版本记录核对
                kept_fields.append(field)
                continue

            # 同键属性已有另一个文件写入的不同取值：冲突，不覆盖生效数据，
            # 完整保留补交变更，等待人工裁决。
            conflict_fields.append(field)
            pending_changes.append({
                "field": field,
                "submitted_value": submitted_value,
                "effective_value": current["value"],
                "effective_source_file": current["source_file"],
                "effective_source_line": current["source_line"],
                "submitted_file": payload.file_name,
                "status": "待裁决",
            })

        result: str
        if conflict_fields:
            # 处理人可先关闭隔离问题，但冲突属性的补交变更保留供后续裁决；
            # 未采用的补交行不算已修正，审计侧仍能继续处理这一行。
            result = R_CONFLICT
            quarantine["pending_changes"] = [
                *quarantine.get("pending_changes", []),
                *pending_changes,
            ]
            quarantine["last_resubmit_file"] = payload.file_name
            self._set_quarantine_status(quarantine, Q_CONFLICT)
        elif adopted_fields:
            result = R_ADOPTED
            quarantine["resolved_by_file"] = payload.file_name
            self._set_quarantine_status(quarantine, Q_ADOPTED)
        else:
            # 所有属性都与已生效的替代行一致：由替代行解决
            result = R_REPLACED
            self._set_quarantine_status(quarantine, Q_REPLACED)

        return {
            "result": result,
            "adopted_fields": adopted_fields,
            "conflict_fields": conflict_fields,
            "kept_fields": kept_fields,
            "pending_changes": pending_changes,
        }

    # ------------------------------------------------------------------
    # 冲突裁决：采用补交值（新版本生效）或驳回（维持替代行取值）
    # ------------------------------------------------------------------
    def adjudicate(self, quarantine_id: int, decision: str, remark: str | None) -> tuple[dict[str, Any] | None, str]:
        quarantine = store.find_in(T_QUARANTINE, quarantine_id)
        if quarantine is None:
            return None, f"隔离行 {quarantine_id} 不存在"
        if quarantine["status"] != Q_CONFLICT:
            return None, f"隔离行当前状态为「{quarantine['status']}」，没有待裁决冲突"
        if decision not in (D_ADOPT, D_REJECT):
            return None, "裁决决定只支持 adopt（采用补交值）或 reject（驳回补交值）"

        pending = quarantine.get("pending_changes", [])
        handled: list[dict[str, Any]] = []
        if decision == D_ADOPT:
            for change in pending:
                if change["status"] != "待裁决":
                    continue
                self._write_fact(
                    task_id=quarantine["task_id"],
                    target_graph=quarantine["target_graph"],
                    entity_key=quarantine["entity_key"],
                    field=change["field"],
                    value=change["submitted_value"],
                    source_file=change["submitted_file"],
                    source_line=quarantine["origin_line_no"],
                    source_kind="裁决采用补交",
                )
                change["status"] = "裁决采用"
                handled.append(change)
            quarantine["adjudication"] = {"decision": "adopt", "remark": remark, "at": _now()}
            # 采用后补交值真正进入生效数据，整改按补交采用核销
            self._set_quarantine_status(quarantine, Q_ADOPTED)
        else:
            for change in pending:
                if change["status"] == "待裁决":
                    change["status"] = "裁决驳回"
                    handled.append(change)
            quarantine["adjudication"] = {"decision": "reject", "remark": remark, "at": _now()}
            # 驳回后图谱中仍是替代行取值，按替代行解决核销
            self._set_quarantine_status(quarantine, Q_REPLACED)
        return quarantine, f"已完成{len(handled)}个冲突属性的裁决"

    # ------------------------------------------------------------------
    # 查询：隔离行、生效数据与版本来源、补交文件
    # ------------------------------------------------------------------
    def list_quarantine(self, task_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.domain_rows(T_QUARANTINE)
        if task_id:
            rows = [row for row in rows if row["task_id"] == task_id]
        if status:
            rows = [row for row in rows if row["status"] == status]
        return rows

    def list_facts(self, target_graph: str | None = None, entity_key: str | None = None) -> list[dict[str, Any]]:
        rows = store.domain_rows(T_GRAPH_FACTS)
        if target_graph:
            rows = [row for row in rows if row["target_graph"] == target_graph]
        if entity_key:
            rows = [row for row in rows if row["entity_key"] == entity_key]
        return rows

    def fact_versions(self, target_graph: str, entity_key: str, field: str) -> list[dict[str, Any]]:
        fk = self._fact_key(target_graph, entity_key)
        return [
            row for row in store.domain_rows(T_FACT_VERSIONS)
            if row["fact_key"] == fk and row["field"] == field
        ]

    def list_resubmit_files(self, task_id: str | None = None) -> list[dict[str, Any]]:
        rows = store.domain_rows(T_RESUBMIT_FILES)
        if task_id:
            rows = [row for row in rows if row["task_id"] == task_id]
        return rows

    def list_resubmit_rows(self, file_record_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.domain_rows(T_RESUBMIT_ROWS)
        if file_record_id is not None:
            rows = [row for row in rows if row["file_record_id"] == file_record_id]
        return rows
