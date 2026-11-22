"""跳过行补交回填端到端核对脚本（使用 FastAPI TestClient，内存仓库）。

覆盖需求：
1. 初始导入坏行隔离，隔离身份回指原行号并生成审计整改待办；
2. 先导入替代值（另一文件同键写入），隔离问题按「替代行解决」核销；
3. 上传不同补交文件：冲突属性不覆盖生效数据、变更保留待裁决；
4. 同份补交文件重复提交只生效一次（内容指纹幂等）；
5. 图谱生效数据版本可核对实际取值来源，未采用补交不算已修正；
6. 审计整改区分未修正 / 补交采用 / 替代行解决 / 冲突待裁决；
7. 裁决采用补交值后产生新版本，驳回则维持替代值。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/workspace/backend")

import os

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")


def api(method: str, path: str, payload: object | None = None, params: dict[str, object] | None = None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"{method} {path} -> {exc.code}: {exc.read().decode('utf-8')}") from exc


class Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def json(self) -> object:
        return self._payload


client = type("Client", (), {
    "post": lambda self, p, json=None: Response(api("POST", p, json)),
    "get": lambda self, p, params=None: Response(api("GET", p, params=params)),
})()
TASK = "BATC-E2E-01"
GRAPH = "零售商品图谱"
failures: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -> {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


# ---------- 1. 初始导入：2 好行 + 2 坏行（含显式隔离与缺值隔离） ----------
resp = client.post("/api/batch_load/imports/initial", json={
    "task_id": TASK,
    "file_name": "2026-11-20-商品主数据.csv",
    "target_graph": GRAPH,
    "key_field": "商品编码",
    "lines": [
        {"line_no": 2, "key": "SKU-100", "values": {"品名": "蓝牙耳机", "类目": "数码"}},
        {"line_no": 7, "key": "SKU-200", "values": {"品名": "保温杯", "类目": "家居"}},
        # 坏行 A：显式隔离（原第 12 行）
        {"line_no": 12, "key": "SKU-200", "values": {"品名": "", "类目": "家居"},
         "quarantine": True, "reason": "品名字段缺失"},
        # 坏行 B：缺省校验拦截（原第 18 行）
        {"line_no": 18, "key": "SKU-300", "values": {"品名": "机械键盘", "类目": ""}},
    ],
})
data = resp.json()
check("初始导入 2 行生效", data["imported_rows"] == 2, data)
check("初始导入 2 行隔离", data["quarantined_rows"] == 2, data)
q_rows = client.get("/api/batch_load/quarantine", params={"task_id": TASK}).json()["items"]
check("隔离行数量为 2", len(q_rows) == 2)
q200 = next(q for q in q_rows if q["entity_key"] == "SKU-200")
q300 = next(q for q in q_rows if q["entity_key"] == "SKU-300")
check("隔离身份回指原行号(12)", q200["origin_line_no"] == 12)
check("隔离身份回指原行号(18)", q300["origin_line_no"] == 18)
check("隔离行初始状态未修正", q200["status"] == "未修正" and q300["status"] == "未修正")

# 审计待办同步生成
summary = client.get("/api/batch_load/rectifications/summary", params={"task_id": TASK}).json()
check("审计整改待办 2 条未修正", summary["未修正"] == 2 and summary["total"] == 2, summary)

# ---------- 2. 先导入替代值：另一个文件先修好 SKU-200 ----------
resp = client.post("/api/batch_load/imports/replacement", json={
    "task_id": TASK,
    "file_name": "2026-11-21-供应商补录.csv",
    "target_graph": GRAPH,
    "key_field": "商品编码",
    "lines": [
        {"line_no": 5, "key": "SKU-200", "values": {"品名": "保温水杯-替代", "类目": "家居用品"}},
    ],
})
rdata = resp.json()
check("替代导入写入 1 行", rdata["written_rows"] == 1, rdata)
check("替代导入核销 SKU-200 隔离", len(rdata["resolved_quarantine"]) == 1
      and rdata["resolved_quarantine"][0]["resolution"] == "替代行解决")
q200_after = client.get("/api/batch_load/quarantine", params={"task_id": TASK}).json()["items"]
q200 = next(q for q in q200_after if q["id"] == q200["id"])
check("SKU-200 隔离状态=替代行解决", q200["status"] == "替代行解决", q200["status"])
facts = client.get("/api/batch_load/graph/facts",
                   params={"target_graph": GRAPH, "entity_key": "SKU-200"}).json()["items"]
fact = next(f for f in facts if f["field"] == "品名")
check("图谱生效值来自替代文件", fact["value"] == "保温水杯-替代"
      and fact["source_kind"] == "替代导入" and fact["source_file"] == "2026-11-21-供应商补录.csv", fact)

summary = client.get("/api/batch_load/rectifications/summary", params={"task_id": TASK}).json()
check("审计核销：1 替代行解决 + 1 未修正",
      summary["替代行解决"] == 1 and summary["未修正"] == 1, summary)

# ---------- 3. 上传补交文件 A：SKU-200 冲突 + SKU-300 全部采用 ----------
payload_a = {
    "task_id": TASK,
    "file_name": "2026-11-22-隔离修订-补交甲.csv",
    "key_field": "商品编码",
    "rows": [
        # SKU-200：品名与替代值冲突；类目与替代值一致（保持）
        {"key": "SKU-200", "values": {"品名": "保温水杯-补交甲", "类目": "家居用品"}},
        # SKU-300：图谱中无该实体，全部采用
        {"key": "SKU-300", "values": {"品名": "机械键盘", "类目": "外设"}},
    ],
}
resp = client.post("/api/batch_load/resubmits", json=payload_a)
adata = resp.json()
check("补交文件 A 首次提交生效", adata["duplicate"] is False, adata)
check("补交 A：1 冲突 + 1 采用",
      adata["summary"]["冲突待裁决"] == 1 and adata["summary"]["补交采用"] == 1, adata["summary"])

# 生效数据未被冲突补交覆盖
facts = client.get("/api/batch_load/graph/facts",
                   params={"target_graph": GRAPH, "entity_key": "SKU-200"}).json()["items"]
fact = next(f for f in facts if f["field"] == "品名")
check("冲突属性生效值仍来自替代文件", fact["value"] == "保温水杯-替代", fact)

# SKU-300 补交采用且来源可核对
facts300 = client.get("/api/batch_load/graph/facts",
                      params={"target_graph": GRAPH, "entity_key": "SKU-300"}).json()["items"]
f300 = next(f for f in facts300 if f["field"] == "类目")
check("SKU-300 补交值生效并记录来源", f300["value"] == "外设"
      and f300["source_kind"] == "补交回填"
      and f300["source_file"] == "2026-11-22-隔离修订-补交甲.csv"
      and f300["source_line"] == 18, f300)

# 隔离行状态与审计口径
q_all = client.get("/api/batch_load/quarantine", params={"task_id": TASK}).json()["items"]
q200 = next(q for q in q_all if q["entity_key"] == "SKU-200")
q300 = next(q for q in q_all if q["entity_key"] == "SKU-300")
check("SKU-200 转入冲突待裁决且补交变更保留",
      q200["status"] == "冲突待裁决"
      and q200["pending_changes"][0]["submitted_value"] == "保温水杯-补交甲"
      and q200["pending_changes"][0]["effective_value"] == "保温水杯-替代", q200)
check("SKU-300 状态=补交采用", q300["status"] == "补交采用")
rects = client.get("/api/batch_load/rectifications", params={"task_id": TASK}).json()["items"]
r200 = next(r for r in rects if r["entity_key"] == "SKU-200")
check("冲突行审计仍开放（未采用不算已修正）", r200["closed"] is False and r200["resolution"] == "冲突待裁决")
summary = client.get("/api/batch_load/rectifications/summary", params={"task_id": TASK}).json()
# 已核销的替代行解决因新冲突重新打开，此时三类口径：1 采用 + 1 冲突 + 0 未修正
check("审计汇总区分三类口径",
      summary["补交采用"] == 1 and summary["替代行解决"] == 0
      and summary["冲突待裁决"] == 1 and summary["未修正"] == 0, summary)

# ---------- 4. 同份补交文件重复提交：只生效一次 ----------
resp = client.post("/api/batch_load/resubmits", json=payload_a)
dup = resp.json()
check("重复提交识别为幂等命中", dup["duplicate"] is True and "未再生效" in dup["message"], dup)
files = client.get("/api/batch_load/resubmits", params={"task_id": TASK}).json()["items"]
fa = next(f for f in files if f["file_name"] == "2026-11-22-隔离修订-补交甲.csv")
check("同文件重复提交计数为 2 但登记只 1 份", fa["resubmit_count"] == 2 and len(files) == 1, fa)
# 改名但内容相同，仍按内容指纹判重
payload_a_renamed = dict(payload_a, file_name="改名副本.csv")
resp = client.post("/api/batch_load/resubmits", json=payload_a_renamed)
check("改名同内容仍判重", resp.json()["duplicate"] is True)

# ---------- 5. 上传不同补交文件 B：再次提交 SKU-200（仍冲突，新变更保留） ----------
resp = client.post("/api/batch_load/resubmits", json={
    "task_id": TASK,
    "file_name": "2026-11-22-隔离修订-补交乙.csv",
    "key_field": "商品编码",
    "rows": [
        {"key": "SKU-200", "values": {"品名": "保温水杯-补交乙", "类目": "家居用品"}},
    ],
})
bdata = resp.json()
check("补交文件 B 为新提交", bdata["duplicate"] is False and bdata["summary"]["冲突待裁决"] == 1, bdata)
q200 = next(q for q in client.get("/api/batch_load/quarantine", params={"task_id": TASK}).json()["items"]
            if q["entity_key"] == "SKU-200")
check("两次补交变更都保留", len(q200["pending_changes"]) == 2
      and q200["pending_changes"][-1]["submitted_file"] == "2026-11-22-隔离修订-补交乙.csv")
# 生效数据依旧是替代值
fact = next(f for f in client.get("/api/batch_load/graph/facts",
                                  params={"target_graph": GRAPH, "entity_key": "SKU-200"}).json()["items"]
            if f["field"] == "品名")
check("裁决前图谱生效值不变", fact["value"] == "保温水杯-替代")

# ---------- 6. 版本流水可核对实际来源 ----------
versions = client.get("/api/batch_load/graph/facts/versions", params={
    "target_graph": GRAPH, "entity_key": "SKU-200", "field": "品名"}).json()["items"]
# 初始好行(版本1) + 替代导入(版本2)；两次冲突补交均未写入，不产生新版本
check("裁决前版本流水仅 2 版，冲突补交未写入",
      [v["version"] for v in versions] == [1, 2]
      and [v["source_kind"] for v in versions] == ["初始导入", "替代导入"], versions)

# ---------- 7. 裁决：采用补交乙的值 ----------
resp = client.post(f"/api/batch_load/quarantine/{q200['id']}/adjudicate",
                   json={"decision": "adopt", "remark": "供应商确认补交口径"})
check("裁决采用成功", resp.json()["ok"] is True, resp.json())
fact = next(f for f in client.get("/api/batch_load/graph/facts",
                                  params={"target_graph": GRAPH, "entity_key": "SKU-200"}).json()["items"]
            if f["field"] == "品名")
check("裁决后生效值变为补交值且来源可核对",
      fact["value"] == "保温水杯-补交乙" and fact["version"] == 4
      and fact["source_kind"] == "裁决采用补交"
      and fact["source_file"] == "2026-11-22-隔离修订-补交乙.csv", fact)
versions = client.get("/api/batch_load/graph/facts/versions", params={
    "target_graph": GRAPH, "entity_key": "SKU-200", "field": "品名"}).json()["items"]
check("版本流水追加且历史来源完整保留",
      [v["version"] for v in versions] == [1, 2, 3, 4]
      and versions[1]["value"] == "保温水杯-替代"
      and versions[2]["source_file"] == "2026-11-22-隔离修订-补交甲.csv", versions)
q200 = next(q for q in client.get("/api/batch_load/quarantine", params={"task_id": TASK}).json()["items"]
            if q["entity_key"] == "SKU-200")
check("裁决采用后隔离核销为补交采用", q200["status"] == "补交采用")
summary = client.get("/api/batch_load/rectifications/summary", params={"task_id": TASK}).json()
# 两条隔离行最终均核销：SKU-200 经裁决采用补交、SKU-300 补交采用；无未决项
check("最终审计汇总：2 补交采用，无未决",
      summary["补交采用"] == 2 and summary["替代行解决"] == 0
      and summary["冲突待裁决"] == 0 and summary["open"] == 0, summary)

# ---------- 8. 驳回路径（新冲突行） ----------
client.post("/api/batch_load/imports/initial", json={
    "task_id": TASK, "file_name": "批次2.csv", "target_graph": GRAPH, "key_field": "商品编码",
    "lines": [{"line_no": 3, "key": "SKU-400", "quarantine": True,
               "values": {"品名": "坏值"}, "reason": "测试隔离"}],
})
client.post("/api/batch_load/imports/replacement", json={
    "task_id": TASK, "file_name": "批次2替代.csv", "target_graph": GRAPH, "key_field": "商品编码",
    "lines": [{"line_no": 1, "key": "SKU-400", "values": {"品名": "替代生效值"}}],
})
client.post("/api/batch_load/resubmits", json={
    "task_id": TASK, "file_name": "批次2补交.csv", "key_field": "商品编码",
    "rows": [{"key": "SKU-400", "values": {"品名": "补交冲突值"}}],
})
q400 = next(q for q in client.get("/api/batch_load/quarantine", params={"task_id": TASK}).json()["items"]
            if q["entity_key"] == "SKU-400")
check("SKU-400 进入冲突待裁决", q400["status"] == "冲突待裁决")
resp = client.post(f"/api/batch_load/quarantine/{q400['id']}/adjudicate",
                   json={"decision": "reject", "remark": "维持替代来源"})
check("裁决驳回成功", resp.json()["ok"] is True)
fact = next(f for f in client.get("/api/batch_load/graph/facts",
                                  params={"target_graph": GRAPH, "entity_key": "SKU-400"}).json()["items"]
            if f["field"] == "品名")
check("驳回后维持替代行生效值", fact["value"] == "替代生效值" and fact["version"] == 1, fact)
q400 = next(q for q in client.get("/api/batch_load/quarantine", params={"task_id": TASK}).json()["items"]
            if q["entity_key"] == "SKU-400")
check("驳回后按替代行解决核销", q400["status"] == "替代行解决")

# 无匹配隔离行的补交
resp = client.post("/api/batch_load/resubmits", json={
    "task_id": TASK, "file_name": "孤儿补交.csv", "key_field": "商品编码",
    "rows": [{"key": "SKU-999", "values": {"品名": "无主"}}],
})
check("无匹配隔离行标记且不产生图谱写入",
      resp.json()["summary"].get("无匹配隔离行") == 1, resp.json()["summary"])
check("孤儿补交未污染图谱",
      client.get("/api/batch_load/graph/facts",
                 params={"target_graph": GRAPH, "entity_key": "SKU-999"}).json()["items"] == [])

print("\n" + ("全部核对通过 ✅" if not failures else f"存在 {len(failures)} 项失败 ❌: {failures}"))
sys.exit(1 if failures else 0)
