import { request } from './client'

export type QuarantineRow = {
  id: number
  task_id: string
  target_graph: string
  entity_key: string
  key_field: string
  source_file: string
  origin_line_no: number
  raw_values: Record<string, string | number>
  reason: string
  status: string
  pending_changes?: Array<{
    field: string
    submitted_value: string | number
    effective_value: string | number
    effective_source_file: string
    effective_source_line: number
    submitted_file: string
    status: string
  }>
  last_resubmit_file?: string
  resolved_by_file?: string
  created_at: string
  updated_at: string
}

export type GraphFact = {
  id: number
  fact_key: string
  target_graph: string
  entity_key: string
  field: string
  value: string | number
  version: number
  source_file: string
  source_line: number
  source_kind: string
  updated_at?: string
  written_at?: string
}

export type ResubmitFile = {
  id: number
  task_id: string
  file_name: string
  fingerprint: string
  resubmit_count: number
  summary: Record<string, number>
  submitted_at: string
  last_submitted_at: string
}

export type Rectification = {
  id: number
  task_id: string
  quarantine_id: number
  entity_key: string
  origin_line_no: number
  source_file: string
  resolution: string
  closed: boolean
  updated_at: string
}

type ImportLine = {
  line_no: number
  key: string
  values: Record<string, string>
  quarantine?: boolean
  reason?: string
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(payload) })
  if (!response.ok) throw new Error('接口返回错误，操作未生效')
  return (await response.json()) as T
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error('接口返回错误，数据读取失败')
  return (await response.json()) as T
}

export function initialImport(payload: {
  task_id: string
  file_name: string
  target_graph: string
  key_field: string
  lines: ImportLine[]
}) {
  return postJson<{ ok: boolean; imported_rows: number; quarantined_rows: number }>(
    '/api/batch_load/imports/initial',
    payload,
  )
}

export function replacementImport(payload: {
  task_id: string
  file_name: string
  target_graph: string
  key_field: string
  lines: ImportLine[]
}) {
  return postJson<{ ok: boolean; written_rows: number; resolved_quarantine: unknown[] }>(
    '/api/batch_load/imports/replacement',
    payload,
  )
}

export function uploadResubmit(payload: {
  task_id: string
  file_name: string
  key_field: string
  rows: Array<{ key: string; values: Record<string, string> }>
}) {
  return postJson<{
    ok: boolean
    duplicate: boolean
    message?: string
    summary: Record<string, number>
  }>('/api/batch_load/resubmits', payload)
}

export function adjudicate(quarantineId: number, decision: 'adopt' | 'reject', remark: string) {
  return postJson<{ ok: boolean; message: string }>(
    `/api/batch_load/quarantine/${quarantineId}/adjudicate`,
    { decision, remark },
  )
}

export function fetchQuarantine(taskId?: string, status?: string) {
  const params = new URLSearchParams()
  if (taskId) params.set('task_id', taskId)
  if (status) params.set('status', status)
  return getJson<{ items: QuarantineRow[] }>(`/api/batch_load/quarantine?${params}`)
}

export function fetchFacts(targetGraph?: string, entityKey?: string) {
  const params = new URLSearchParams()
  if (targetGraph) params.set('target_graph', targetGraph)
  if (entityKey) params.set('entity_key', entityKey)
  return getJson<{ items: GraphFact[] }>(`/api/batch_load/graph/facts?${params}`)
}

export function fetchFactVersions(targetGraph: string, entityKey: string, field: string) {
  const params = new URLSearchParams({ target_graph: targetGraph, entity_key: entityKey, field })
  return getJson<{ items: GraphFact[] }>(`/api/batch_load/graph/facts/versions?${params}`)
}

export function fetchResubmitFiles(taskId?: string) {
  const params = new URLSearchParams()
  if (taskId) params.set('task_id', taskId)
  return getJson<{ items: ResubmitFile[] }>(`/api/batch_load/resubmits?${params}`)
}

export function fetchRectifications(taskId?: string, includeClosed = true) {
  const params = new URLSearchParams()
  if (taskId) params.set('task_id', taskId)
  params.set('include_closed', String(includeClosed))
  return getJson<{ items: Rectification[] }>(`/api/batch_load/rectifications?${params}`)
}

export function fetchRectificationSummary(taskId?: string) {
  const params = new URLSearchParams()
  if (taskId) params.set('task_id', taskId)
  return getJson<Record<string, number>>(`/api/batch_load/rectifications/summary?${params}`)
}
