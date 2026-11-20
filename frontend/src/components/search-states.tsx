import { SearchXIcon, ServerOffIcon } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"

const EXAMPLE_QUERIES = [
  "abnormal blood glucose",
  "heart failure with a leg wound",
  "bone scan for prostate cancer",
  "headache with visual aura",
  "chronic pain on opioids",
]

function Examples({ onPick }: { onPick: (query: string) => void }) {
  return (
    <div className="flex flex-wrap justify-center gap-2">
      {EXAMPLE_QUERIES.map((q) => (
        <Button key={q} variant="outline" size="lg" className="bg-card font-normal" onClick={() => onPick(q)}>
          {q}
        </Button>
      ))}
    </div>
  )
}

export function StartState({ onPick }: { onPick: (query: string) => void }) {
  return (
    <Empty className="py-14">
      <EmptyHeader>
        <EmptyTitle role="heading" aria-level={2} className="text-xl">Find any record in plain language</EmptyTitle>
        <EmptyDescription className="max-w-md text-[15px]">
          Describe what you are looking for the way you would ask a colleague. Each result shows the record, the
          patient, and their AI summary.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent className="max-w-2xl">
        <Examples onPick={onPick} />
      </EmptyContent>
    </Empty>
  )
}

export function NoResults({
  query,
  hasFilters,
  onClearFilters,
  onPick,
}: {
  query: string
  hasFilters: boolean
  onClearFilters: () => void
  onPick: (query: string) => void
}) {
  return (
    <Empty className="py-12">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <SearchXIcon />
        </EmptyMedia>
        <EmptyTitle role="heading" aria-level={2}>No records match "{query}"</EmptyTitle>
        <EmptyDescription>
          {hasFilters ? "The filters may be hiding matches. Clear them, or try broader words." : "Try fewer or broader words, such as:"}
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent className="max-w-2xl">
        {hasFilters ? <Button onClick={onClearFilters}>Clear filters</Button> : <Examples onPick={onPick} />}
      </EmptyContent>
    </Empty>
  )
}

export function SearchError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert variant="destructive" className="my-6">
      <ServerOffIcon />
      <AlertTitle>Search is unavailable</AlertTitle>
      <AlertDescription>
        <p>{message} Start it with <code className="font-semibold">uv run python -m ehr_platform.api</code>, then try again.</p>
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-2">
          Try again
        </Button>
      </AlertDescription>
    </Alert>
  )
}

export function ResultsSkeleton() {
  return (
    <div aria-hidden="true" className="flex flex-col">
      {[0, 1, 2].map((i) => (
        <div key={i} className="grid grid-cols-[3.75rem_1fr] gap-4 border-b px-2 py-4 sm:grid-cols-[4.5rem_1fr_5.5rem] sm:px-3">
          <div className="flex flex-col gap-1.5">
            <Skeleton className="h-4 w-12" />
            <Skeleton className="h-3.5 w-9" />
          </div>
          <div className="flex flex-col gap-2">
            <Skeleton className="h-5 w-28" />
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-1/2" />
          </div>
          <Skeleton className="hidden h-5 w-12 justify-self-end sm:block" />
        </div>
      ))}
    </div>
  )
}
