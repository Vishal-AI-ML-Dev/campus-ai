"use client"

/**
 * TPO / admin Placement Hub home (Phase 9.3): genuine institute-scoped counts
 * from GET /placement/dashboard - drives, deadlines, applications, students,
 * campaigns and pending actions. Nothing is estimated or invented here.
 */

import Link from "next/link"
import { useState } from "react"
import type { Role } from "@/lib/auth"
import { EmptyState, ErrorState, SkeletonList } from "@/components/ui/states"
import { insightsApi } from "./placementApi"
import { cardClass, Field, inputClass } from "./HubUi"
import { formatDateTime, LIFECYCLE_LABEL, LIFECYCLES, ratio, STATUS_LABEL, APP_STATUSES } from "./placementLogic"
import { useAsync } from "./useAsync"

const ACTION_LINK: Record<string, string> = {
	drafts: "/dashboard/placement-hub/drives",
	window_closed: "/dashboard/placement-hub/drives",
	awaiting_decision: "/dashboard/placement-hub/drives",
	profiles_missing: "/dashboard/placement-hub/import",
	campaigns_overdue: "/dashboard/placement-hub/campaigns",
}

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
	return (
		<div className={cardClass}>
			<p className="text-xs text-slate-600 dark:text-slate-400">{label}</p>
			<p className="mt-1 text-2xl font-bold tabular-nums">{value}</p>
			{hint ? <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p> : null}
		</div>
	)
}

export default function TpoHome({ role }: { role: Role }) {
	const [year, setYear] = useState("")
	const valid = year === "" || /^\d{4}-\d{2}$/.test(year)
	const res = useAsync(valid ? `dash:${year}` : null, () => insightsApi.dashboard(year))
	const d = res.data

	return (
		<div className="space-y-6">
			<div className="flex flex-wrap items-end justify-between gap-3">
				<div>
					<h2 className="text-2xl font-bold">Placement overview</h2>
					<p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
						{role === "admin" ? "Read-only view of your institute's placement work." : "Everything that needs your attention."}
					</p>
				</div>
				<div className="w-40">
					<Field id="dash-year" label="Academic year" hint={valid ? "e.g. 2026-27" : "Use the form 2026-27"}>
						<input
							id="dash-year"
							className={inputClass}
							placeholder="All years"
							maxLength={7}
							value={year}
							aria-invalid={!valid}
							aria-describedby="dash-year-hint"
							onChange={(e) => setYear(e.target.value.trim())}
						/>
					</Field>
				</div>
			</div>

			{res.loading && !d ? (
				<SkeletonList rows={3} />
			) : res.error ? (
				<ErrorState message={res.error} onRetry={res.reload} requestId={res.requestId} />
			) : !d ? null : (
				<>
					<section aria-label="Key numbers" className="grid grid-cols-2 gap-3 md:grid-cols-4">
						<Tile label="Active drives" value={d.drives.active} hint={`${d.drives.total} drives in total`} />
						<Tile label="Applications" value={d.applications.total} hint={`${d.shortlisting.pending} awaiting a first decision`} />
						<Tile
							label="Shortlisting progress"
							value={d.shortlisting.decided_percentage === null ? "—" : `${d.shortlisting.decided_percentage}%`}
							hint={`${d.shortlisting.decided} decided (withdrawn excluded)`}
						/>
						<Tile label="Students selected" value={d.students.selected} hint={`${d.students.placed} placed incl. accepted offers`} />
						<Tile label="Available for placement" value={d.students.available_for_placement} hint={`of ${d.students.registered} registered students`} />
						<Tile label="Placed" value={d.students.placed} hint={ratio(d.students.placed, d.students.registered) + " of registered"} />
						<Tile label="Active campaigns" value={d.campaigns.active} hint={`${d.campaigns.completed} of ${d.campaigns.assigned} assignments completed`} />
						<Tile label="Missing academic records" value={d.students.without_academic_records} />
					</section>

					<div className="grid gap-6 lg:grid-cols-2">
						<section className={cardClass} aria-labelledby="actions-h">
							<h3 id="actions-h" className="font-semibold">
								Pending actions
							</h3>
							{d.pending_actions.length === 0 ? (
								<p className="mt-2 text-sm text-slate-600 dark:text-slate-400">Nothing needs attention right now.</p>
							) : (
								<ul className="mt-2 divide-y divide-slate-200 text-sm dark:divide-white/10">
									{d.pending_actions.map((a) => (
										<li key={a.kind} className="flex items-center justify-between gap-2 py-2">
											<Link href={ACTION_LINK[a.kind] ?? "/dashboard/placement-hub"} className="text-indigo-700 hover:underline dark:text-indigo-300">
												{a.label}
											</Link>
											<span className="font-semibold tabular-nums">{a.count}</span>
										</li>
									))}
								</ul>
							)}
						</section>

						<section className={cardClass} aria-labelledby="deadlines-h">
							<h3 id="deadlines-h" className="font-semibold">
								Deadlines in the next 14 days
							</h3>
							{d.upcoming_deadlines.length === 0 ? (
								<div className="mt-2">
									<EmptyState title="No upcoming deadlines" />
								</div>
							) : (
								<ul className="mt-2 divide-y divide-slate-200 text-sm dark:divide-white/10">
									{d.upcoming_deadlines.map((x) => (
										<li key={x.drive_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
											<Link href={`/dashboard/placement-hub/drives/${x.drive_id}`} className="text-indigo-700 hover:underline dark:text-indigo-300">
												{x.company_name} {"—"} {x.role_title}
											</Link>
											<span className="text-slate-600 dark:text-slate-300">{formatDateTime(x.closes_at)}</span>
										</li>
									))}
								</ul>
							)}
						</section>
					</div>

					<div className="grid gap-6 lg:grid-cols-2">
						<section className={cardClass} aria-labelledby="lc-h">
							<h3 id="lc-h" className="font-semibold">
								Drives by status
							</h3>
							<table className="mt-2 w-full text-sm">
								<caption className="sr-only">Number of drives in each status</caption>
								<tbody>
									{LIFECYCLES.map((l) => (
										<tr key={l} className="border-t border-slate-200 dark:border-white/10">
											<th scope="row" className="py-1.5 text-left font-normal">{LIFECYCLE_LABEL[l]}</th>
											<td className="py-1.5 text-right tabular-nums">{d.drives.by_lifecycle[l]}</td>
										</tr>
									))}
								</tbody>
							</table>
						</section>
						<section className={cardClass} aria-labelledby="st-h">
							<h3 id="st-h" className="font-semibold">
								Applications by status
							</h3>
							<table className="mt-2 w-full text-sm">
								<caption className="sr-only">Number of applications in each status</caption>
								<tbody>
									{APP_STATUSES.map((s) => (
										<tr key={s} className="border-t border-slate-200 dark:border-white/10">
											<th scope="row" className="py-1.5 text-left font-normal">{STATUS_LABEL[s]}</th>
											<td className="py-1.5 text-right tabular-nums">{d.applications.by_status[s]}</td>
										</tr>
									))}
								</tbody>
							</table>
						</section>
					</div>
					{d.notes.map((n) => (
						<p key={n} className="text-xs text-slate-500 dark:text-slate-400">
							{n}
						</p>
					))}
				</>
			)}
		</div>
	)
}
