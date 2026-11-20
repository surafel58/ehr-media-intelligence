import { Suspense, lazy, useCallback, useState } from "react"
import type { DateRange } from "react-day-picker"
import useSWR, { preload } from "swr"

import { AppHeader } from "@/components/app-header"
import { ResultList } from "@/components/result-list"
import { SearchControls } from "@/components/search-controls"
import { NoResults, ResultsSkeleton, SearchError, StartState } from "@/components/search-states"
import { useDebouncedValue } from "@/hooks/use-debounced-value"
import { type ResourceType, type SearchResult, getJson, patientKey, searchRecords } from "@/lib/api"
import { toIsoDate } from "@/lib/format"

// The drawer is only needed after a result is opened, so its code loads on demand (and early on hover or focus).
const loadDrawer = () => import("@/components/patient-drawer")
const PatientDrawer = lazy(() => loadDrawer().then((m) => ({ default: m.PatientDrawer })))

type SearchKey = readonly ["search", string, ResourceType | null, string | null, string | null]

const fetchSearch = ([, query, resourceType, dateFrom, dateTo]: SearchKey) =>
  searchRecords({ query, resourceType, dateFrom, dateTo })

export function App() {
  const [query, setQuery] = useState("")
  const [resourceType, setResourceType] = useState<ResourceType | null>(null)
  const [range, setRange] = useState<DateRange | undefined>()
  const [selected, setSelected] = useState<{ patientId: string; recordId: string } | null>(null)

  const debounced = useDebouncedValue(query.trim(), 250)
  const key: SearchKey | null =
    debounced.length >= 2 ? ["search", debounced, resourceType, toIsoDate(range?.from), toIsoDate(range?.to)] : null

  const { data, error, isLoading, isValidating, mutate } = useSWR(key, fetchSearch, {
    keepPreviousData: true,
    revalidateOnFocus: false,
  })

  const hasFilters = resourceType !== null || range?.from !== undefined
  const clearFilters = useCallback(() => {
    setResourceType(null)
    setRange(undefined)
  }, [])
  const [drawerUsed, setDrawerUsed] = useState(false)
  const openResult = useCallback((result: SearchResult) => {
    setDrawerUsed(true)
    setSelected({ patientId: result.patient.id, recordId: result.record_id })
  }, [])
  const preloadPatient = useCallback((id: string) => {
    void loadDrawer()
    void preload(patientKey(id), getJson)
  }, [])

  const typing = query.trim() !== debounced
  const busy = typing || isLoading || isValidating
  const showStart = key === null
  const results = data?.results ?? []

  return (
    <div className="flex min-h-svh flex-col">
      <AppHeader />
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6 sm:py-10">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-[26px] leading-tight font-semibold tracking-tight sm:text-[30px]">Search patient records</h1>
          <p className="max-w-2xl text-[15px] text-muted-foreground">
            Notes, lab and imaging reports, and AI summaries, ranked by meaning rather than exact words.
          </p>
        </div>

        <SearchControls
          query={query}
          onQueryChange={setQuery}
          resourceType={resourceType}
          onResourceTypeChange={setResourceType}
          range={range}
          onRangeChange={setRange}
          busy={busy}
        />

        <p className="sr-only" role="status" aria-live="polite">
          {key && data && !busy ? `${results.length} ${results.length === 1 ? "result" : "results"} for ${data.query}` : ""}
        </p>

        <section aria-label="Results" aria-busy={busy} className="flex flex-col">
          {showStart ? (
            <StartState onPick={setQuery} />
          ) : error && !data ? (
            <SearchError message={error.message} onRetry={() => void mutate()} />
          ) : !data ? (
            <ResultsSkeleton />
          ) : results.length === 0 ? (
            <NoResults query={data.query} hasFilters={hasFilters} onClearFilters={clearFilters} onPick={setQuery} />
          ) : (
            <>
              <div className="flex items-baseline justify-between gap-4 border-b pb-2 text-sm text-muted-foreground">
                <span>
                  <span className="font-semibold text-foreground">{results.length}</span> best{" "}
                  {results.length === 1 ? "match" : "matches"} for "{data.query}"
                </span>
                <span className="whitespace-nowrap tabular-nums">{busy ? "Updating" : `${Math.round(data.took_ms)} ms`}</span>
              </div>
              <div className={busy ? "opacity-60 transition-opacity duration-150" : "transition-opacity duration-150"}>
                <ResultList results={results} query={data.query} onOpen={openResult} onPreload={preloadPatient} />
              </div>
            </>
          )}
        </section>
      </main>

      <footer className="border-t">
        <p className="mx-auto max-w-5xl px-4 py-4 text-xs text-muted-foreground sm:px-6">
          Synthetic and de-identified sample data. AI summaries support review and are not clinical decisions.
        </p>
      </footer>

      {drawerUsed ? (
        <Suspense fallback={null}>
          <PatientDrawer
            patientId={selected?.patientId ?? null}
            focusRecordId={selected?.recordId ?? null}
            onClose={() => setSelected(null)}
          />
        </Suspense>
      ) : null}
    </div>
  )
}
