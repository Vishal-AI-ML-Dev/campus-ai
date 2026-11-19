"use client"

/**
 * Ended interview: closing message, the feedback report (completed
 * interviews; generated on first view) and the transcript.
 */

import InterviewReportPanel from "./InterviewReport"
import type { InterviewSession } from "./mockInterviewApi"

export default function InterviewFinished({
	session,
	onNew,
	newLabel = "Start another interview",
	reportExists = false,
}: {
	session: InterviewSession
	onNew: () => void
	newLabel?: string
	reportExists?: boolean
}) {
	const abandoned = session.status === "abandoned"
	const answered = session.turns.filter((t) => t.answer_text !== null).length
	const hasReport = session.status === "completed" && answered > 0
	return (
		<section
			aria-labelledby="mi-finished-title"
			className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6 dark:border-white/10 dark:bg-slate-900"
		>
			<div>
				<h2 id="mi-finished-title" className="text-lg font-semibold">
					{abandoned ? "Interview cancelled" : "Interview complete"}
				</h2>
				{session.closing_message ? (
					<p className="mt-2 whitespace-pre-wrap text-sm text-slate-700 dark:text-slate-200">
						{session.closing_message}
					</p>
				) : null}
				<p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
					{answered} question{answered === 1 ? "" : "s"} answered.
					{abandoned ? " Cancelled interviews don't get a feedback report." : ""}
				</p>
			</div>

			{session.mode === "video" ? (
				<p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-white/10 dark:bg-slate-800/50 dark:text-slate-300">
					Video interview: your camera was only a preview on your screen. It was not recorded, uploaded or
					analysed, and it played no part in this feedback. The feedback is based only on the answer text you
					submitted.
				</p>
			) : null}

			{hasReport ? <InterviewReportPanel sessionId={session.id} exists={reportExists} /> : null}

			{session.turns.length > 0 ? (
				<details className="rounded-lg border border-slate-200 p-3 text-sm dark:border-white/10">
					<summary className="cursor-pointer font-medium">View transcript</summary>
					<ol className="mt-3 space-y-3">
						{session.turns.map((t) => (
							<li key={t.seq}>
								<p className="font-medium text-slate-800 dark:text-slate-100">
									Q{t.seq}. <span className="whitespace-pre-wrap">{t.question}</span>
								</p>
								<p className="mt-1 whitespace-pre-wrap text-slate-600 dark:text-slate-300">
									{t.answer_text ?? <em className="text-slate-400">Not answered</em>}
									{t.answer_source === "stt" ? (
										<span className="ml-2 text-[11px] uppercase tracking-wide text-slate-400">· spoken</span>
									) : null}
								</p>
							</li>
						))}
					</ol>
				</details>
			) : null}

			<button
				type="button"
				onClick={onNew}
				className="rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-medium text-white"
			>
				{newLabel}
			</button>
		</section>
	)
}
