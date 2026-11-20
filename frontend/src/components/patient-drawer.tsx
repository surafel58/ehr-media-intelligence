import { ChevronDownIcon, FlagIcon, InfoIcon, TriangleAlertIcon } from "lucide-react"
import { useEffect, useState } from "react"
import useSWR from "swr"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { type PatientDetail, type ResourceDetail, getJson, patientKey, resourceKey } from "@/lib/api"
import { FLAG_LABEL, ageFrom, formatDate, sexLabel } from "@/lib/format"
import { cn } from "@/lib/utils"

const GROUPS: { type: string; label: string }[] = [
  { type: "DocumentReference", label: "Notes" },
  { type: "DiagnosticReport", label: "Lab and imaging reports" },
  { type: "Observation", label: "Lab results" },
  { type: "Encounter", label: "Visits" },
]
const CONFLICT_FLAGS = new Set(["conflicting_mrn", "conflicting_gender", "shared_mrn", "possible_duplicate"])

interface Props {
  patientId: string | null
  focusRecordId: string | null
  onClose: () => void
}

export function PatientDrawer({ patientId, focusRecordId, onClose }: Props) {
  return (
    <Sheet open={patientId !== null} onOpenChange={(open) => (open ? null : onClose())}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl">
        {/* The key resets the open records whenever another result is opened. */}
        {patientId ? <DrawerBody key={`${patientId}:${focusRecordId}`} patientId={patientId} focusRecordId={focusRecordId} /> : null}
      </SheetContent>
    </Sheet>
  )
}

function DrawerBody({ patientId, focusRecordId }: { patientId: string; focusRecordId: string | null }) {
  const { data, error, isLoading } = useSWR<PatientDetail>(patientKey(patientId), getJson)
  // The record clicked in the results starts open.
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set(focusRecordId ? [focusRecordId] : []))

  // Scrolling is a DOM side effect, so it stays in an effect; no state is set here.
  const loaded = data !== undefined
  useEffect(() => {
    if (!loaded || !focusRecordId) return
    requestAnimationFrame(() => document.getElementById(`rec-${focusRecordId}`)?.scrollIntoView({ block: "center" }))
  }, [loaded, focusRecordId])

  const showRecord = (id: string) => {
    setOpenIds((current) => new Set(current).add(id))
    requestAnimationFrame(() => {
      const el = document.getElementById(`rec-${id}`)
      el?.scrollIntoView({ block: "center" })
      el?.querySelector<HTMLElement>("button")?.focus()
    })
  }

  const patient = data?.patient
  return (
    <>
        <SheetHeader className="gap-3 border-b bg-card px-5 py-5 sm:px-6">
          <SheetTitle className="pr-10 text-xl leading-tight">{patient?.name ?? "Patient record"}</SheetTitle>
          <SheetDescription className="sr-only">Patient details, AI summary and linked FHIR resources</SheetDescription>
          {patient ? <PatientBanner detail={data!} /> : isLoading ? <Skeleton className="h-12 w-full" /> : null}
        </SheetHeader>

        {error ? (
          <Alert variant="destructive" className="m-6 w-auto">
            <TriangleAlertIcon />
            <AlertDescription>This patient record could not be loaded. {String(error.message ?? "")}</AlertDescription>
          </Alert>
        ) : null}

        {data ? (
          <div className="flex flex-col gap-8 px-5 py-6 sm:px-6">
            <SummarySection detail={data} onShowRecord={showRecord} />
            <Separator />
            <RecordsSection
              detail={data}
              openIds={openIds}
              onToggle={(id, open) =>
                setOpenIds((current) => {
                  const next = new Set(current)
                  if (open) next.add(id)
                  else next.delete(id)
                  return next
                })
              }
            />
          </div>
        ) : isLoading ? (
          <div className="flex flex-col gap-3 px-6 py-6" aria-busy="true" aria-label="Loading patient record">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : null}
    </>
  )
}

