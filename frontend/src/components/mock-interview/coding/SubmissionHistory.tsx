"use client"

/**
 * Phase 8.2: every submission for one problem, oldest first. Review-only:
 * no score or pass/fail is ever shown (code is not run); when execution exists
 * (8.6) the submission the final result uses is marked.
 */

import { LANGUAGE_LABELS, submissionStatusText } from "./codingFlow"
import type { CodingSubmission } from "./codingApi"

function time(iso: string): string {
	const d = new Date(iso)
	return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { timeStyle: "short" })
}

export default function SubmissionHistory({
	submissions,
	total,
}: {
	submissions: CodingSubmission[]
	total: number
}) {
	if (!submissions.length)
		return <p className="text-xs text-slate-500 dark:text-slate-400">No submissions for this problem yet.</p>
	return (
		<table className="w-full text-left text-xs">
			<caption className="sr-only">Your submissions for this problem</caption>
			<thead className="text-slate-500 dark:text-slate-400">
				<tr>
					<th scope="col" className="py-1 pr-2 font-medium">
						#
					</th>
					<th scope="col" className="py-1 pr-2 font-medium">
						Language
					</th>
					<th scope="col" className="py-1 pr-2 font-medium">
						Status
					</th>
					<th scope="col" className="py-1 font-medium">
						Time
					</th>
				</tr>
			</thead>
			<tbody>
				{submissions.map((s, i) => (
					<tr key={s.id} className="border-t border-slate-100 dark:border-white/5">
						<td className="py-1 pr-2 tabular-nums">
							{i + 1}/{total}
							{s.is_best ? <span className="ml-1 font-medium text-indigo-600">(used for your result)</span> : null}
						</td>
						<td className="py-1 pr-2">{LANGUAGE_LABELS[s.language] ?? s.language}</td>
						<td className="py-1 pr-2">{submissionStatusText(s)}</td>
						<td className="py-1 tabular-nums">{time(s.created_at)}</td>
					</tr>
				))}
			</tbody>
		</table>
	)
}
