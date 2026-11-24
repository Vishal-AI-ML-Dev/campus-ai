"use client"

/** Eligibility v2 across the institute's students for one drive (Phase 9.2). */

import { Fragment, useState } from "react"
import { EmptyState, ErrorState, SkeletonList } from "@/components/ui/states"
import { placementApi } from "./placementApi"
import { Pager, secondaryButtonClass, YesNo } from "./HubUi"
import { PLACEMENT_STATUS_LABEL } from "./placementLogic"
import { useAsync } from "./useAsync"

const LIMIT = 50

export default function EligibilityPanel({ driveId }: { driveId: number }) {
	const [eligibleOnly, setEligibleOnly] = useState(false)
	const [offset, setOffset] = useState(0)
	const [open, setOpen] = useState<number | null>(null)
	const res = useAsync(JSON.stringify({ driveId, eligibleOnly, offset }), () =>
		placementApi.eligibility(driveId, { eligible_only: eligibleOnly, limit: LIMIT, offset }),
	)
	const eligibleCount = res.data?.headers.get("X-Eligible-Count")
	return (
		<div>
			<div className="flex flex-wrap items-center justify-between gap-3">
				<p className="text-sm text-slate-600 dark:text-slate-300" aria-live="polite">
					{eligibleCount != null ? `${eligibleCount} eligible student${eligibleCount === "1" ? "" : "s"}` : ""}
				</p>
				<label className="flex items-center gap-2 text-sm">
					<input
						type="checkbox"
						className="h-4 w-4"
						checked={eligibleOnly}
						onChange={(e) => {
							setOffset(0)
							setEligibleOnly(e.target.checked)
						}}
					/>
					Eligible only
				</label>
			</div>
			<div className="mt-3">
				{res.loading && !res.data ? (
					<SkeletonList rows={3} />
				) : res.error ? (
					<ErrorState message={res.error} onRetry={res.reload} requestId={res.requestId} />
				) : !res.data || res.data.items.length === 0 ? (
					<EmptyState title="No students to show" />
				) : (
					<div className="overflow-x-auto relative [contain:inline-size] rounded-xl border border-slate-200 dark:border-white/10">
						<table className="min-w-full text-left text-sm">
							<caption className="sr-only">Eligibility of students for this drive</caption>
							<thead className="bg-slate-50 text-xs uppercase text-slate-600 dark:bg-white/5 dark:text-slate-300">
								<tr>
									<th scope="col" className="px-3 py-2">Student</th>
									<th scope="col" className="px-3 py-2">Eligible</th>
									<th scope="col" className="px-3 py-2">CGPA</th>
									<th scope="col" className="hidden px-3 py-2 md:table-cell">Attendance</th>
									<th scope="col" className="hidden px-3 py-2 md:table-cell">Status</th>
									<th scope="col" className="px-3 py-2">
										<span className="sr-only">Reasons</span>
									</th>
								</tr>
							</thead>
							<tbody className="divide-y divide-slate-200 dark:divide-white/10">
								{res.data.items.map((r) => (
									<Fragment key={r.student_id}>
										<tr>
											<td className="px-3 py-2">
												<div className="font-medium">{r.full_name}</div>
												<div className="text-xs text-slate-500 dark:text-slate-400">
													{[r.department_name, r.graduation_year].filter(Boolean).join(" · ") || "—"}
													{r.applied ? " · applied" : ""}
													{r.placed ? " · placed" : ""}
												</div>
											</td>
											<td className="px-3 py-2">
												<YesNo ok={r.eligible} yes="Yes" no="No" />
											</td>
											<td className="px-3 py-2">{r.cgpa}</td>
											<td className="hidden px-3 py-2 md:table-cell">{r.attendance}%</td>
											<td className="hidden px-3 py-2 md:table-cell">{PLACEMENT_STATUS_LABEL[r.placement_status]}</td>
											<td className="px-3 py-2">
												<button
													type="button"
													className={secondaryButtonClass}
													aria-expanded={open === r.student_id}
													onClick={() => setOpen(open === r.student_id ? null : r.student_id)}
												>
													{open === r.student_id ? "Hide" : "Why"}
												</button>
											</td>
										</tr>
										{open === r.student_id ? (
											<tr>
												<td colSpan={6} className="bg-slate-50 px-3 py-2 dark:bg-white/5">
													<ul className="space-y-1 text-xs">
														{r.reasons.map((x) => (
															<li key={x.criterion}>
																<span aria-hidden="true">{x.passed ? "✓" : "✗"}</span>{" "}
																<span className="sr-only">{x.passed ? "met:" : "not met:"}</span>
																<span className="font-medium">{x.criterion}</span>: {x.detail}
															</li>
														))}
													</ul>
												</td>
											</tr>
										) : null}
									</Fragment>
								))}
							</tbody>
						</table>
					</div>
				)}
				{res.data ? <Pager offset={offset} limit={LIMIT} total={res.data.total} onChange={setOffset} /> : null}
			</div>
		</div>
	)
}
