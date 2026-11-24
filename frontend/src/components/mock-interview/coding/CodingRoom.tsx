"use client"

/**
 * Phase 8.2: the coding round room (review-only, decision P8-D8).
 *
 * Problem statement | editor | submissions. Code autosaves to the SERVER
 * (never to browser storage) after 10 s idle and before every action; a
 * refresh resumes exactly. Submit stores code for a later review; nothing is
 * run and no result is simulated. The server enforces every limit; the
 * buttons only mirror the counts it returns.
 *
 * Layout: >= 1024 px side by side; below that, tabs (Problem / Code /
 * Submissions). No proctoring: nothing watches focus, tabs or paste (P8-D15).
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { ApiError } from "@/lib/api"
import { ErrorState, LoadingState, Spinner } from "@/components/ui/states"
import EndInterviewControl from "../EndInterviewControl"
import { Countdown } from "../InterviewRoom"
import { describeError, mockInterviewApi, type InterviewSession } from "../mockInterviewApi"
import CodeEditor from "./CodeEditor"
import ExplanationPanel from "./ExplanationPanel"
import ProblemPanel from "./ProblemPanel"
import ReviewOnlyNotice from "./ReviewOnlyNotice"
import SubmissionHistory from "./SubmissionHistory"
import { codingApi, type CodingState, type CodingSubmission } from "./codingApi"
import {
	AUTOSAVE_IDLE_MS,
	LANGUAGE_LABELS,
	attemptOpen,
	codeAfterLanguageSwitch,
	hiddenAiHints,
	newIdempotencyKey,
	pendingExplanation,
	sizeLabel,
	submitBlockedReason,
	submittedAnnouncement,
	utf8Bytes,
} from "./codingFlow"

const MAX_BYTES = 64 * 1024
type Draft = { code: string; language: string; rev: number; dirty: boolean }
type Busy = null | "submit" | "hint" | "done" | "finish" | "explain"
type SaveState = "saved" | "saving" | "unsaved" | "stale" | "error"
type Tab = "problem" | "code" | "submissions"
const TABS: { id: Tab; label: string }[] = [
	{ id: "problem", label: "Problem" },
	{ id: "code", label: "Code" },
	{ id: "submissions", label: "Submissions" },
]

const btn =
	"inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 min-h-[44px]"

export default function CodingRoom({
	session,
	onChange,
}: {
	session: InterviewSession
	onChange: (s: InterviewSession) => void
}) {
	const [state, setState] = useState<CodingState | null>(null)
	const [loadError, setLoadError] = useState<string | null>(null)
	const [active, setActive] = useState(1)
	const [tab, setTab] = useState<Tab>("problem")
	const [simple, setSimple] = useState(false)
	const [fontSize, setFontSize] = useState(14)
	const [editorKey, setEditorKey] = useState(0)
	const [busy, setBusy] = useState<Busy>(null)
	const [saveState, setSaveState] = useState<SaveState>("saved")
	const [announce, setAnnounce] = useState("")
	const [error, setError] = useState<string | null>(null)
	const [subs, setSubs] = useState<Record<number, CodingSubmission[]>>({})
	// Phase 8.3: personalised AI hints are shown once (the server does not store
	// their text), so they live only in this view.
	const [aiHints, setAiHints] = useState<Record<number, string[]>>({})
	// Drafts: the ref is the source of truth for async saving; `draftView` is
	// the copy React renders, published after every change.
	const drafts = useRef<Record<number, Draft>>({})
	const [draftView, setDraftView] = useState<Record<number, Draft>>({})
	const publish = useCallback(() => {
		setDraftView(Object.fromEntries(Object.entries(drafts.current).map(([k, v]) => [k, { ...v }])))
	}, [])
	const keys = useRef<Record<number, string>>({})
	const timer = useRef<number | null>(null)
	const saving = useRef<Promise<void> | null>(null)
	const sessionId = session.id

	const applyState = useCallback((next: CodingState) => {
		for (const a of next.attempts) {
			const current = drafts.current[a.seq]
			if (current?.dirty) {
				current.rev = Math.max(current.rev, a.draft_rev)
				continue
			}
			const language = a.language ?? next.languages[0]
			drafts.current[a.seq] = {
				code: a.draft_source ?? a.problem.starter[language] ?? "",
				language,
				rev: a.draft_rev,
				dirty: false,
			}
		}
		publish()
		setState(next)
	}, [publish])

	const loadSubs = useCallback(
		async (seq: number) => {
			try {
				const list = await codingApi.submissions(sessionId, seq)
				setSubs((m) => ({ ...m, [seq]: list }))
			} catch {
				// history is informational; the room keeps working without it
			}
		},
		[sessionId],
	)

	const reload = useCallback(async () => {
		setLoadError(null)
		try {
			applyState(await codingApi.state(sessionId))
			setEditorKey((k) => k + 1)
		} catch (err) {
			setLoadError(describeError(err, "Could not load your coding round."))
		}
	}, [applyState, sessionId])

	useEffect(() => {
		void reload()
	}, [reload])

	useEffect(() => {
		if (state) void loadSubs(active)
	}, [active, state, loadSubs])

	// --- Saving -------------------------------------------------------------
	const save = useCallback(
		async (seq: number): Promise<boolean> => {
			if (saving.current) await saving.current
			const d = drafts.current[seq]
			if (!d || !d.dirty) return true
			if (utf8Bytes(d.code) > MAX_BYTES) {
				setSaveState("error")
				return false
			}
			setSaveState("saving")
			const code = d.code
			let ok = true
			const run = (async () => {
				try {
					const res = await codingApi.saveDraft(sessionId, seq, d.language, code, d.rev)
					d.rev = res.draft_rev
					if (d.code === code) d.dirty = false
					publish()
					setSaveState(d.dirty ? "unsaved" : "saved")
				} catch (err) {
					ok = false
					if (err instanceof ApiError && err.status === 409) {
						setSaveState("stale")
						setError(err.message)
					} else {
						setSaveState("error")
					}
				}
			})()
			saving.current = run
			await run
			saving.current = null
			return ok
		},
		[sessionId, publish],
	)

	const saveAll = useCallback(async () => {
		for (const seq of Object.keys(drafts.current).map(Number)) await save(seq)
	}, [save])

	function edit(code: string) {
		const d = drafts.current[active]
		if (!d) return
		d.code = code
		d.dirty = true
		setSaveState("unsaved")
		publish()
		if (timer.current) window.clearTimeout(timer.current)
		const seq = active
		timer.current = window.setTimeout(() => void save(seq), AUTOSAVE_IDLE_MS)
	}

	useEffect(() => () => {
		if (timer.current) window.clearTimeout(timer.current)
	}, [])

	// --- Time: save shortly before the end, then let the server close the round.
	const remaining = state?.remaining_seconds ?? null
	useEffect(() => {
		if (remaining === null) return
		const flush = window.setTimeout(() => void saveAll(), Math.max(0, remaining - 5) * 1000)
		const end = window.setTimeout(async () => {
			await saveAll()
			try {
				onChange(await mockInterviewApi.get(sessionId))
			} catch {
				// the next action will show the ended round
			}
		}, (remaining + 1) * 1000)
		return () => {
			window.clearTimeout(flush)
			window.clearTimeout(end)
		}
	}, [remaining, saveAll, onChange, sessionId])

	// --- Actions -------------------------------------------------------------
	async function refreshAfter(err: unknown, fallback: string) {
		setError(describeError(err, fallback))
		try {
			const fresh = await mockInterviewApi.get(sessionId)
			if (fresh.status !== "in_progress") return onChange(fresh)
			applyState(await codingApi.state(sessionId))
		} catch {
			// keep the message; the student can retry
		}
	}

	async function submit() {
		const attempt = state?.attempts.find((a) => a.seq === active)
		const d = drafts.current[active]
		if (!attempt || !d || busy || submitBlockedReason(attempt, d.code, MAX_BYTES, false)) return
		setBusy("submit")
		setError(null)
		try {
			await save(active)
			keys.current[active] ??= newIdempotencyKey()
			await codingApi.submit(sessionId, active, d.language, d.code, keys.current[active])
			delete keys.current[active]
			const next = await codingApi.state(sessionId)
			applyState(next)
			await loadSubs(active)
			const left = next.attempts.find((a) => a.seq === active)?.submissions_remaining ?? 0
			setAnnounce(submittedAnnouncement(left))
		} catch (err) {
			if (err instanceof ApiError) delete keys.current[active] // a definite answer: next try is new
			await refreshAfter(err, "Could not submit your code. It is saved; please try again.")
		} finally {
			setBusy(null)
		}
	}

	async function hint() {
		if (busy) return
		setBusy("hint")
		setError(null)
		try {
			await save(active) // an AI hint reads the saved code: never a stale draft
			const h = await codingApi.hint(sessionId, active)
			if (h.source === "ai") {
				const seq = active
				setAiHints((m) => ({ ...m, [seq]: [...(m[seq] ?? []), h.hint] }))
			}
			applyState(await codingApi.state(sessionId))
			setAnnounce(
				`${h.source === "ai" ? "Personalised AI hint" : "Hint"} ${h.hint_number} of ${h.hints_available} opened. It is shown under the problem.`,
			)
		} catch (err) {
			await refreshAfter(err, "Could not open a hint.")
		} finally {
			setBusy(null)
		}
	}

	async function done() {
		if (busy) return
		setBusy("done")
		setError(null)
		try {
			await save(active)
			const next = await codingApi.done(sessionId, active)
			applyState(next)
			const after = next.attempts.find((a) => a.seq === active)
			setAnnounce(
				after && pendingExplanation(after)
					? "Please explain your solution: a question is shown below the editor."
					: "Problem closed. Your saved code is kept.",
			)
		} catch (err) {
			await refreshAfter(err, "Could not close this problem.")
		} finally {
			setBusy(null)
		}
	}

	// Phase 8.3: a typed explanation answer (text only, B1-a) via the existing /answer route.
	async function explain(turnSeq: number, text: string): Promise<boolean> {
		if (busy) return false
		setBusy("explain")
		setError(null)
		try {
			await mockInterviewApi.answer(sessionId, turnSeq, text)
			const next = await codingApi.state(sessionId)
			applyState(next)
			const after = next.attempts.find((a) => a.seq === active)
			setAnnounce(
				after && pendingExplanation(after)
					? "Answer saved. Another question is shown."
					: "Answer saved. This problem is closed.",
			)
			return true
		} catch (err) {
			await refreshAfter(err, "Could not save your answer. Please try again.")
			return false
		} finally {
			setBusy(null)
		}
	}

	async function finish() {
		if (busy) return
		setBusy("finish")
		setError(null)
		try {
			await saveAll()
			onChange(await mockInterviewApi.finish(sessionId))
		} catch (err) {
			await refreshAfter(err, "Could not end the coding round.")
		} finally {
			setBusy(null)
		}
	}

	function switchLanguage(language: string) {
		const attempt = state?.attempts.find((a) => a.seq === active)
		const d = drafts.current[active]
		if (!attempt || !d || language === d.language) return
		const { code, replaced } = codeAfterLanguageSwitch(
			d.code,
			attempt.problem.starter[d.language],
			attempt.problem.starter[language],
		)
		d.language = language // in place: a save in flight keeps updating this draft's revision
		setEditorKey((k) => k + 1)
		setAnnounce(replaced ? `Switched to ${LANGUAGE_LABELS[language]} starter code.` : `Language set to ${LANGUAGE_LABELS[language]}. Your code was kept.`)
		edit(code)
	}

	function onTabKey(e: React.KeyboardEvent, index: number) {
		if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return
		e.preventDefault()
		const next = TABS[(index + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length]
		setTab(next.id)
		document.getElementById(`coding-tab-${next.id}`)?.focus()
	}

	// --- Render ----------------------------------------------------------------
	if (loadError)
		return <ErrorState message={loadError} onRetry={() => void reload()} />
	if (!state) return <LoadingState message="Loading your coding round…" />

	const attempt = state.attempts.find((a) => a.seq === active) ?? state.attempts[0]
	if (!attempt) return <ErrorState message="This coding round has no problems." onRetry={() => void reload()} />
	const d = draftView[attempt.seq]
	if (!d) return <LoadingState message="Loading your coding round…" />
	const blocked = submitBlockedReason(attempt, d.code, MAX_BYTES, busy !== null)
	const open = attemptOpen(attempt)
	const hintsLeft = attempt.hints_available - attempt.hints_used
	const panelClass = (t: Tab) => `${tab === t ? "block" : "hidden"} lg:block`

	return (
		<section
			aria-labelledby="coding-room-title"
			className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6 dark:border-white/10 dark:bg-slate-900"
		>
			<div className="flex flex-wrap items-center gap-3">
				<h2 id="coding-room-title" className="text-lg font-semibold">
					Coding round
				</h2>
				{state.remaining_seconds !== null ? (
					<Countdown key={state.remaining_seconds} seconds={state.remaining_seconds} />
				) : null}
				<div className="ml-auto">
					<EndInterviewControl busy={busy !== null} ending={busy === "finish"} onConfirm={finish} />
				</div>
			</div>
			<ReviewOnlyNotice compact />

			{state.attempts.length > 1 ? (
				<div className="flex flex-wrap gap-2" role="group" aria-label="Problems in this round">
					{state.attempts.map((a) => (
						<button
							key={a.seq}
							type="button"
							aria-pressed={a.seq === attempt.seq}
							onClick={() => {
								void save(active)
								setActive(a.seq)
								setEditorKey((k) => k + 1)
							}}
							className={`min-h-[44px] rounded-lg border px-3 py-1.5 text-left text-sm ${
								a.seq === attempt.seq
									? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
									: "border-slate-200 dark:border-white/10"
							}`}
						>
							Problem {a.seq}: {a.problem.title}
							{attemptOpen(a) ? "" : a.status === "explaining" ? " (explaining)" : " (closed)"}
						</button>
					))}
				</div>
			) : null}

			<div role="tablist" aria-label="Coding round view" className="flex gap-1 border-b border-slate-200 lg:hidden dark:border-white/10">
				{TABS.map((t, i) => (
					<button
						key={t.id}
						id={`coding-tab-${t.id}`}
						role="tab"
						type="button"
						aria-selected={tab === t.id}
						aria-controls={`coding-panel-${t.id}`}
						tabIndex={tab === t.id ? 0 : -1}
						onClick={() => setTab(t.id)}
						onKeyDown={(e) => onTabKey(e, i)}
						className={`min-h-[44px] px-3 text-sm ${tab === t.id ? "border-b-2 border-indigo-500 font-medium" : "text-slate-500"}`}
					>
						{t.label}
					</button>
				))}
			</div>

			<div className="grid gap-6 lg:grid-cols-2">
				<div id="coding-panel-problem" role="tabpanel" aria-labelledby="coding-tab-problem" className={`${panelClass("problem")} min-w-0`}>
					<ProblemPanel
						problem={attempt.problem}
						headingId={`coding-problem-${attempt.seq}`}
						aiHints={aiHints[attempt.seq] ?? []}
						hiddenAiHints={hiddenAiHints(attempt, (aiHints[attempt.seq] ?? []).length)}
					/>
				</div>

				<div className="min-w-0 space-y-4">
					<div id="coding-panel-code" role="tabpanel" aria-labelledby="coding-tab-code" className={`${panelClass("code")} space-y-3`}>
						<div className="flex flex-wrap items-center gap-3 text-sm">
							<label className="flex items-center gap-2">
								<span>Language</span>
								<select
									value={d.language}
									disabled={!open || busy !== null}
									onChange={(e) => switchLanguage(e.target.value)}
									className="min-h-[44px] rounded-lg border border-slate-200 bg-white px-2 dark:border-white/10 dark:bg-slate-900"
								>
									{state.languages.map((l) => (
										<option key={l} value={l}>
											{LANGUAGE_LABELS[l] ?? l}
										</option>
									))}
								</select>
							</label>
							<label className="flex items-center gap-2">
								<input type="checkbox" checked={simple} onChange={(e) => {
									setSimple(e.target.checked)
									setEditorKey((k) => k + 1)
								}} />
								Simple editor
							</label>
							<span className="flex items-center gap-1" role="group" aria-label="Code font size">
								<button type="button" className="min-h-[44px] min-w-[44px] rounded border border-slate-200 dark:border-white/10" onClick={() => setFontSize((f) => Math.max(12, f - 2))} aria-label="Smaller code text">
									A−
								</button>
								<button type="button" className="min-h-[44px] min-w-[44px] rounded border border-slate-200 dark:border-white/10" onClick={() => setFontSize((f) => Math.min(24, f + 2))} aria-label="Larger code text">
									A+
								</button>
							</span>
						</div>
						<CodeEditor
							key={`${attempt.seq}:${d.language}:${simple}:${editorKey}`}
							id={`coding-editor-${attempt.seq}`}
							label={`Code for problem ${attempt.seq}, ${LANGUAGE_LABELS[d.language] ?? d.language}`}
							describedBy="coding-editor-help"
							language={d.language}
							initialValue={d.code}
							onChange={edit}
							onSubmitShortcut={() => void submit()}
							simple={simple}
							fontSize={fontSize}
							readOnly={!open}
						/>
						<p id="coding-editor-help" className="text-xs text-slate-500 dark:text-slate-400">
							Tab indents. To leave the editor with the keyboard, press Esc, then Tab. Ctrl+Shift+Enter
							(Cmd+Shift+Enter on Mac) submits. {sizeLabel(utf8Bytes(d.code), MAX_BYTES)} ·{" "}
							<span>
								{saveState === "saving"
									? "Saving…"
									: saveState === "unsaved"
										? "Unsaved changes (saves automatically)"
										: saveState === "stale"
											? "Changed in another tab or window"
											: saveState === "error"
												? "Not saved: check your connection"
												: "All changes saved"}
							</span>
						</p>
						{saveState === "stale" ? (
							<button type="button" onClick={() => void reload()} className="text-sm font-medium text-indigo-600 underline">
								Reload the latest saved code
							</button>
						) : null}

						<div className="flex flex-wrap gap-2">
							<button
								type="button"
								disabled
								aria-describedby="coding-run-why"
								className={`${btn} border border-slate-200 text-slate-500 dark:border-white/10`}
							>
								Run
							</button>
							<button
								type="button"
								onClick={() => void submit()}
								disabled={blocked !== null}
								aria-describedby="coding-submit-why"
								className={`${btn} bg-gradient-to-r from-indigo-500 to-violet-500 text-white`}
							>
								{busy === "submit" ? <Spinner /> : null}
								Submit for grading ({attempt.submissions_remaining} left)
							</button>
							<button
								type="button"
								onClick={() => void hint()}
								disabled={!open || hintsLeft <= 0 || busy !== null}
								className={`${btn} border border-slate-300 dark:border-white/20`}
							>
								{busy === "hint" ? <Spinner /> : null}
								Open a hint ({Math.max(0, hintsLeft)} left)
							</button>
							<button
								type="button"
								onClick={() => void done()}
								disabled={!open || busy !== null}
								className={`${btn} border border-slate-300 dark:border-white/20`}
							>
								{busy === "done" ? <Spinner /> : null}
								Done with this problem
							</button>
						</div>
						<p id="coding-run-why" className="text-xs text-slate-500 dark:text-slate-400">
							Run is not available yet: code is not executed in review-only mode. Hints never lower your score.
						</p>
						<p id="coding-submit-why" className="text-xs text-slate-500 dark:text-slate-400">
							{blocked ?? `You can submit up to ${state.graded_submissions_per_problem} times per problem.`}
						</p>
						{attempt.explanation?.length ? (
							<ExplanationPanel attempt={attempt} busy={busy !== null} onAnswer={explain} />
						) : null}
					</div>

					<div id="coding-panel-submissions" role="tabpanel" aria-labelledby="coding-tab-submissions" className={`${panelClass("submissions")} space-y-2`}>
						<h3 className="text-sm font-semibold">Your submissions</h3>
						<SubmissionHistory submissions={subs[attempt.seq] ?? []} total={state.graded_submissions_per_problem} />
					</div>
				</div>
			</div>

			{error ? (
				<p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
					{error}
				</p>
			) : null}
			<p className="sr-only" aria-live="polite">
				{announce}
			</p>
		</section>
	)
}
