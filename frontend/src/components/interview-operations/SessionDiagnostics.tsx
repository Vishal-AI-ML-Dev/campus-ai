"use client"

/**
 * Content-free diagnostics for one interview (Phase 7.8): the step timeline with
 * outcome, latency, error class and request id for support. No questions,
 * answers, resumes or job descriptions - the API never returns them.
 */

import { useState } from "react"
import { ApiError } from "@/lib/api"
import { ErrorState, Spinner } from "@/components/ui/states"
import { opsApi, type SessionDiagnostics as Diagnostics } from "./opsApi"
import { formatCount, formatLatency, outcomeLabel, STEP_LABELS } from "./opsLogic"

export default function SessionDiagnostics() {
	const [input, setInput] = useState("")
	const [busy, setBusy] = useState(false)
	const [error, setError] = useState<{ text: string; requestId?: string } | null>(null)
	const [result, setResult] = useState<Diagnostics | null>(null)

	async function lookUp(e: React.FormEvent) {
		e.preventDefault()
		const id = Number(input.trim())
		if (!Number.isSafeInteger(id) || id < 1) {
			setError({ text: "Enter an interview number." })
			return
		}
		setBusy(true)
		setError(null)
		setResult(null)
		try {
			setResult(await opsApi.diagnostics(id))
		} catch (err) {
			const e2 = err instanceof ApiError ? err : null
			setError({
				text: e2?.status === 404 ? "No interview with that number in your institute." : e2?.message ?? "Lookup failed.",
				requestId: e2?.requestId,
			})
		} finally {
			setBusy(false)
		}
	}

	return (
		<section aria-labelledby="ops-diagnostics" className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-slate-900">
			<h2 id="ops-diagnostics" className="text-lg font-semibold">Interview diagnostics</h2>
			<form onSubmit={lookUp} className="mt-3 flex flex-wrap items-end gap-2">
				<div>
					<label htmlFor="diag-id" className="block text-xs font-medium">Interview number</label>
					<input id="diag-id" inputMode="numeric" value={input} onChange={(e) => setInput(e.target.value)} className="mt-1 w-40 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-slate-900" />
				</div>
				<button type="submit" disabled={busy} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium disabled:opacity-50 dark:border-white/20">
					{busy ? <Spinner /> : null} Look up
				</button>
			</form>
			{error ? <div className="mt-3"><ErrorState message={error.text} requestId={error.requestId} /></div> : null}
			{result ? (
				<div className="mt-4 space-y-3 text-sm">
					<p>
						Interview #{result.session_id} · {result.interview_type} · {result.mode} · <span className="font-medium">{result.status}</span>
						{" · "}{formatCount(result.answered)}/{formatCount(result.turns)} questions answered
						{result.report_generated ? " · report generated" : ""}
					</p>
					{result.events.length === 0 ? (
						<p className="text-slate-500 dark:text-slate-400">No AI calls recorded for this interview.</p>
					) : (
						<div className="overflow-x-auto">
							<table className="w-full text-left text-xs">
								<thead className="text-slate-500 dark:text-slate-400">
									<tr>
										<th scope="col" className="py-1 pr-3 font-medium">Time</th>
										<th scope="col" className="py-1 pr-3 font-medium">Step</th>
										<th scope="col" className="py-1 pr-3 font-medium">Outcome</th>
										<th scope="col" className="py-1 pr-3 font-medium">Latency</th>
										<th scope="col" className="py-1 pr-3 font-medium">Tokens in / out</th>
										<th scope="col" className="py-1 font-medium">Request id</th>
									</tr>
								</thead>
								<tbody>
									{result.events.map((ev) => (
										<tr key={ev.event_id} className="border-t border-slate-100 dark:border-white/5">
											<td className="py-1 pr-3">{new Date(ev.created_at).toLocaleTimeString()}</td>
											<td className="py-1 pr-3">{STEP_LABELS[ev.step] ?? ev.step}</td>
											<td className="py-1 pr-3">
												{outcomeLabel(ev.outcome)}
												{ev.error_class ? ` (${ev.error_class}${ev.http_status ? `, ${ev.http_status}` : ""})` : ""}
											</td>
											<td className="py-1 pr-3">{formatLatency(ev.latency_ms)}</td>
											<td className="py-1 pr-3">{formatCount(ev.input_tokens)} / {formatCount(ev.output_tokens)}</td>
											<td className="py-1"><code>{ev.request_id ?? "–"}</code></td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</div>
			) : null}
		</section>
	)
}
