<template>
  <section class="page" data-module="batch_load_resubmit">
    <header class="page-head">
      <div>
        <h2>跳过行补交工作台</h2>
        <p class="page-desc">
          上传原隔离行的修订文件回填图谱：同键替代值冲突时保留补交变更待裁决，
          同份补交文件重复提交只生效一次，生效数据的每个版本均可核对取值来源。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="refresh" @click="reloadAll">刷新数据</button>
        <button class="btn ghost" type="button" @click="fillExample">填入示例报文</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">未修正</span>
        <strong class="stat-value">{{ summary['未修正'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">补交采用</span>
        <strong class="stat-value">{{ summary['补交采用'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">替代行解决</span>
        <strong class="stat-value">{{ summary['替代行解决'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">冲突待裁决</span>
        <strong class="stat-value">{{ summary['冲突待裁决'] ?? 0 }}</strong>
      </article>
    </div>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </div>

    <p v-if="message" class="message" :class="messageKind">{{ message }}</p>

    <!-- 导入与补交报文 -->
    <div v-if="activeTab === 'ingest'" class="panel">
      <div class="panel-row">
        <label class="create-field">
          <span>导入任务编号</span>
          <input v-model="form.task_id" data-testid="task-id" placeholder="如 BATC-2026-001" />
        </label>
        <label class="create-field">
          <span>目标图谱</span>
          <input v-model="form.target_graph" placeholder="如 零售商品图谱" />
        </label>
        <label class="create-field">
          <span>主键字段</span>
          <input v-model="form.key_field" placeholder="如 商品编码" />
        </label>
        <label class="create-field">
          <span>文件名</span>
          <input v-model="form.file_name" placeholder="来源文件名（用于来源核对）" />
        </label>
      </div>
      <label class="create-field">
        <span>行报文（JSON：初始/替代导入为 lines，补交为 rows）</span>
        <textarea
          v-model="rawPayload"
          class="payload-box"
          data-testid="payload"
          rows="12"
          placeholder='{"lines":[{"line_no":12,"key":"SKU-200","values":{"品名":"保温杯"},"quarantine":true,"reason":"品名缺失"}]}'
        ></textarea>
      </label>
      <div class="page-actions">
        <button class="btn" type="button" @click="submitImport('initial')">初始导入（坏行隔离）</button>
        <button class="btn" type="button" @click="submitImport('replacement')">导入替代值</button>
        <button class="btn primary" type="button" data-testid="submit-resubmit" @click="submitResubmit">
          上传补交文件
        </button>
      </div>
    </div>

    <!-- 隔离行与冲突裁决 -->
    <div v-if="activeTab === 'quarantine'" class="panel">
      <table class="data-table">
        <thead>
          <tr>
            <th>隔离ID</th><th>实体键</th><th>原文件行号</th><th>原坏行内容</th>
            <th>隔离原因</th><th>状态</th><th>待裁决补交变更</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in quarantineRows" :key="row.id" :data-testid="`q-${row.id}`">
            <td>{{ row.id }}</td>
            <td>{{ row.entity_key }}</td>
            <td>{{ row.origin_line_no }}</td>
            <td>{{ formatValues(row.raw_values) }}</td>
            <td>{{ row.reason }}</td>
            <td>
              <span class="status-badge" :data-status="row.status">{{ row.status }}</span>
            </td>
            <td>
              <div v-for="change in row.pending_changes ?? []" :key="change.field" class="change-item">
                <strong>{{ change.field }}</strong>：
                补交「{{ change.submitted_value }}」 vs 生效「{{ change.effective_value }}」
                <em>（来源 {{ change.effective_source_file }}:{{ change.effective_source_line }}，
                  {{ change.status }}）</em>
              </div>
              <span v-if="!(row.pending_changes ?? []).length" class="muted">—</span>
            </td>
            <td class="row-actions">
              <template v-if="row.status === '冲突待裁决'">
                <button class="link" type="button" @click="decide(row, 'adopt')">采用补交值</button>
                <button class="link danger" type="button" @click="decide(row, 'reject')">驳回</button>
              </template>
              <span v-else class="muted">—</span>
            </td>
          </tr>
          <tr v-if="!quarantineRows.length">
            <td colspan="8" class="empty-state">暂无隔离行</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 图谱生效数据与来源版本 -->
    <div v-if="activeTab === 'facts'" class="panel">
      <div class="filter-bar">
        <label class="filter-item">
          <span>按图谱筛选</span>
          <input v-model="factFilter.graph" placeholder="目标图谱" />
        </label>
        <label class="filter-item">
          <span>按实体键筛选</span>
          <input v-model="factFilter.entity" placeholder="实体键" />
        </label>
        <button class="btn" type="button" @click="loadFacts">查询</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>实体键</th><th>属性</th><th>生效值</th><th>版本</th>
            <th>来源类型</th><th>来源文件</th><th>来源行号</th><th>版本流水</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="fact in facts" :key="fact.id">
            <td>{{ fact.entity_key }}</td>
            <td>{{ fact.field }}</td>
            <td>{{ fact.value }}</td>
            <td>v{{ fact.version }}</td>
            <td>{{ fact.source_kind }}</td>
            <td>{{ fact.source_file }}</td>
            <td>{{ fact.source_line }}</td>
            <td>
              <button class="link" type="button" @click="showVersions(fact)">查看版本</button>
            </td>
          </tr>
          <tr v-if="!facts.length">
            <td colspan="8" class="empty-state">暂无生效数据</td>
          </tr>
        </tbody>
      </table>
      <div v-if="versionList.length" class="version-box" data-testid="version-box">
        <h4>版本流水（{{ versionTitle }}）</h4>
        <table class="data-table">
          <thead>
            <tr><th>版本</th><th>取值</th><th>来源类型</th><th>来源文件</th><th>行号</th><th>写入时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="version in versionList" :key="version.id">
              <td>v{{ version.version }}</td>
              <td>{{ version.value }}</td>
              <td>{{ version.source_kind }}</td>
              <td>{{ version.source_file }}</td>
              <td>{{ version.source_line }}</td>
              <td>{{ version.written_at }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 补交文件幂等记录 -->
    <div v-if="activeTab === 'files'" class="panel">
      <table class="data-table">
        <thead>
          <tr><th>文件</th><th>任务编号</th><th>内容指纹</th><th>提交次数</th><th>处理汇总</th><th>最近提交</th></tr>
        </thead>
        <tbody>
          <tr v-for="file in resubmitFiles" :key="file.id">
            <td>{{ file.file_name }}</td>
            <td>{{ file.task_id }}</td>
            <td><code>{{ file.fingerprint.slice(0, 12) }}…</code></td>
            <td>{{ file.resubmit_count }}</td>
            <td>{{ formatValues(file.summary) }}</td>
            <td>{{ file.last_submitted_at }}</td>
          </tr>
          <tr v-if="!resubmitFiles.length">
            <td colspan="6" class="empty-state">暂无补交文件</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 审计整改核销 -->
    <div v-if="activeTab === 'audit'" class="panel">
      <table class="data-table">
        <thead>
          <tr><th>整改ID</th><th>任务</th><th>实体键</th><th>原行号</th><th>来源文件</th><th>核销口径</th><th>是否关闭</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in rectifications" :key="item.id">
            <td>{{ item.id }}</td>
            <td>{{ item.task_id }}</td>
            <td>{{ item.entity_key }}</td>
            <td>{{ item.origin_line_no }}</td>
            <td>{{ item.source_file }}</td>
            <td><span class="status-badge" :data-status="item.resolution">{{ item.resolution }}</span></td>
            <td>{{ item.closed ? '已关闭' : '处理中' }}</td>
          </tr>
          <tr v-if="!rectifications.length">
            <td colspan="7" class="empty-state">暂无整改项</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  adjudicate,
  fetchFactVersions,
  fetchFacts,
  fetchQuarantine,
  fetchRectificationSummary,
  fetchRectifications,
  fetchResubmitFiles,
  initialImport,
  replacementImport,
  uploadResubmit,
  type GraphFact,
  type QuarantineRow,
  type Rectification,
  type ResubmitFile,
} from '@/api/batch-load'

const tabs = [
  { key: 'ingest', label: '导入与补交' },
  { key: 'quarantine', label: '隔离行与裁决' },
  { key: 'facts', label: '图谱生效数据' },
  { key: 'files', label: '补交文件记录' },
  { key: 'audit', label: '审计整改核销' },
] as const

const activeTab = ref<(typeof tabs)[number]['key']>('ingest')
const form = ref({ task_id: 'BATC-2026-001', target_graph: '零售商品图谱', key_field: '商品编码', file_name: '' })
const rawPayload = ref('')
const message = ref('')
const messageKind = ref('ok')

const quarantineRows = ref<QuarantineRow[]>([])
const facts = ref<GraphFact[]>([])
const resubmitFiles = ref<ResubmitFile[]>([])
const rectifications = ref<Rectification[]>([])
const summary = ref<Record<string, number>>({})
const factFilter = ref({ graph: '', entity: '' })
const versionList = ref<GraphFact[]>([])
const versionTitle = ref('')

function formatValues(values: Record<string, unknown>): string {
  return Object.entries(values).map(([key, value]) => `${key}=${String(value)}`).join('，')
}

function notify(text: string, ok = true) {
  message.value = text
  messageKind.value = ok ? 'ok' : 'error'
}

function parsePayload(): Record<string, unknown> {
  try {
    return JSON.parse(rawPayload.value) as Record<string, unknown>
  } catch {
    throw new Error('报文不是合法 JSON，请检查后再提交')
  }
}

type LoosePayload = Record<string, unknown>

async function submitImport(kind: 'initial' | 'replacement') {
  try {
    const base = { ...form.value, ...parsePayload() }
    if (kind === 'initial') {
      const result = await initialImport(base as LoosePayload as Parameters<typeof initialImport>[0])
      notify(`初始导入完成：生效 ${result.imported_rows} 行，隔离 ${result.quarantined_rows} 行`)
    } else {
      const result = await replacementImport(base as LoosePayload as Parameters<typeof replacementImport>[0])
      notify(`替代导入完成：写入 ${result.written_rows} 行，核销隔离 ${result.resolved_quarantine.length} 行`)
    }
    await reloadAll()
  } catch (error) {
    notify(error instanceof Error ? error.message : '导入失败', false)
  }
}

async function submitResubmit() {
  try {
    const base = { ...form.value, ...parsePayload() }
    const result = await uploadResubmit(base as LoosePayload as Parameters<typeof uploadResubmit>[0])
    if (result.duplicate) {
      notify(result.message ?? '该补交文件已提交过，本次未再生效应')
    } else {
      notify(`补交处理完成：${formatValues(result.summary)}`)
    }
    await reloadAll()
  } catch (error) {
    notify(error instanceof Error ? error.message : '补交失败', false)
  }
}

async function decide(row: QuarantineRow, decision: 'adopt' | 'reject') {
  try {
    const result = await adjudicate(row.id, decision, decision === 'adopt' ? '人工裁决采用补交值' : '人工裁决驳回补交值')
    notify(result.message)
    await reloadAll()
  } catch (error) {
    notify(error instanceof Error ? error.message : '裁决失败', false)
  }
}

async function showVersions(fact: GraphFact) {
  versionTitle.value = `${fact.entity_key} / ${fact.field}`
  const data = await fetchFactVersions(fact.target_graph, fact.entity_key, fact.field)
  versionList.value = data.items
}

async function loadFacts() {
  const data = await fetchFacts(factFilter.value.graph || undefined, factFilter.value.entity || undefined)
  facts.value = data.items
}

async function reloadAll() {
  try {
    const [quarantine, files, rects, sum] = await Promise.all([
      fetchQuarantine(),
      fetchResubmitFiles(),
      fetchRectifications(),
      fetchRectificationSummary(),
    ])
    quarantineRows.value = quarantine.items
    resubmitFiles.value = files.items
    rectifications.value = rects.items
    summary.value = sum
    await loadFacts()
  } catch (error) {
    notify(error instanceof Error ? error.message : '数据刷新失败', false)
  }
}

function fillExample() {
  form.value = {
    task_id: 'BATC-2026-001',
    target_graph: '零售商品图谱',
    key_field: '商品编码',
    file_name: '2026-11-22-隔离修订-补交.csv',
  }
  rawPayload.value = JSON.stringify(
    {
      rows: [
        { key: 'SKU-200', values: { 品名: '保温水杯-修订值', 类目: '家居用品' } },
        { key: 'SKU-300', values: { 品名: '机械键盘', 类目: '外设' } },
      ],
    },
    null,
    2,
  )
}

onMounted(reloadAll)
</script>

<style scoped>
.tabs { display: flex; gap: 8px; margin: 12px 0; flex-wrap: wrap; }
.tab { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 6px 14px; cursor: pointer; }
.tab.active { background: var(--brand); color: #fff; border-color: var(--brand); }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.panel-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.payload-box { width: 100%; font-family: ui-monospace, monospace; font-size: 12px; border: 1px solid var(--border); border-radius: 6px; padding: 8px; }
.message { padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.message.ok { background: #ecfdf3; color: #027a48; }
.message.error { background: #fef3f2; color: #b42318; }
.status-badge { padding: 2px 8px; border-radius: 999px; font-size: 12px; background: #f2f4f7; }
.status-badge[data-status='冲突待裁决'] { background: #fffaeb; color: #b54708; }
.status-badge[data-status='补交采用'] { background: #ecfdf3; color: #027a48; }
.status-badge[data-status='替代行解决'] { background: #eff8ff; color: #175cd3; }
.change-item { font-size: 12px; margin-bottom: 4px; }
.change-item em { color: var(--muted); font-style: normal; }
.muted { color: var(--muted); font-size: 12px; }
.danger { color: #b42318; }
.version-box { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
.version-box h4 { margin: 0 0 8px; }
code { font-size: 11px; }
</style>
