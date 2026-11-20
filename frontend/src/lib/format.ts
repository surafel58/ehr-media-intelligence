import type { RecordKind } from "./api"

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/** "2024-03-16" -> { dayMonth: "16 Mar", year: "2024" } */
export function splitDate(iso: string | null): { dayMonth: string; year: string } | null {
  if (!iso || iso.length < 10) return null
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number)
  return { dayMonth: `${d} ${MONTHS[m - 1]}`, year: String(y) }
}

export function formatDate(iso: string | null): string {
  const parts = splitDate(iso)
  return parts ? `${parts.dayMonth} ${parts.year}` : "No date"
}

export function toIsoDate(date: Date | undefined): string | null {
  if (!date) return null
  const m = String(date.getMonth() + 1).padStart(2, "0")
  const d = String(date.getDate()).padStart(2, "0")
  return `${date.getFullYear()}-${m}-${d}`
}

export function ageFrom(birthDate: string | null, statedAge: number | null): number | null {
  if (statedAge != null) return statedAge
  if (!birthDate || birthDate.length < 10) return null
  const born = new Date(`${birthDate}T00:00:00`)
  const now = new Date()
  let age = now.getFullYear() - born.getFullYear()
  if (now < new Date(now.getFullYear(), born.getMonth(), born.getDate())) age -= 1
  return age
}

export function sexLabel(gender: string | null): string {
  if (gender === "male") return "Male"
  if (gender === "female") return "Female"
  if (gender === "other") return "Other"
  return "Sex unknown"
}

export const KIND_LABEL: Record<RecordKind, string> = {
  note: "Clinical note",
  lab_report: "Lab report",
  imaging_report: "Imaging report",
  summary: "AI summary",
}

/** Plain-language names for the data quality flags set while cleaning. */
export const FLAG_LABEL: Record<string, string> = {
  conflicting_mrn: "Conflicting MRNs",
  conflicting_gender: "Conflicting sex",
  shared_mrn: "MRN shared with another record",
  possible_duplicate: "Possible duplicate patient",
  missing_birth_date: "No birth date",
  missing_gender: "No sex recorded",
  missing_mrn: "No MRN",
  missing_name: "No name",
  identity_unknown: "Identity unknown",
}

const WORD = /[a-z0-9]{3,}/gi
const STOP = new Set(["and", "the", "for", "with", "from", "that", "this", "are", "was", "has", "have", "patient"])

/** Split text into plain and matched parts so query words can be highlighted with <mark>. */
export function highlightParts(text: string, query: string): { text: string; match: boolean }[] {
  const terms = [...new Set((query.match(WORD) ?? []).map((t) => t.toLowerCase()))].filter((t) => !STOP.has(t))
  if (terms.length === 0) return [{ text, match: false }]
  const pattern = new RegExp(`\\b(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\w*`, "gi")
  const parts: { text: string; match: boolean }[] = []
  let last = 0
  for (const m of text.matchAll(pattern)) {
    const start = m.index ?? 0
    if (start > last) parts.push({ text: text.slice(last, start), match: false })
    parts.push({ text: m[0], match: true })
    last = start + m[0].length
  }
  if (last < text.length) parts.push({ text: text.slice(last), match: false })
  return parts
}
