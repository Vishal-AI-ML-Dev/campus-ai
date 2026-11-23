"use client"

/**
 * Institute AI usage overview (Phase 7.8): tiles, a small daily chart, breakdowns
 * by step / model / outcome / mode, and cost - shown only when the backend has an
 * estimate from an approved price table ("Not configured" otherwise, never 0).
 */

import { ErrorState, LoadingState } from "@/components/ui/states"
import { opsApi, useLoaded, type BreakdownRow, type UsageSummary } from "./opsApi"
import {
	barHeights,
	describeCost,
	formatCount,
	formatLatency,
	outcomeLabel,
	STEP_LABELS,
	tokenUsageHint,
} from "./opsLogic"

const card = "rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-slate-900"

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
	return (
		<div className={card}>
			<p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
			<p className="mt-1 text-2xl font-semibold">{value}</p>
			{hint ? <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p> : null}
		</div>
	)
}

function Breakdown({ title, rows, label }: { title: string; rows: BreakdownRow[]; label: (k: string) => string }) {
	return (
		<div className={card}>
			<h3 className="text-sm font-semibold">{title}</h3>
			{rows.length === 0 ? (
				<p className="mt-2 text-sm text-slate-500 dark:text-slate-400">No AI calls in this period.</p>
			) : (
				<div className="mt-2 overflow-x-auto">
					<table className="w-full text-left text-sm">
						<thead className="text-xs text-slate-500 dark:text-slate-400">
							<tr>
								<th scope="col" className="py-1 pr-3 font-medium">Name</th>
								<th scope="col" className="py-1 pr-3 font-medium">Calls</th>
								<th scope="col" className="py-1 pr-3 font-medium">Failures</th>
								<th scope="col" className="py-1 pr-3 font-medium">Tokens in / out</th>
								<th scope="col" className="py-1 font-medium">Avg latency</th>
							</tr>
						</thead>
						<tbody>
							{rows.map((r) => (
								<tr key={r.key} className="border-t border-slate-100 dark:border-white/5">
									<td className="py-1 pr-3">{label(r.key)}</td>
									<td className="py-1 pr-3">{formatCount(r.ai_calls)}</td>
									<td className="py-1 pr-3">{formatCount(r.ai_failures)}</td>
									<td className="py-1 pr-3">
										{formatCount(r.input_tokens)} / {formatCount(r.output_tokens)}
									</td>
									<td className="py-1">{formatLatency(r.avg_latency_ms)}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
		</div>
	)
}

function DailyChart({ data }: { data: UsageSummary["daily"] }) {
	const heights = barHeights(data.map((d) => d.ai_calls))
	return (
		<div className={card}>
			<h3 className="text-sm font-semibold">AI calls per day</h3>
			<div className="mt-3 flex h-24 items-end gap-0.5" aria-hidden="true">
				{data.map((d, i) => (
					<div
						key={d.day}
						className={`flex-1 rounded-t ${d.ai_failures > 0 ? "bg-amber-400" : "bg-indigo-400"}`}
						style={{ height: `${Math.max(heights[i], d.ai_calls > 0 ? 4 : 1)}%` }}
						title={`${d.day}: ${d.ai_calls} calls, ${d.ai_failures} failures`}
					/>
				))}
			</div>
			{/* Text equivalent for screen readers. */}
			<table className="sr-only">
				<caption>AI calls per day</caption>
				<tbody>
					{data.map((d) => (
						<tr key={d.day}>
							<th scope="row">{d.day}</th>
							<td>{d.ai_calls} calls</td>
							<td>{d.ai_failures} failures</td>
							<td>{d.sessions_created} interviews</td>
						</tr>
					))}
				</tbody>
			</table>
			<p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
				Amber days had at least one failed AI call.
			</p>
		</div>
	)
}

export default function UsageOverview({ days }: { days: number }) {
	const usage = useLoaded(() => opsApi.usage(days), [days])
	if (usage.loading) return <LoadingState message="Loading usage…" />
	if (usage.error || !usage.data)
		return <ErrorState message={usage.error ?? "Could not load usage."} onRetry={usage.reload} requestId={usage.requestId} />
	const u = usage.data
	const t = u.totals
	const cost = describeCost(u.cost)
	return (
		<section aria-labelledby="ops-usage" className="space-y-4">
			<h2 id="ops-usage" className="text-lg font-semibold">Usage (last {u.days} days)</h2>
			<div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
				<Tile
					label="Interviews"
					value={formatCount(t.sessions_created)}
					hint={`${formatCount(t.sessions_completed)} completed · ${formatCount(t.sessions_abandoned)} ended early`}
				/>
				<Tile
					label="AI calls"
					value={formatCount(t.ai_calls)}
					hint={`${formatCount(t.ai_failures)} failed · ${formatCount(t.circuit_open)} skipped by the breaker`}
				/>
				<Tile
					label="Tokens (in / out)"
					value={`${formatCount(t.input_tokens)} / ${formatCount(t.output_tokens)}`}
					hint={tokenUsageHint(t.calls_with_unknown_tokens, t.calls_with_partial_tokens)}
				/>
				<Tile
					label="Latency (avg / p95)"
					value={`${formatLatency(t.avg_latency_ms)} / ${formatLatency(t.p95_latency_ms)}`}
					hint={`${formatCount(t.quota_blocked)} new interview(s) blocked by quotas`}
				/>
			</div>
			<div className={card}>
				<h3 className="text-sm font-semibold">Estimated AI cost</h3>
				<p className="mt-1 text-xl font-semibold">{cost.headline}</p>
				<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{cost.detail}</p>
			</div>
			<DailyChart data={u.daily} />
			<div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
				<Breakdown title="By step" rows={u.by_step} label={(k) => STEP_LABELS[k] ?? k} />
				<Breakdown title="By provider / model" rows={u.by_model} label={(k) => k} />
			</div>
			<div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
				<div className={card}>
					<h3 className="text-sm font-semibold">Outcomes</h3>
					<ul className="mt-2 space-y-1 text-sm">
						{Object.keys(u.by_outcome).length === 0 ? (
							<li className="text-slate-500 dark:text-slate-400">No AI calls in this period.</li>
						) : (
							Object.entries(u.by_outcome).map(([k, v]) => (
								<li key={k} className="flex justify-between gap-3">
									<span>{outcomeLabel(k)}</span>
									<span className="font-medium">{formatCount(v)}</span>
								</li>
							))
						)}
					</ul>
				</div>
				<div className={card}>
					<h3 className="text-sm font-semibold">Interview modes</h3>
					<ul className="mt-2 space-y-1 text-sm">
						{Object.keys(u.modes).length === 0 ? (
							<li className="text-slate-500 dark:text-slate-400">No interviews in this period.</li>
						) : (
							Object.entries(u.modes).map(([k, v]) => (
								<li key={k} className="flex justify-between gap-3">
									<span className="capitalize">{k}</span>
									<span className="font-medium">{formatCount(v)}</span>
								</li>
							))
						)}
					</ul>
				</div>
			</div>
		</section>
	)
}
