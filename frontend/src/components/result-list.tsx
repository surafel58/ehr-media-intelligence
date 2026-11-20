import { ClipboardListIcon, FileTextIcon, FlaskConicalIcon, ScanLineIcon } from "lucide-react"
import type { KeyboardEvent } from "react"

import { Badge } from "@/components/ui/badge"
import type { RecordKind, SearchResult } from "@/lib/api"
import { KIND_LABEL, ageFrom, highlightParts, sexLabel, splitDate } from "@/lib/format"

const KIND_ICON = {
  note: FileTextIcon,
  lab_report: FlaskConicalIcon,
  imaging_report: ScanLineIcon,
  summary: ClipboardListIcon,
} satisfies Record<RecordKind, unknown>

interface ListProps {
  results: SearchResult[]
  query: string
  onOpen: (result: SearchResult) => void
  onPreload: (patientId: string) => void
}

/** Up and down arrows move between results; Home and End jump to the first and last. */
function moveFocus(event: KeyboardEvent<HTMLOListElement>) {
  const items = [...event.currentTarget.querySelectorAll<HTMLElement>("[data-result-index]")]
  const current = items.indexOf(document.activeElement as HTMLElement)
  if (current === -1) return
  const next =
    event.key === "ArrowDown" ? Math.min(current + 1, items.length - 1)
    : event.key === "ArrowUp" ? current - 1
    : event.key === "Home" ? 0
    : event.key === "End" ? items.length - 1
    : null
  if (next === null) return
  event.preventDefault()
  if (next < 0) document.getElementById("record-search")?.focus()
  else items[next].focus()
}

export function ResultList({ results, query, onOpen, onPreload }: ListProps) {
  return (
    <ol aria-label="Search results" onKeyDown={moveFocus} className="flex flex-col">
      {results.map((result, index) => (
        <li key={result.record_id} className="border-b last:border-b-0">
          <ResultEntry result={result} index={index} query={query} onOpen={onOpen} onPreload={onPreload} />
        </li>
      ))}
    </ol>
  )
}

interface EntryProps {
  result: SearchResult
  index: number
  query: string
  onOpen: (result: SearchResult) => void
  onPreload: (patientId: string) => void
}

function ResultEntry({ result, index, query, onOpen, onPreload }: EntryProps) {
  const date = splitDate(result.date)
  const Icon = KIND_ICON[result.kind]
  const percent = Math.round(result.score * 100)
  const { patient } = result
  const age = ageFrom(patient.birth_date, patient.age)
  const titleId = `result-${index}-title`
  const preload = () => onPreload(patient.id)

  return (
    <article
      aria-labelledby={titleId}
      className="group relative grid grid-cols-[3.75rem_1fr] gap-x-4 gap-y-3 rounded-lg px-2 py-4 transition-colors duration-150 hover:bg-accent/60 has-focus-visible:bg-accent/60 sm:grid-cols-[4.5rem_1fr_5.5rem] sm:px-3"
    >
      <div className="pt-0.5 tabular-nums">
        {date ? (
          <>
            <span className="block text-[15px] leading-tight font-semibold">{date.dayMonth}</span>
            <span className="block text-sm text-muted-foreground">{date.year}</span>
          </>
        ) : (
          <span className="block text-sm leading-tight text-muted-foreground">No date</span>
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={result.kind === "summary" ? "default" : "secondary"}>
            <Icon data-icon="inline-start" aria-hidden="true" />
            {KIND_LABEL[result.kind]}
          </Badge>
          <span className="text-xs text-muted-foreground">{result.resource_type}</span>
        </div>

        <h3 id={titleId} className="text-[16px] leading-snug font-semibold">
          <button
            type="button"
            data-result-index={index}
            data-focus-ring
            onClick={() => onOpen(result)}
            onMouseEnter={preload}
            onFocus={preload}
            className="rounded-sm text-left after:absolute after:inset-0 after:rounded-lg after:content-[''] focus-visible:outline-2"
          >
            {result.title}
            <span className="sr-only">, open patient record</span>
          </button>
        </h3>

        <p className="line-clamp-2 text-sm text-muted-foreground">
          {highlightParts(result.snippet, query).map((part, i) =>
            part.match ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
          )}
        </p>

        <p className="flex flex-wrap gap-x-4 gap-y-0.5 text-sm">
          <span className="font-semibold">{patient.name}</span>
          {patient.mrn ? <span className="tabular-nums">MRN {patient.mrn}</span> : null}
          <span className="text-muted-foreground">
            {sexLabel(patient.gender)}
            {age != null ? `, ${age} years` : ""}
          </span>
          {result.similar_records.length > 0 ? (
            <span className="text-muted-foreground">
              {result.similar_records.length} more {result.similar_records.length === 1 ? "copy" : "copies"} of this note
            </span>
          ) : null}
        </p>

        {result.summary_snippet && result.kind !== "summary" ? (
          <div className="rounded-md border-l-2 border-note bg-note-soft px-3 py-1.5 text-sm">
            {/* The clamp sits on the inner text so the box padding cannot reveal a cut-off third line. */}
            <p className="line-clamp-2">
              <span className="font-semibold text-note">AI summary: </span>
              {result.summary_snippet}
            </p>
          </div>
        ) : null}
      </div>

      <div className="col-start-2 flex items-center gap-2 sm:col-start-auto sm:flex-col sm:items-end sm:gap-1.5 sm:pt-0.5">
        <span className="text-[15px] font-semibold tabular-nums">
          {percent}%<span className="sr-only"> relevance</span>
        </span>
        <span aria-hidden="true" className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
          <span className="block h-full rounded-full bg-primary" style={{ width: `${percent}%` }} />
        </span>
        <span className="text-xs text-muted-foreground sm:block" aria-hidden="true">
          relevance
        </span>
      </div>
    </article>
  )
}
