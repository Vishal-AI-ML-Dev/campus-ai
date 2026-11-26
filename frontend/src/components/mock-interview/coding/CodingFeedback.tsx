"use client"

/**
 * Phase 8.3: review-only coding feedback (rubric "coding-v1-unverified").
 *
 * Decision P8-D7: the code was NOT run, so correctness, robustness and
 * efficiency are "Not verified", and there is NO overall, problem or session
 * score anywhere on this screen. The three AI categories are shown one by one
 * as "AI estimate" values with the evidence the server verified (code quotes
 * with line numbers, or quotes from the student's typed explanation), plus
 * the uncertainty note. Everything is plain text (no HTML rendering).
 */

import { useCallback, useEffect, useState } from "react"
import { ApiError } from "@/lib/api"
import { ErrorState, LoadingState, Spinner } from "@/components/ui/states"
import { describeError } from "../mockInterviewApi"
import { codingApi, type CodingFeedback as Feedback, type CodingProblemFeedback } from "./codingApi"
import { LANGUAGE_LABELS, estimateLabel, evidenceSource, objectiveLabel } from "./codingFlow"

type Load =
	| { kind: "loading" }
	| { kind: "none"; aiEnabled: boolean }
	| { kind: "ready"; data: Feedback }
	| { kind: "error"; message: string }

function ProblemFeedback({ p }: { p: CodingProblemFeedback }) {
	const headingId = `coding-feedback-problem-${p.attempt_seq}`
	return (
		<article aria-labelledby={headingId} className="space-y-3 rounded-xl border border-slate-200 p-3 dark:border-white/10">
			<h4 id={headingId} className="text-sm font-semibold">
				Problem {p.attempt_seq}: {p.problem_title}
				{p.language ? (
					<span className="font-normal text-slate-500 dark:text-slate-400"> · {LANGUAGE_LABELS[p.language] ?? p.language}</span>
				) : null}
			</h4>
			{p.status === "not_submitted" ? (
				<p className="text-sm text-slate-600 dark:text-slate-300">
					No code was submitted for this problem, so it was not reviewed.
				</p>
			) : (
				<>
					<div>
						<h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
							Checked by running tests
						</h5>
						<ul className="mt-1 grid gap-1 text-sm sm:grid-cols-3">
							{p.objective.map((o) => (
								<li key={o.key} className="rounded-lg border border-dashed border-slate-300 px-2 py-1 dark:border-white/20">
									<span className="font-medium">{o.label}</span>
									<span className="block text-xs text-slate-500 dark:text-slate-400">{objectiveLabel(o)}</span>
								</li>
							))}
						</ul>
					</div>
					<div>
						<h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
							AI estimates (from reading your code and explanation)
						</h5>
						<ul className="mt-1 space-y-2">
							{p.estimates.map((e) => (
								<li key={e.key} className="rounded-lg bg-slate-50 p-2 text-sm dark:bg-slate-800/50">
									<p>
										<span className="font-medium">{e.label}</span>{" "}
										<span className="text-slate-600 dark:text-slate-300">· {estimateLabel(e)}</span>
									</p>
									{e.rationale ? <p className="mt-1 text-slate-700 dark:text-slate-200">{e.rationale}</p> : null}
									<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{e.uncertainty}</p>
									{e.evidence.length ? (
										<ul className="mt-1 space-y-1" aria-label={`Evidence for ${e.label}`}>
											{e.evidence.map((ev, i) => (
												<li key={i} className="text-xs">
													<span className="text-slate-500 dark:text-slate-400">{evidenceSource(ev)}: </span>
													<code className="whitespace-pre-wrap break-words font-mono">{ev.quote}</code>
												</li>
											))}
										</ul>
									) : null}
								</li>
							))}
						</ul>
					</div>
					{p.complexity ? (
						<p className="text-sm">
							<span className="font-medium">Estimated complexity (AI estimate):</span> time{" "}
							{p.complexity.time || "not given"}, space {p.complexity.space || "not given"}.
							{p.complexity.justification ? ` ${p.complexity.justification}` : ""}
							{p.complexity.quote ? (
								<span className="block text-xs text-slate-500 dark:text-slate-400">
									{evidenceSource(p.complexity.quote)}:{" "}
									<code className="font-mono">{p.complexity.quote.quote}</code>
								</span>
							) : null}
						</p>
					) : null}
					{p.observations.length ? (
						<div>
							<h5 className="text-sm font-medium">Observations</h5>
							<ul className="list-disc space-y-1 pl-5 text-sm">
								{p.observations.map((o, i) => (
									<li key={i}>
										{o.line !== null ? <span className="text-slate-500 dark:text-slate-400">Line {o.line}: </span> : null}
										{o.text}
									</li>
								))}
							</ul>
						</div>
					) : null}
					{p.consider_cases.length ? (
						<div>
							<h5 className="text-sm font-medium">Cases worth considering</h5>
							<ul className="list-disc space-y-1 pl-5 text-sm">
								{p.consider_cases.map((c, i) => (
									<li key={i}>{c}</li>
								))}
							</ul>
						</div>
					) : null}
					{p.testing_notes.length ? (
						<div>
							<h5 className="text-sm font-medium">About your testing</h5>
							<ul className="list-disc space-y-1 pl-5 text-sm">
								{p.testing_notes.map((c, i) => (
									<li key={i}>{c}</li>
								))}
							</ul>
						</div>
					) : null}
					<p className="text-xs text-slate-500 dark:text-slate-400">
						Hints opened: {p.hints_used} (hints never lower anything). Explanation answers: {p.explanation_answers}.
					</p>
				</>
			)}
		</article>
	)
}

