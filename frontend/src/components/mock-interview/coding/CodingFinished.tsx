"use client"

/**
 * Phase 8.2: an ended coding round. Review-only: there is no score and no
 * report yet (the HR/technical report routes answer coding rounds with 409
 * until 8.3), so this shows what was saved and submitted, never a result.
 */

import { useEffect, useRef } from "react"
import type { InterviewSession } from "../mockInterviewApi"

export default function CodingFinished({
	session,
	onNew,
	newLabel = "Start another interview",
}: {
	session: InterviewSession
	onNew: () => void
	newLabel?: string
}) {
	const headingRef = useRef<HTMLHeadingElement>(null)
	useEffect(() => {
		headingRef.current?.focus()
	}, [])
	const summary = session.coding_summary
	const cancelled = session.status === "abandoned"
	return (
		<section
			aria-labelledby="coding-finished-title"
			className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6 dark:border-white/10 dark:bg-slate-900"
		>
			<h2 id="coding-finished-title" ref={headingRef} tabIndex={-1} className="text-lg font-semibold focus:outline-none">
				{cancelled ? "Coding round cancelled" : "Coding round complete"}
			</h2>
			{session.closing_message && !cancelled ? (
				<p className="text-sm text-slate-700 dark:text-slate-200">{session.closing_message}</p>
			) : null}
			{session.end_reason === "time_up" ? (
				<p className="text-sm text-slate-600 dark:text-slate-300">
					Time ran out. The code you had saved for each open problem was submitted for you.
				</p>
			) : null}
			{summary ? (
				<dl className="grid gap-2 text-sm sm:grid-cols-3">
					<div className="rounded-lg border border-slate-200 p-3 dark:border-white/10">
						<dt className="text-xs text-slate-500 dark:text-slate-400">Problems</dt>
						<dd className="text-lg font-semibold tabular-nums">{summary.problems}</dd>
					</div>
					<div className="rounded-lg border border-slate-200 p-3 dark:border-white/10">
						<dt className="text-xs text-slate-500 dark:text-slate-400">Problems closed</dt>
						<dd className="text-lg font-semibold tabular-nums">{summary.problems_closed}</dd>
					</div>
					<div className="rounded-lg border border-slate-200 p-3 dark:border-white/10">
						<dt className="text-xs text-slate-500 dark:text-slate-400">Graded submissions</dt>
						<dd className="text-lg font-semibold tabular-nums">{summary.graded_submissions}</dd>
					</div>
				</dl>
			) : null}
			<p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-white/10 dark:bg-slate-800/50 dark:text-slate-300">
				Your code was saved but not run, so there is no score. Feedback for coding rounds will be added in a
				later update.
			</p>
			<button
				type="button"
				onClick={onNew}
				className="min-h-[44px] rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-medium text-white"
			>
				{newLabel}
			</button>
		</section>
	)
}