function PatientBanner({ detail }: { detail: PatientDetail }) {
  const { patient, flags } = detail
  const age = ageFrom(patient.birth_date, patient.age)
  const fields: [string, string][] = [
    ["MRN", patient.mrn ?? "None"],
    ["Sex", sexLabel(patient.gender)],
    ["Born", patient.birth_date ? `${formatDate(patient.birth_date)}${age != null ? `, ${age} years` : ""}` : age != null ? `${age} years (stated)` : "Unknown"],
    ["Patient ID", patient.id],
  ]
  return (
    <div className="flex flex-col gap-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        {fields.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="truncate text-sm font-semibold tabular-nums" title={value}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
      {flags.length > 0 ? (
        <ul aria-label="Data quality flags" className="flex flex-wrap gap-1.5">
          {flags.map((flag) => (
            <li key={flag}>
              <Badge variant={CONFLICT_FLAGS.has(flag) ? "destructive" : "outline"}>
                {CONFLICT_FLAGS.has(flag) ? <FlagIcon data-icon="inline-start" aria-hidden="true" /> : null}
                {FLAG_LABEL[flag] ?? flag.replaceAll("_", " ")}
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function SummarySection({ detail, onShowRecord }: { detail: PatientDetail; onShowRecord: (id: string) => void }) {
  const titles = new Map(detail.resources.map((r) => [r.id, r]))
  // Long record names are shortened by CSS only, so the visible text stays part of the accessible name.
  const cite = (ids: string[]) =>
    ids.filter((id) => titles.has(id)).map((id) => {
      const r = titles.get(id)!
      const date = r.date ? `${formatDate(r.date)}, ` : ""
      return (
        <button
          key={id}
          type="button"
          data-focus-ring
          title={`${date}${r.title}`}
          onClick={() => onShowRecord(id)}
          className="inline-flex max-w-full rounded-sm text-sm whitespace-pre text-primary underline-offset-2 hover:underline"
        >
          <span className="shrink-0 tabular-nums">{date}</span>
          <span className="max-w-60 truncate">{r.title}</span>
        </button>
      )
    })

  if (!detail.summary) {
    return (
      <section aria-labelledby="summary-heading" className="flex flex-col gap-2">
        <h3 id="summary-heading" className="text-base font-semibold">AI summary</h3>
        <p className="text-sm text-muted-foreground">
          No AI summary yet. Summaries are written in batches, and this patient is still in the queue.
        </p>
      </section>
    )
  }

  const { summary, model, generated_at, quality, disclaimer } = detail.summary
  const passed = quality.filter((q) => q.passed).length
  return (
    <section aria-labelledby="summary-heading" className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h3 id="summary-heading" className="text-base font-semibold">AI summary</h3>
        <Badge variant="outline">{summary.confidence[0].toUpperCase() + summary.confidence.slice(1)} confidence</Badge>
      </div>

      <div className="flex flex-col gap-4 rounded-lg border bg-card px-4 py-4">
        <p className="text-[15px] leading-relaxed">{summary.overview}</p>
        <dl className="flex flex-col gap-3 text-sm">
          <div>
            <dt className="text-muted-foreground">Chief concern</dt>
            <dd className="font-semibold">{summary.chief_concern}</dd>
          </div>
          {summary.key_diagnoses.length > 0 ? (
            <div>
              <dt className="text-muted-foreground">Key diagnoses</dt>
              <dd>
                <ul className="flex flex-col gap-1.5">
                  {summary.key_diagnoses.map((d) => (
                    <li key={d.text}>
                      <span className="font-semibold">{d.text}</span>
                      <span className="ml-2 inline-flex flex-wrap gap-x-2">{cite(d.source_ids)}</span>
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          ) : null}
          {summary.recent_media.length > 0 ? (
            <div>
              <dt className="text-muted-foreground">Recent imaging and labs</dt>
              <dd>
                <ul className="flex flex-col gap-1.5">
                  {summary.recent_media.map((m) => (
                    <li key={`${m.source_id}-${m.result}`} className="flex flex-col">
                      <span>
                        <span className="tabular-nums text-muted-foreground">{m.date ? formatDate(m.date) : "No date"}</span>
                        <span className="ml-2">{m.result}</span>
                      </span>
                      <span>{cite([m.source_id])}</span>
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          ) : null}
          {summary.anomalies.length > 0 ? (
            <div>
              <dt className="text-muted-foreground">Flagged</dt>
              <dd>
                <ul className="flex flex-col gap-1.5">
                  {summary.anomalies.map((a) => (
                    <li key={a.text} className="flex gap-2">
                      {a.kind === "clinical" ? (
                        <TriangleAlertIcon aria-label="Clinical" className="mt-0.5 size-4 shrink-0 text-flag" />
                      ) : (
                        <InfoIcon aria-label="Data quality" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                      )}
                      <span>
                        {a.text}
                        <span className="ml-2 inline-flex flex-wrap gap-x-2">{cite(a.source_ids)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          ) : null}
          <div>
            <dt className="text-muted-foreground">Confidence</dt>
            <dd>{summary.confidence_reason}</dd>
          </div>
        </dl>
      </div>

      <Alert>
        <InfoIcon />
        <AlertDescription>
          <p>{disclaimer}</p>
          <p className="text-xs">
            Written by {model} on {formatDate(generated_at.slice(0, 10))}. {passed} of {quality.length} automatic checks
            passed.
          </p>
        </AlertDescription>
      </Alert>
    </section>
  )
}

interface RecordsProps {
  detail: PatientDetail
  openIds: Set<string>
  onToggle: (id: string, open: boolean) => void
}

function RecordsSection({ detail, openIds, onToggle }: RecordsProps) {
  return (
    <section aria-labelledby="records-heading" className="flex flex-col gap-5">
      <h3 id="records-heading" className="text-base font-semibold">
        Linked FHIR resources <span className="font-normal text-muted-foreground">{detail.resources.length}</span>
      </h3>
      {GROUPS.map(({ type, label }) => {
        const items = detail.resources.filter((r) => r.resource_type === type)
        if (items.length === 0) return null
        return (
          <div key={type} className="flex flex-col gap-1">
            <h4 className="text-sm font-semibold">
              {label} <span className="font-normal text-muted-foreground">{items.length}, {type}</span>
            </h4>
            <ul className="flex flex-col">
              {items.map((r) => (
                <li key={r.id} id={`rec-${r.id}`} className="border-b last:border-b-0">
                  <Collapsible open={openIds.has(r.id)} onOpenChange={(open) => onToggle(r.id, open)}>
                    <CollapsibleTrigger
                      data-focus-ring
                      className="group flex w-full items-center gap-3 rounded-md px-1 py-2.5 text-left text-sm hover:bg-accent/60"
                    >
                      <span className="w-24 shrink-0 tabular-nums text-muted-foreground">{formatDate(r.date)}</span>
                      <span className="min-w-0 flex-1 truncate font-semibold">{r.title}</span>
                      {r.detail ? <ValueText value={r.detail} className="shrink-0 tabular-nums" /> : null}
                      {r.status && !r.detail ? (
                        <span className="hidden text-xs text-muted-foreground sm:inline">{r.status}</span>
                      ) : null}
                      <ChevronDownIcon
                        aria-hidden="true"
                        className="size-4 shrink-0 text-muted-foreground transition-transform duration-150 group-data-[state=open]:rotate-180"
                      />
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      {openIds.has(r.id) ? <RecordBody type={r.resource_type} id={r.id} /> : null}
                    </CollapsibleContent>
                  </Collapsible>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </section>
  )
}

/** A lab value; high, low and abnormal values use the flag color, with the word itself so color is not the only signal. */
function ValueText({ value, className }: { value: string; className?: string }) {
  const abnormal = /\b(high|low|abnormal)$/.test(value)
  return <span className={cn(abnormal ? "font-semibold text-flag" : "text-muted-foreground", className)}>{value}</span>
}

interface FhirFields {
  conclusion?: string
  referenceRange?: { low?: { value?: number }; high?: { value?: number } }[]
  period?: { start?: string; end?: string }
  reasonCode?: { text?: string }[]
}

function RecordBody({ type, id }: { type: string; id: string }) {
  const { data, error } = useSWR<ResourceDetail>(resourceKey(type, id), getJson, { revalidateOnFocus: false })
  if (error) return <p className="px-1 pb-3 text-sm text-destructive">This record could not be loaded.</p>
  if (!data) return <Skeleton className="mx-1 mb-3 h-16" />

  const res = data.resource as FhirFields
  const facts: string[] = []
  const range = res.referenceRange?.[0]
  if (range) facts.push(`Reference range ${range.low?.value ?? ""} to ${range.high?.value ?? ""}`)
  if (res.period?.start) facts.push(`${formatDate(res.period.start)}${res.period.end ? ` to ${formatDate(res.period.end)}` : ""}`)
  if (res.reasonCode?.[0]?.text) facts.push(`Reason: ${res.reasonCode[0].text}`)

  const empty = !data.text && data.results.length === 0 && facts.length === 0 && !res.conclusion
  return (
    <div className="mx-1 mb-3 flex max-h-96 flex-col gap-2.5 overflow-y-auto rounded-md bg-muted px-3 py-2.5 text-sm">
      {res.conclusion ? <p className="font-semibold">{res.conclusion}</p> : null}
      {data.results.length > 0 ? (
        <table className="w-full text-left">
          <caption className="sr-only">Results</caption>
          <tbody>
            {data.results.map((r, i) => (
              <tr key={`${r.name}-${i}`} className="border-b border-border/60 last:border-b-0">
                <th scope="row" className="py-1 pr-3 font-normal">{r.name}</th>
                <td className="py-1 text-right tabular-nums">{r.value ? <ValueText value={r.value} /> : "No value"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {data.text ? <p className="leading-relaxed whitespace-pre-wrap">{data.text}</p> : null}
      {facts.length > 0 ? <p className="text-muted-foreground">{facts.join(". ")}</p> : null}
      {empty ? <p className="text-muted-foreground">No text in this record.</p> : null}
    </div>
  )
}