export default function CodingFeedback({ sessionId, completed }: { sessionId: number; completed: boolean }) {
	const [load, setLoad] = useState<Load>({ kind: "loading" })
	const [busy, setBusy] = useState(false)
	const [message, setMessage] = useState<string | null>(null)

	const fetchStored = useCallback(async () => {
		setLoad({ kind: "loading" })
		try {
			setLoad({ kind: "ready", data: await codingApi.feedback(sessionId) })
		} catch (err) {
			if (!(err instanceof ApiError && err.status === 404)) {
				setLoad({ kind: "error", message: describeError(err, "Could not load your coding feedback.") })
				return
			}
			// Not generated yet: offer it only when AI help is on for this round.
			let aiEnabled = false
			try {
				aiEnabled = (await codingApi.state(sessionId)).ai_enabled === true
			} catch {
				// unknown: keep the button hidden
			}
			setLoad({ kind: "none", aiEnabled })
		}
	}, [sessionId])

	useEffect(() => {
		void fetchStored()
	}, [fetchStored])

	async function generate() {
		if (busy) return
		setBusy(true)
		setMessage(null)
		try {
			setLoad({ kind: "ready", data: await codingApi.generateFeedback(sessionId) })
		} catch (err) {
			setMessage(describeError(err, "We couldn't prepare your coding feedback right now. Please try again."))
		} finally {
			setBusy(false)
		}
	}

	if (load.kind === "loading") return <LoadingState message="Loading your coding feedback…" />
	if (load.kind === "error") return <ErrorState message={load.message} onRetry={() => void fetchStored()} />
	if (load.kind === "none") {
		if (!completed) return null
		if (!load.aiEnabled)
			return (
				<p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-white/10 dark:bg-slate-800/50 dark:text-slate-300">
					Your code was saved but not run, so there is no score. AI feedback for coding rounds is not
					available right now.
				</p>
			)
		return (
			<div className="space-y-2 rounded-xl border border-slate-200 p-3 dark:border-white/10">
				<p className="text-sm text-slate-700 dark:text-slate-200">
					Get AI feedback on your code and explanation. It gives AI estimates only: your code is not run and
					there is no score.
				</p>
				<button
					type="button"
					onClick={() => void generate()}
					disabled={busy}
					className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
				>
					{busy ? <Spinner /> : null}
					{busy ? "Preparing feedback…" : "Get AI feedback"}
				</button>
				{message ? (
					<p role="alert" className="text-sm text-red-700 dark:text-red-300">
						{message}
					</p>
				) : null}
			</div>
		)
	}
	const data = load.data
	return (
		<section aria-labelledby="coding-feedback-title" className="space-y-3">
			<h3 id="coding-feedback-title" className="text-base font-semibold">
				AI feedback (review only)
			</h3>
			<p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
				{data.uncertainty_note}
			</p>
			{data.problems.map((p) => (
				<ProblemFeedback key={p.attempt_seq} p={p} />
			))}
			<p className="text-xs text-slate-500 dark:text-slate-400">{data.disclaimer}</p>
		</section>
	)
}
