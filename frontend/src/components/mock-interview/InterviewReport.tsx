"use client"

/**
 * Phase 4 feedback report: overall score, category scores, strengths,
 * weaknesses, recommendations and a per-question breakdown. Every score the
 * student sees is backed by a quote from their own answer (verified
 * server-side), and the disclaimer is always shown.
 */

import { useCallback, useEffect, useState } from "react"
import { ApiError } from "@/lib/api"
import { ErrorState, Spinner } from "@/components/ui/states"
import {
	describeError,
	DIFFICULTY_LABELS,
	loadOrGenerateReport,
	TYPE_LABELS,
	type InterviewReport,
	type ReportItem,
	type ReportQuestion,
} from "./mockInterviewApi"

const STATUS_LABELS: Record<ReportQuestion["status"], string> = {
	scored: "Scored",
	no_answer: "Not answered",
	dont_know: "Didn't know",
	not_assessed: "Not assessed",
}

function band(score: number): string {
	if (score >= 75) return "Strong"
	if (score >= 50) return "Developing"
	return "Needs practice"
}

function formatDate(iso: string | null): string {
	if (!iso) return ""
	const d = new Date(iso)
	return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { dateStyle: "medium" })
}

function Quote({ text, seq }: { text: string; seq: number | null }) {
	if (!text) return null
	return (
		<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
			<span className="italic">&ldquo;{text}&rdquo;</span>
			{seq !== null ? <span> (Q{seq})</span> : null}
		</p>
	)
}

function ItemList({ title, items, empty }: { title: string; items: ReportItem[]; empty: string }) {
	return (
		<div className="rounded-xl border border-slate-200 p-4 dark:border-white/10">
			<h3 className="text-sm font-semibold">{title}</h3>
			{items.length ? (
				<ul className="mt-2 space-y-2 text-sm">
					{items.map((item, i) => (
						<li key={i}>
							<p className="text-slate-700 dark:text-slate-200">{item.text}</p>
							<Quote text={item.evidence} seq={item.seq} />
						</li>
					))}
				</ul>
			) : (
				<p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{empty}</p>
			)}
		</div>
	)
}

