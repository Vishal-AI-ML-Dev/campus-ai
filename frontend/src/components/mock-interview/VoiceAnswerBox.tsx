"use client"

/**
 * Phase 5: answer by voice. The browser turns speech into text, which lands in
 * an editable box; the student reviews/corrects it and submits. Typing always
 * works too (unsupported browser, blocked mic, or preference).
 *
 * The answer is submitted as text with source "stt" when any of the current
 * text was dictated, else "typed" (an informational label only). No audio is
 * ever sent to our server.
 */

import { useEffect, useRef, useState } from "react"
import { Spinner } from "@/components/ui/states"
import { draftKey, LANGUAGE_LABELS, readDraft, writeDraft, type InterviewSession } from "./mockInterviewApi"
import { speechLang, useSpeechRecognition } from "./useSpeech"

export type AnswerSource = "typed" | "stt"

function MicIcon({ className = "h-4 w-4" }: { className?: string }) {
	return (
		<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className} aria-hidden="true">
			<rect x="9" y="3" width="6" height="11" rx="3" />
			<path d="M5 11a7 7 0 0 0 14 0M12 18v3" strokeLinecap="round" />
		</svg>
	)
}

export default function VoiceAnswerBox({
	session,
	busy,
	maxChars,
	onSubmit,
	onBeforeListen,
	onHearQuestion,
	speaking = false,
}: {
	session: InterviewSession
	busy: boolean
	maxChars: number
	onSubmit: (seq: number, text: string, source: AnswerSource, clear: () => void) => void
	/** Called before the mic opens (stops the interviewer's voice so it isn't transcribed). */
	onBeforeListen: () => void
	/** Re-read the question aloud; undefined when speech output is unavailable. */
	onHearQuestion?: () => void
	speaking?: boolean
}) {
	const seq = session.current_turn_seq ?? 0
	const key = draftKey(session.id, seq)
	const sttKey = `${key}:stt`
	const [text, setText] = useState(() => readDraft(key))
	const [dictated, setDictated] = useState(() => readDraft(sttKey) === "1")
	const [limitHit, setLimitHit] = useState(false)
	// Source of truth for appending dictation. Updated synchronously in
	// update() only: syncing it from state in an effect could overwrite a
	// phrase that arrived between a render and its effect (lost speech).
	const textRef = useRef(text)

	function markDictated(on: boolean) {
		setDictated(on)
		writeDraft(sttKey, on ? "1" : "")
	}

	function update(value: string, fromSpeech = false) {
		const v = value.slice(0, maxChars)
		textRef.current = v
		setText(v)
		writeDraft(key, v)
		if (fromSpeech) markDictated(true)
		else if (!v.trim()) markDictated(false) // cleared, then typed: no longer spoken
	}

	const recLang = speechLang(session.language)
	const rec = useSpeechRecognition(recLang, (phrase) => {
		const prev = textRef.current.trimEnd()
		const next = prev ? `${prev} ${phrase}` : phrase
		if (next.length > maxChars) setLimitHit(true)
		update(next, true)
	})
	const { listening, stop } = rec

	// Never keep the mic open while an answer is sending or the interview is
	// ending, or once the answer is full (further speech would be cut off).
	useEffect(() => {
		if (listening && (busy || limitHit)) stop()
	}, [busy, limitHit, listening, stop])

	function clear() {
		update("")
	}

	function submit() {
		const trimmed = text.trim()
		if (!trimmed || busy || listening) return
		onSubmit(seq, trimmed, dictated ? "stt" : "typed", clear)
	}

	function toggleMic() {
		if (listening) {
			stop()
			return
		}
		setLimitHit(false)
		onBeforeListen()
		rec.listen()
	}

	const full = text.length >= maxChars
	const status = listening
		? "Listening…"
		: limitHit
			? "Answer limit reached. Recording stopped."
			: rec.stoppedUnexpectedly
				? "Listening stopped. Press Speak more to continue, or edit and submit."
				: ""

	return (
		<form
			onSubmit={(e) => {
				e.preventDefault()
				submit()
			}}
			className="space-y-2"
		>
			{rec.supported ? (
				<div className="flex flex-wrap items-center gap-3">
					<button
						type="button"
						onClick={toggleMic}
						disabled={!listening && (busy || full || rec.blocked)}
						className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium text-white disabled:opacity-50 ${
							listening ? "bg-red-600 hover:bg-red-700" : "bg-indigo-600 hover:bg-indigo-700"
						}`}
					>
						<MicIcon />
						{listening ? "Stop speaking" : text ? "Speak more" : "Start speaking"}
					</button>
					{listening ? (
						<span className="h-2 w-2 animate-pulse rounded-full bg-red-600" aria-hidden="true" />
					) : null}
					<span className="text-xs text-slate-400">
						Recognition language: {LANGUAGE_LABELS[session.language] ?? session.language} ({recLang})
					</span>
					{onHearQuestion ? (
						<button
							type="button"
							onClick={onHearQuestion}
							disabled={listening}
							title={listening ? "Stop speaking first" : undefined}
							className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:border-indigo-300 disabled:opacity-50 dark:border-white/10 dark:text-slate-300"
						>
							{speaking ? "Speaking…" : "Hear question"}
						</button>
					) : null}
				</div>
			) : (
				<div className="space-y-2">
					<p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
						Your browser can&apos;t turn speech into text. Type your answer below, or use Chrome or Edge for
						voice answers.
					</p>
					{onHearQuestion ? (
						<button
							type="button"
							onClick={onHearQuestion}
							className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:border-indigo-300 dark:border-white/10 dark:text-slate-300"
						>
							{speaking ? "Speaking…" : "Hear question"}
						</button>
					) : null}
				</div>
			)}

			{/* Always present so screen readers announce changes to it. */}
			<p
				role="status"
				className={`min-h-0 text-sm empty:hidden ${
					listening || limitHit ? "text-red-600 dark:text-red-400" : "text-slate-500 dark:text-slate-400"
				}`}
			>
				{status}
			</p>

			{rec.error ? (
				<p role="alert" className="text-xs text-red-600 dark:text-red-400">
					{rec.error}
				</p>
			) : null}

			<label htmlFor="mi-answer" className="sr-only">
				Your answer (dictated text appears here; you can edit it)
			</label>
			<textarea
				id="mi-answer"
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
				placeholder={rec.supported ? "Press Start speaking, or type your answer…" : "Type your answer…"}
				aria-describedby="mi-answer-help"
				className="w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-400/30 disabled:opacity-60 dark:border-white/10 dark:bg-slate-900"
			/>
			{rec.interim ? (
				<p className="text-sm italic text-slate-400" aria-hidden="true">
					{rec.interim}…
				</p>
			) : null}
			<div className="flex flex-wrap items-center justify-between gap-2">
				<p id="mi-answer-help" className="text-xs text-slate-400">
					{listening ? "Stop speaking to review and submit · " : "Check the text before you submit · "}
					{text.length.toLocaleString()} / {maxChars.toLocaleString()}
				</p>
				<button
					type="submit"
					disabled={busy || listening || !text.trim()}
					className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
				>
					{busy ? <Spinner /> : null}
					Submit answer
				</button>
			</div>
		</form>
	)
}
