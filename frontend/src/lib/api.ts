// Types and fetchers for the FastAPI service (src/ehr_platform/api/app.py).

const BASE = import.meta.env.VITE_API_URL ?? ""

export type ResourceType = "DocumentReference" | "DiagnosticReport" | "Patient"
export type RecordKind = "note" | "lab_report" | "imaging_report" | "summary"

export interface PatientInfo {
  id: string
  name: string
  mrn: string | null
  gender: string | null
  birth_date: string | null
  age: number | null
}

export interface SearchResult {
  record_id: string
  resource_type: ResourceType
  kind: RecordKind
  title: string
  date: string | null
  score: number
  snippet: string
  patient: PatientInfo
  summary_snippet: string | null
  similar_records: string[]
}

export interface SearchResponse {
  query: string
  took_ms: number
  results: SearchResult[]
}

export interface SearchParams {
  query: string
  resourceType: ResourceType | null
  dateFrom: string | null
  dateTo: string | null
}

export interface Finding {
  text: string
  source_ids: string[]
}

export interface ClinicalSummary {
  overview: string
  chief_concern: string
  key_diagnoses: Finding[]
  recent_media: { source_id: string; kind: "imaging" | "lab" | "pathology"; date: string | null; result: string }[]
  anomalies: { kind: "clinical" | "data_quality"; text: string; source_ids: string[] }[]
  confidence: "high" | "medium" | "low"
  confidence_reason: string
}

export interface PatientDetail {
  patient: PatientInfo
  flags: string[]
  summary: {
    summary: ClinicalSummary
    disclaimer: string
    model: string
    generated_at: string
    word_count: number
    quality: { name: string; passed: boolean; detail: string }[]
  } | null
  resources: { resource_type: string; id: string; title: string; date: string | null; status: string | null; detail: string | null }[]
}

export interface ResourceDetail {
  resource: Record<string, unknown>
  text: string | null
  results: { name: string; value: string | null }[]
}

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, init)
  } catch {
    throw new ApiError("The search service is not reachable.")
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${response.status}).`
    throw new ApiError(detail)
  }
  return response.json() as Promise<T>
}

export function searchRecords({ query, resourceType, dateFrom, dateTo }: SearchParams): Promise<SearchResponse> {
  const params = new URLSearchParams()
  if (resourceType) params.set("resource_type", resourceType)
  if (dateFrom) params.set("date_from", dateFrom)
  if (dateTo) params.set("date_to", dateTo)
  const qs = params.toString()
  return request<SearchResponse>(`/search${qs ? `?${qs}` : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  })
}

export const patientKey = (id: string) => `/patients/${encodeURIComponent(id)}`
export const resourceKey = (type: string, id: string) =>
  `/resources/${encodeURIComponent(type)}/${encodeURIComponent(id)}`

export const getJson = <T,>(path: string) => request<T>(path)
