"use client"

/** Shown for a created (not yet started) interview: summary, rules, start. */

import { useState } from "react"
import { Spinner } from "@/components/ui/states"
import {
	describeError,
	DIFFICULTY_LABELS,
	LANGUAGE_LABELS,
	mockInterviewApi,
	MODE_LABELS,
	TYPE_LABELS,
	type InterviewSession,
} from "./mockInterviewApi"

export default function InterviewInstructions({
	session,
	onChange,
}: {
	session: InterviewSession
	onChange: (s: InterviewSession) => void
}) {
	const [busy, setBusy] = useState<"start" | "cancel" | null>(null)
	const [error, setError] = useState<string | null>(null)

	async function run(kind: "start" | "cancel") {
		if (busy) return
		setBusy(kind)
		setError(null)
		try {
			const next =
				kind === "start"
					? await mockInterviewApi.start(session.id)
					: await mockInterviewApi.abandon(session.id)
			onChange(next)
		} catch (err) {
			setError(
				describeError(
					err,
					kind === "start" ? "Could not start the interview." : "Could not cancel the interview.",
				),
			)
			// It may have been started or cancelled elsewhere (another tab).
			try {
				const fresh = await mockInterviewApi.get(session.id)
				if (fresh.status !== session.status || fresh.awaiting !== session.awaiting) onChange(fresh)
			} catch {
				// keep showing the error; the buttons stay available for a retry
			}
		} finally {
			setBusy(null)
		}
	}

	const facts = [
		["Type", TYPE_LABELS[session.interview_type] ?? session.interview_type],
		["Mode", MODE_LABELS[session.mode] ?? session.mode],
		["Role", session.target_role ?? "-"],
		["Company", session.company_name ?? "Not specified"],
		["Difficulty", DIFFICULTY_LABELS[session.difficulty] ?? session.difficulty],
		["Duration", `${session.duration_minutes} minutes`],
		["Language", LANGUAGE_LABELS[session.language] ?? session.language],
		["Resume", session.resume_id ? "Selected" : "Not used"],
		["Job description", session.has_job_description ? "Provided" : "Not provided"],
	]

	return (
		<section
			aria-labelledby="mi-instructions-title"
			className="space-y-5 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6 dark:border-white/10 dark:bg-slate-900"
		>
			<h2 id="mi-instructions-title" className="text-lg font-semibold">
				Before you begin
			</h2>
			<dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
				{facts.map(([k, v]) => (
					<div key={k} className="flex justify-between gap-4 border-b border-slate-100 py-1 dark:border-white/5">
						<dt className="text-slate-500 dark:text-slate-400">{k}</dt>
						<dd className="text-right font-medium">{v}</dd>
					</div>
				))}
			</dl>
			<ul className="list-disc space-y-1 pl-5 text-sm text-slate-600 dark:text-slate-300">
				<li>The interviewer asks one question at a time and may ask follow-ups on your answer.</li>
				{session.mode === "voice" ? (
					<>
						<li>
							Voice mode: the interviewer reads each question aloud. Press <strong>Start speaking</strong> to
							answer; your browser asks for microphone access the first time. Check the text, fix any
							mistakes, then submit. You can always type instead.
						</li>
						<li>
							Speech is converted to text by your browser. Some browsers (e.g. Chrome) send audio to
							their speech service to do this. Campus AI stores only the text you submit, never audio;
							unsent text stays in this browser tab until you submit it.
						</li>
					</>
				) : null}
				<li>Answer in your own words, as you would in a real interview. Use examples (STAR) where it helps.</li>
				<li>It is fine to say &quot;I don&apos;t know&quot; - the interviewer will move on.</li>
				<li>The timer starts when you press Start. You can end the interview at any time.</li>
				<li>If you refresh or lose connection, come back to this page to continue where you left off.</li>
				<li>This is a practice interview. Your answers are saved to your account for your feedback report.</li>
			</ul>
			{error ? (
				<p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
					{error}
				</p>
			) : null}
			<div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
				<button
					type="button"
					onClick={() => run("cancel")}
					disabled={busy !== null}
					className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:border-red-300 hover:text-red-600 disabled:opacity-50 dark:border-white/10 dark:text-slate-300"
				>
					{busy === "cancel" ? "Cancelling…" : "Cancel interview"}
				</button>
				<button
					type="button"
					onClick={() => run("start")}
					disabled={busy !== null}
					className="inline-flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
				>
					{busy === "start" ? (
						<>
							<Spinner /> Preparing your interview…
						</>
					) : (
						"Start interview"
					)}
				</button>
			</div>
		</section>
	)
}