function QuestionDetail({ q }: { q: ReportQuestion }) {
	const scored = q.scores.length > 0 && q.status !== "not_assessed"
	const avg = scored ? q.scores.reduce((sum, s) => sum + s.score, 0) / q.scores.length : null
	return (
		<details className="rounded-lg border border-slate-200 p-3 text-sm dark:border-white/10">
			<summary className="cursor-pointer">
				{/* Flex on an inner span keeps the native disclosure marker. */}
				<span className="inline-flex w-[calc(100%-1.25rem)] flex-wrap items-center gap-x-2 gap-y-1 align-middle">
					<span className="font-medium">Q{q.seq}</span>
					{q.topic ? <span className="text-slate-500 dark:text-slate-400">{q.topic}</span> : null}
					{q.is_follow_up ? (
						<span className="rounded bg-slate-100 px-1.5 text-xs dark:bg-white/10">follow-up</span>
					) : null}
					<span className="ml-auto text-xs text-slate-600 dark:text-slate-300">
						{avg !== null && q.status === "scored"
							? `${avg.toFixed(1)} / 5`
							: STATUS_LABELS[q.status]}
					</span>
				</span>
			</summary>
			<div className="mt-3 space-y-3">
				<p className="whitespace-pre-wrap font-medium text-slate-800 dark:text-slate-100">
					{q.question}
				</p>
				<div>
					<p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Your answer</p>
					<p className="mt-1 whitespace-pre-wrap break-words text-slate-700 dark:text-slate-200">
						{q.answer_text ?? <em className="text-slate-400">Not answered</em>}
					</p>
				</div>
				{q.feedback ? (
					<div>
						<p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Feedback</p>
						<p className="mt-1 text-slate-700 dark:text-slate-200">{q.feedback}</p>
					</div>
				) : null}
				{q.status === "not_assessed" ? (
					<p className="text-xs text-slate-500 dark:text-slate-400">
						This answer could not be assessed reliably, so it is not counted in your scores.
					</p>
				) : null}
				{scored ? (
					<table className="w-full text-left text-xs">
						<caption className="sr-only">Scores for question {q.seq}</caption>
						<thead>
							<tr className="text-slate-500">
								<th scope="col" className="py-1 pr-2 font-medium">Category</th>
								<th scope="col" className="py-1 pr-2 font-medium">Score</th>
								<th scope="col" className="py-1 font-medium">Evidence from your answer</th>
							</tr>
						</thead>
						<tbody>
							{q.scores.map((s) => (
								<tr key={s.key} className="border-t border-slate-100 align-top dark:border-white/5">
									<td className="py-1 pr-2">{s.label}</td>
									<td className="py-1 pr-2 tabular-nums">{s.score}/5</td>
									<td className="break-words py-1 italic text-slate-600 dark:text-slate-300">
										{s.evidence ? `“${s.evidence}”` : "-"}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				) : null}
			</div>
		</details>
	)
}

export function InterviewReportView({ report }: { report: InterviewReport }) {
	const score = Math.round(report.overall_score)
	return (
		<div className="space-y-4">
			<div className="flex flex-col gap-4 rounded-xl border border-slate-200 p-4 sm:flex-row sm:items-center dark:border-white/10">
				<div className="text-center sm:w-36">
					<p className="text-4xl font-bold tabular-nums text-indigo-600 dark:text-indigo-400">
						{score}
						<span className="text-base font-medium text-slate-500">/100</span>
					</p>
					<p className="text-sm font-medium">{band(report.overall_score)}</p>
				</div>
				<div className="min-w-0 flex-1 text-sm">
					<p className="font-medium">
						{TYPE_LABELS[report.interview_type] ?? report.interview_type} interview
						{report.target_role ? ` · ${report.target_role}` : ""}
						{report.company_name ? ` · ${report.company_name}` : ""}
					</p>
					<p className="text-slate-500 dark:text-slate-400">
						{DIFFICULTY_LABELS[report.difficulty] ?? report.difficulty} ·{" "}
						{report.duration_minutes} min · {formatDate(report.completed_at ?? report.created_at)}
					</p>
					<p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{report.disclaimer}</p>
				</div>
			</div>

			<div className="rounded-xl border border-slate-200 p-4 dark:border-white/10">
				<h3 className="text-sm font-semibold">Scores by category</h3>
				<ul className="mt-3 space-y-3">
					{report.categories.map((c) => (
						<li key={c.key}>
							<div className="flex items-baseline justify-between gap-2 text-sm">
								<span className="min-w-0">
									{c.label}
									<span className="block text-xs text-slate-500 dark:text-slate-400">
										{c.description}
									</span>
								</span>
								<span className="shrink-0 tabular-nums">
									{c.score === null ? "Not assessed" : `${Math.round(c.score)}/100`}
								</span>
							</div>
							<div className="mt-1 h-2 rounded-full bg-slate-100 dark:bg-white/10" aria-hidden="true">
								<div
									className="h-2 rounded-full bg-indigo-500"
									style={{ width: `${Math.max(0, Math.min(100, c.score ?? 0))}%` }}
								/>
							</div>
						</li>
					))}
				</ul>
			</div>

			<div className="grid gap-4 md:grid-cols-2">
				<ItemList title="Strengths" items={report.strengths} empty="No clear strengths were found in this attempt yet." />
				<ItemList title="Areas to improve" items={report.weaknesses} empty="No major weaknesses were found." />
			</div>

			{report.recommendations.length ? (
				<div className="rounded-xl border border-slate-200 p-4 dark:border-white/10">
					<h3 className="text-sm font-semibold">What to practise next</h3>
					<ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-200">
						{report.recommendations.map((r, i) => (
							<li key={i}>{r}</li>
						))}
					</ul>
				</div>
			) : null}

			<div className="space-y-2">
				<h3 className="text-sm font-semibold">Question by question</h3>
				{report.questions.map((q) => (
					<QuestionDetail key={q.seq} q={q} />
				))}
			</div>
		</div>
	)
}

/** Loads the report for a finished session, generating it on first view. */
export default function InterviewReportPanel({
	sessionId,
	exists = false,
}: {
	sessionId: number
	/** The report is known to exist (opened from history): read it with GET. */
	exists?: boolean
}) {
	const [report, setReport] = useState<InterviewReport | null>(null)
	const [error, setError] = useState<{ message: string; requestId?: string; final: boolean } | null>(
		null,
	)
	const [loading, setLoading] = useState(true)

	const load = useCallback(() => {
		setLoading(true)
		setError(null)
		loadOrGenerateReport(sessionId, { exists })
			.then(setReport)
			.catch((err) =>
				setError({
					message: describeError(err, "Could not load your feedback report."),
					requestId: err instanceof ApiError ? err.requestId : undefined,
					// 409: no report can exist for this interview; retrying won't help.
					final: err instanceof ApiError && err.status === 409,
				}),
			)
			.finally(() => setLoading(false))
	}, [sessionId, exists])

	useEffect(() => {
		load()
	}, [load])

	if (loading)
		return (
			<div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400" role="status">
				<Spinner />
				<span>Preparing your feedback report. This can take up to a minute…</span>
			</div>
		)
	if (error || !report)
		return (
			<ErrorState
				message={error?.message ?? "Could not load your feedback report."}
				onRetry={error?.final ? undefined : load}
				requestId={error?.requestId}
			/>
		)
	return <InterviewReportView report={report} />
}
