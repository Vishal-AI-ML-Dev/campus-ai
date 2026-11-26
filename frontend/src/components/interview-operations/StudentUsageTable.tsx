"use client"

/** Per-student usage counts (Phase 7.8) - counts only, never interview content. */

import { useState } from "react"
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states"
import { opsApi, useLoaded } from "./opsApi"
import { formatCount } from "./opsLogic"

const PAGE = 25

export default function StudentUsageTable({ days }: { days: number }) {
	const [offset, setOffset] = useState(0)
	const rows = useLoaded(() => opsApi.students(days, PAGE, offset), [days, offset])
	if (rows.loading) return <LoadingState message="Loading students…" />
	if (rows.error || !rows.data)
		return <ErrorState message={rows.error ?? "Could not load student usage."} onRetry={rows.reload} requestId={rows.requestId} />
	return (
		<section aria-labelledby="ops-students" className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-slate-900">
			<h2 id="ops-students" className="text-lg font-semibold">Students</h2>
			{rows.data.length === 0 && offset === 0 ? (
				<EmptyState title="No student activity in this period." />
			) : (
				<>
					<div className="mt-3 overflow-x-auto">
						<table className="w-full text-left text-sm">
							<thead className="text-xs text-slate-500 dark:text-slate-400">
								<tr>
									<th scope="col" className="py-1 pr-3 font-medium">Student</th>
									<th scope="col" className="py-1 pr-3 font-medium">Interviews</th>
									<th scope="col" className="py-1 pr-3 font-medium">Completed</th>
									<th scope="col" className="py-1 pr-3 font-medium">AI calls (failed)</th>
									<th scope="col" className="py-1 font-medium">Tokens in / out</th>
								</tr>
							</thead>
							<tbody>
								{rows.data.map((r) => (
									<tr key={r.student_id} className="border-t border-slate-100 dark:border-white/5">
										<td className="py-1 pr-3">{r.full_name}</td>
										<td className="py-1 pr-3">{formatCount(r.sessions_created)}</td>
										<td className="py-1 pr-3">{formatCount(r.sessions_completed)}</td>
										<td className="py-1 pr-3">{formatCount(r.ai_calls)} ({formatCount(r.ai_failures)})</td>
										<td className="py-1">{formatCount(r.input_tokens)} / {formatCount(r.output_tokens)}</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
					<div className="mt-3 flex gap-2">
						<button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="rounded-md border border-slate-300 px-3 py-1 text-xs disabled:opacity-40 dark:border-white/20">Previous</button>
						<button type="button" disabled={rows.data.length < PAGE} onClick={() => setOffset(offset + PAGE)} className="rounded-md border border-slate-300 px-3 py-1 text-xs disabled:opacity-40 dark:border-white/20">Next</button>
					</div>
				</>
			)}
		</section>
	)
}
