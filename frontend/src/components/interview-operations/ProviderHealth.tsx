"use client"

/** AI service health (Phase 7.8): circuit breaker per step + last-hour error rate. */

import { ErrorState, LoadingState } from "@/components/ui/states"
import { opsApi, useLoaded } from "./opsApi"
import { breakerLabel, formatCount, formatRate, STEP_LABELS } from "./opsLogic"

export default function ProviderHealth() {
	const health = useLoaded(() => opsApi.health(), [])
	if (health.loading) return <LoadingState message="Checking AI service health…" />
	if (health.error || !health.data)
		return <ErrorState message={health.error ?? "Could not load health."} onRetry={health.reload} requestId={health.requestId} />
	const h = health.data
	return (
		<section aria-labelledby="ops-health" className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-slate-900">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<h2 id="ops-health" className="text-lg font-semibold">AI service health</h2>
				<button type="button" onClick={health.reload} className="rounded-md border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-50 dark:border-white/20 dark:hover:bg-white/5">
					Refresh
				</button>
			</div>
			<ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
				{h.steps.map((s) => (
					<li key={s.step} className="rounded-lg border border-slate-100 p-3 text-sm dark:border-white/5">
						<p className="font-medium">{STEP_LABELS[s.step] ?? s.step}</p>
						<p className={s.state === "open" ? "text-amber-600 dark:text-amber-300" : "text-slate-600 dark:text-slate-300"}>
							{breakerLabel(s.state)}
							{s.retry_in_seconds !== null ? ` · retry in ${s.retry_in_seconds}s` : ""}
						</p>
						<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
							Last hour: {formatCount(s.last_hour_calls)} calls, error rate {formatRate(s.last_hour_error_rate)}
						</p>
					</li>
				))}
			</ul>
			<p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{h.note}</p>
		</section>
	)
}
