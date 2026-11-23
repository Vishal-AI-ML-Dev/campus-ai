"use client"

/** Phase 8.2: a created (not yet started) coding round: what to expect, then Start. */

import { useState } from "react"
import { Spinner } from "@/components/ui/states"
import { describeError, DIFFICULTY_LABELS, mockInterviewApi, type InterviewSession } from "../mockInterviewApi"
import ReviewOnlyNotice from "./ReviewOnlyNotice"

export default function CodingInstructions({
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
			onChange(kind === "start" ? await mockInterviewApi.start(session.id) : await mockInterviewApi.abandon(session.id))
		} catch (err) {
			setError(describeError(err, kind === "start" ? "Could not start the coding round." : "Could not cancel it."))
			try {
				const fresh = await mockInterviewApi.get(session.id)
				if (fresh.status !== session.status) onChange(fresh)
			} catch {
				// keep the error; the buttons stay available
			}
		} finally {
			setBusy(null)
		}
	}

	return (
		<section
			aria-labelledby="coding-instructions-title"
			className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6 dark:border-white/10 dark:bg-slate-900"
		>
			<h2 id="coding-instructions-title" className="text-lg font-semibold">
				Before you start your coding round
			</h2>
			<dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
				<div>
					<dt className="inline font-medium">Difficulty: </dt>
					<dd className="inline">{DIFFICULTY_LABELS[session.difficulty] ?? session.difficulty}</dd>
				</div>
				<div>
					<dt className="inline font-medium">Time: </dt>
					<dd className="inline">{session.duration_minutes} minutes</dd>
				</div>
			</dl>
			<ReviewOnlyNotice />
			<ul className="list-disc space-y-1 pl-5 text-sm text-slate-700 dark:text-slate-200">
				<li>Programs read from standard input and write to standard output, as each problem describes.</li>
				<li>Your code saves automatically. You can submit each problem for grading up to 3 times.</li>
				<li>Hints are optional and never lower your score.</li>
				<li>When time runs out, the code you saved for each open problem is submitted for you.</li>
				<li>Nothing watches your screen, tabs or clipboard during this practice round.</li>
			</ul>
			{error ? (
				<p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
					{error}
				</p>
			) : null}
			<div className="flex flex-wrap gap-2">
				<button
					type="button"
					onClick={() => void run("start")}
					disabled={busy !== null}
					className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
				>
					{busy === "start" ? <Spinner /> : null}
					Start coding round
				</button>
				<button
					type="button"
					onClick={() => void run("cancel")}
					disabled={busy !== null}
					className="min-h-[44px] rounded-lg border border-slate-300 px-4 py-2 text-sm dark:border-white/20"
				>
					Cancel
				</button>
			</div>
		</section>
	)
}
