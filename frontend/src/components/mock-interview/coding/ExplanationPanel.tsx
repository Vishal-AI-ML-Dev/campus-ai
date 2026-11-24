"use client"

/**
 * Phase 8.3: typed explanation questions after "Done with this problem".
 *
 * Text only (decision B1-a): no microphone, no dictation. The answer goes to
 * the existing POST /sessions/{id}/answer with the question's turn_seq; the
 * server then asks one more question or closes the problem. Nothing is
 * scored here; answers are used as evidence for the AI-estimate feedback.
 */

import { useEffect, useRef, useState } from "react"
import { Spinner } from "@/components/ui/states"
import type { CodingAttempt } from "./codingApi"
import { EXPLANATION_MAX_CHARS, pendingExplanation } from "./codingFlow"

export default function ExplanationPanel({
	attempt,
	busy,
	onAnswer,
}: {
	attempt: CodingAttempt
	busy: boolean
	onAnswer: (turnSeq: number, text: string) => Promise<boolean>
}) {
	const pending = pendingExplanation(attempt)
	const [text, setText] = useState("")
	const boxRef = useRef<HTMLTextAreaElement>(null)
	const pendingSeq = pending?.turn_seq ?? null
	useEffect(() => {
		// A new question: start with an empty box and move focus to it.
		setText("")
		if (pendingSeq !== null) boxRef.current?.focus()
	}, [pendingSeq])

	const turns = attempt.explanation ?? []
	if (!turns.length) return null
	const boxId = `coding-explain-${attempt.seq}`
	return (
		<section
			aria-labelledby={`${boxId}-title`}
			className="space-y-3 rounded-xl border border-indigo-200 bg-indigo-50/60 p-3 dark:border-indigo-500/30 dark:bg-indigo-500/10"
		>
			<h3 id={`${boxId}-title`} className="text-sm font-semibold">
				Explain your solution
			</h3>
			<p className="text-xs text-slate-600 dark:text-slate-300">
				Type your answer. Your explanation is used as evidence for the AI estimates in your feedback. Your
				code is not run.
			</p>
			<ol className="space-y-3">
				{turns.map((t, i) => (
					<li key={t.turn_seq} className="space-y-1 text-sm">
						<p className="font-medium">
							Question {i + 1}: <span className="font-normal">{t.question}</span>
						</p>
						{t.answer_text !== null ? (
							<p className="whitespace-pre-wrap rounded-md bg-white px-2 py-1 text-slate-700 dark:bg-slate-900 dark:text-slate-200">
								{t.answer_text}
							</p>
						) : null}
					</li>
				))}
			</ol>
			{pending ? (
				<form
					className="space-y-2"
					onSubmit={async (e) => {
						e.preventDefault()
						const answer = text.trim()
						if (!answer || busy) return
						if (await onAnswer(pending.turn_seq, answer)) setText("")
					}}
				>
					<label htmlFor={boxId} className="block text-sm font-medium">
						Your answer
					</label>
					<textarea
						id={boxId}
						ref={boxRef}
						value={text}
						maxLength={EXPLANATION_MAX_CHARS}
						onChange={(e) => setText(e.target.value)}
						rows={4}
						className="w-full rounded-lg border border-slate-300 bg-white p-2 text-sm dark:border-white/20 dark:bg-slate-900"
					/>
					<button
						type="submit"
						disabled={busy || !text.trim()}
						className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
					>
						{busy ? <Spinner /> : null}
						Send answer
					</button>
				</form>
			) : attempt.status === "explaining" ? (
				<p className="text-sm text-slate-600 dark:text-slate-300">Preparing the next question…</p>
			) : (
				<p className="text-sm text-slate-600 dark:text-slate-300">Thanks, this problem is closed.</p>
			)}
		</section>
	)
}
