"use client"

/**
 * Active text interview: transcript, current question, answer box, progress,
 * countdown, end button. All state comes from the server, so a refresh
 * resumes exactly where the student left off.
 *
 * Retry safety: an answer is keyed by its question's seq, so re-sending after
 * a network error can never create a duplicate. When the server saved the
 * answer but the AI could not produce the next question (awaiting =
 * "next_question"), the student retries with POST /next.
 *
 * Phase 5 voice sessions: each new question is read aloud (when the browser
 * allows speech, i.e. after a click) and the answer box takes dictation.
 * Text sessions render exactly as before.
 */

import { useEffect, useRef, useState } from "react"
import { Spinner } from "@/components/ui/states"
import {
	describeError,
	draftKey,
	mockInterviewApi,
	readDraft,
	TYPE_LABELS,
	writeDraft,
	type InterviewSession,
} from "./mockInterviewApi"
import { pageHasUserActivation, speechLang, useSpeechSynthesis } from "./useSpeech"
import EndInterviewControl from "./EndInterviewControl"
import VoiceAnswerBox, { type AnswerSource } from "./VoiceAnswerBox"

function formatClock(totalSeconds: number): string {
	const s = Math.max(0, totalSeconds)
	const m = Math.floor(s / 60)
	return `${m}:${String(s % 60).padStart(2, "0")}`
}

/** Counts down locally from the server's remaining_seconds (re-keyed on every update). Reused by the video room. */
export function Countdown({ seconds }: { seconds: number }) {
	const [startedAt] = useState(() => Date.now())
	const [now, setNow] = useState(startedAt)
	useEffect(() => {
		const id = window.setInterval(() => setNow(Date.now()), 1000)
		return () => window.clearInterval(id)
	}, [])
	const left = seconds - Math.floor((now - startedAt) / 1000)
	const low = left <= 60
	// Phase 6.6: screen readers hear only these milestones (never every second).
	// A live region announces changes, not its content at mount, so a re-keyed
	// Countdown does not repeat a milestone that was already passed.
	const milestone =
		left <= 0 ? "Time is up. Submit your last answer." : left <= 60 ? "One minute remaining." : left <= 300 ? "Five minutes remaining." : ""
	return (
		<>
			<span
				role="timer"
				className={`font-mono text-sm tabular-nums ${low ? "text-red-600 dark:text-red-400" : ""}`}
				aria-label={left > 0 ? `${Math.ceil(left / 60)} minutes remaining` : "Time is up"}
			>
				{left > 0 ? formatClock(left) : "Time's up - submit your last answer"}
			</span>
			<span className="sr-only" aria-live="polite">
				{milestone}
			</span>
		</>
	)
}

function AnswerBox({
	session,
	busy,
	maxChars,
	onSubmit,
}: {
	session: InterviewSession
	busy: boolean
	maxChars: number
	onSubmit: (seq: number, text: string, clear: () => void) => void
}) {
	const seq = session.current_turn_seq ?? 0
	const key = draftKey(session.id, seq)
	const [text, setText] = useState(() => readDraft(key))
	const ref = useRef<HTMLTextAreaElement>(null)

	useEffect(() => {
		ref.current?.focus()
	}, [])

	function update(value: string) {
		setText(value)
		writeDraft(key, value)
	}

	function submit() {
		const trimmed = text.trim()
		if (!trimmed || busy) return
		onSubmit(seq, trimmed, () => update(""))
	}

	return (
		<form
			onSubmit={(e) => {
				e.preventDefault()
				submit()
			}}
			className="space-y-2"
		>
			<label htmlFor="mi-answer" className="sr-only">
				Your answer
			</label>
			<textarea
				id="mi-answer"
				ref={ref}
				rows={4}
				maxLength={maxChars}
				value={text}
				disabled={busy}
				onChange={(e) => update(e.target.value)}
				onKeyDown={(e) => {
					if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
						e.preventDefault()
						submit()
					}
				}}
				placeholder="Type your answer…"
				aria-describedby="mi-answer-help"
				className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-400/30 disabled:opacity-60 dark:border-white/10 dark:bg-slate-900"
			/>
			<div className="flex items-center justify-between gap-2">
				<p id="mi-answer-help" className="text-xs text-slate-400">
					<span className="hidden sm:inline">Ctrl+Enter to submit · </span>
					{text.length.toLocaleString()} / {maxChars.toLocaleString()}
				</p>
				<button
					type="submit"
					disabled={busy || !text.trim()}
					className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
				>
					{busy ? <Spinner /> : null}
					Submit answer
				</button>
			</div>
		</form>
	)
}

export default function InterviewRoom({
	session,
	onChange,
	answerMaxChars = 5000,
}: {
	session: InterviewSession
	onChange: (s: InterviewSession) => void
	answerMaxChars?: number
}) {
	const [busy, setBusy] = useState<"answer" | "next" | "finish" | null>(null)
	const [error, setError] = useState<string | null>(null)
	const endRef = useRef<HTMLDivElement>(null)
	const isVoice = session.mode === "voice"
	const tts = useSpeechSynthesis()
	const { speak, cancel: cancelSpeech } = tts
	const [muted, setMuted] = useState(false)
	const mutedRef = useRef(false)
	const lang = speechLang(session.language)
	const currentSeq = session.awaiting === "answer" ? session.current_turn_seq : null
	const currentQuestion =
		currentSeq !== null ? (session.turns.find((t) => t.seq === currentSeq)?.question ?? "") : ""

	useEffect(() => {
		endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
	}, [session.turns.length, busy])

	// Voice: read each new question aloud once. A question counts as read only
	// when speech actually starts (so a blocked or cancelled attempt can't mark
	// it), and the mark survives a refresh, which therefore doesn't replay it.
	// Skipped when muted or before any click (browsers block that speech);
	// "Hear question" is always available instead.
	useEffect(() => {
		if (!isVoice || currentSeq === null || !currentQuestion) return
		const spokenKey = `mock-interview:spoken:${session.id}`
		if (readDraft(spokenKey) === String(currentSeq)) return
		if (mutedRef.current || !pageHasUserActivation()) return
		speak(currentQuestion, lang, () => writeDraft(spokenKey, String(currentSeq)))
		return () => cancelSpeech()
	}, [isVoice, session.id, currentSeq, currentQuestion, lang, speak, cancelSpeech])

	function toggleMute() {
		const next = !muted
		mutedRef.current = next
		setMuted(next)
		if (next) cancelSpeech()
	}

	/** After a failure, re-read the server state so the UI matches what was saved. */
	async function resync(seq?: number) {
		try {
			const fresh = await mockInterviewApi.get(session.id)
			// The answer reached the server: its local draft is no longer needed.
			if (seq !== undefined && fresh.current_turn_seq !== seq) {
				writeDraft(draftKey(session.id, seq), "")
				writeDraft(`${draftKey(session.id, seq)}:stt`, "")
			}
			onChange(fresh)
		} catch {
			// keep the current view; the error banner offers a retry
		}
	}

	async function submitAnswer(seq: number, text: string, clear: () => void, source?: AnswerSource) {
		setBusy("answer")
		setError(null)
		cancelSpeech()
		try {
			const next = await mockInterviewApi.answer(session.id, seq, text, source)
			clear()
			onChange(next)
		} catch (err) {
			setError(describeError(err, "Could not submit your answer."))
			await resync(seq)
		} finally {
			setBusy(null)
		}
	}

	async function fetchNext() {
		setBusy("next")
		setError(null)
		try {
			onChange(await mockInterviewApi.next(session.id))
		} catch (err) {
			setError(describeError(err, "Could not load the next question."))
		} finally {
			setBusy(null)
		}
	}

	async function finish() {
		cancelSpeech()
		setBusy("finish")
		setError(null)
		try {
			const done = await mockInterviewApi.finish(session.id)
			writeDraft(`mock-interview:spoken:${session.id}`, "")
			onChange(done)
		} catch (err) {
			setError(describeError(err, "Could not end the interview."))
		} finally {
			setBusy(null)
		}
	}

	const { progress } = session
	const budget = progress.question_budget ?? Math.max(progress.answered, 1)
	const pct = Math.min(100, Math.round((progress.answered / budget) * 100))
	const thinking = busy === "answer" || busy === "next"
	const lastSeq = session.turns.at(-1)?.seq

	return (
		<div className="flex min-h-[calc(100vh-10rem)] flex-col gap-3">
			<header className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 dark:border-white/10 dark:bg-slate-900">
				<div className="min-w-0">
					<p className="truncate text-sm font-semibold">
						{TYPE_LABELS[session.interview_type] ?? session.interview_type} interview ·{" "}
						{session.target_role}
					</p>
					<div className="mt-1 flex items-center gap-2">
						<div
							className="h-1.5 w-32 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800 sm:w-48"
							role="progressbar"
							aria-label="Interview progress"
							aria-valuemin={0}
							aria-valuemax={budget}
							aria-valuenow={progress.answered}
						>
							<div className="h-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} />
						</div>
						<span className="text-xs text-slate-500 dark:text-slate-400">
							{progress.answered} / ~{budget} answered
						</span>
					</div>
				</div>
				<div className="flex flex-wrap items-center gap-3">
					{isVoice && tts.supported ? (
						<span className="flex items-center gap-2">
							<button
								type="button"
								onClick={toggleMute}
								className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:border-indigo-300 dark:border-white/10 dark:text-slate-300"
							>
								{muted ? "Unmute voice" : "Mute voice"}
							</button>
						</span>
					) : null}
					{session.remaining_seconds !== null ? (
						<Countdown
							key={`${session.id}-${session.turns.length}-${session.awaiting}-${session.remaining_seconds}`}
							seconds={session.remaining_seconds}
						/>
					) : null}
					<EndInterviewControl busy={busy !== null} ending={busy === "finish"} onConfirm={finish} />
				</div>
			</header>

			<div
				className="flex-1 space-y-3 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-3 sm:p-4 dark:border-white/10 dark:bg-slate-900"
				aria-live="polite"
				aria-relevant="additions"
			>
				{session.turns.map((t) => (
					<div key={t.seq} className="space-y-3">
						<div className="flex justify-start">
							<div className="max-w-[90%] rounded-2xl bg-slate-100 px-4 py-2 text-sm text-slate-800 sm:max-w-[80%] dark:bg-slate-800 dark:text-slate-100">
								<p className="mb-1 text-[11px] uppercase tracking-wide text-slate-400">
									Interviewer{t.topic ? ` · ${t.topic}` : ""}
									{t.is_follow_up ? " · follow-up" : ""}
								</p>
								<p className="whitespace-pre-wrap">{t.question}</p>
							</div>
						</div>
						{t.answer_text !== null ? (
							<div className="flex justify-end">
								<p className="max-w-[90%] whitespace-pre-wrap rounded-2xl bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2 text-sm text-white sm:max-w-[80%]">
									{t.answer_text}
									{t.answer_source === "stt" ? (
										<span className="mt-1 block text-[10px] uppercase tracking-wide text-white/70">spoken</span>
									) : null}
								</p>
							</div>
						) : null}
					</div>
				))}
				{thinking ? (
					<div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400" role="status">
						<Spinner /> Interviewer is thinking…
					</div>
				) : null}
				<div ref={endRef} />
			</div>

			{error ? (
				<div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
					{error}
				</div>
			) : null}

			{session.awaiting === "answer" && isVoice ? (
				<VoiceAnswerBox
					key={`${session.id}-${session.current_turn_seq}`}
					session={session}
					busy={busy !== null}
					maxChars={answerMaxChars}
					onSubmit={(seq, text, source, clear) => submitAnswer(seq, text, clear, source)}
					onBeforeListen={cancelSpeech}
					onHearQuestion={tts.supported && currentQuestion ? () => speak(currentQuestion, lang) : undefined}
					speaking={tts.speaking}
					focusOnMount
				/>
			) : session.awaiting === "answer" ? (
				<AnswerBox
					key={`${session.id}-${session.current_turn_seq}`}
					session={session}
					busy={busy !== null}
					maxChars={answerMaxChars}
					onSubmit={submitAnswer}
				/>
			) : session.awaiting === "next_question" ? (
				<div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">
					<span>Your answer to question {lastSeq} is saved. The next question did not load.</span>
					<button
						type="button"
						onClick={fetchNext}
						disabled={busy !== null}
						className="inline-flex items-center gap-2 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
					>
						{busy === "next" ? <Spinner className="h-3 w-3" /> : null}
						Get next question
					</button>
				</div>
			) : null}
		</div>
	)
}
