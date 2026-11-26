"use client"

/**
 * Phase 6.3: video interview room.
 *
 * The same interview as text/voice: the same session API (answer, next,
 * finish), the same idempotent turn_seq answers and retry path, the same
 * Countdown, the browser TTS that reads each question aloud and the Phase 5
 * VoiceAnswerBox for dictation or typing. Only the layout adds a local
 * camera self-view and an interviewer tile.
 *
 * Camera and microphone are separate (D10). The camera handle comes from the
 * workspace (one owner, so the lobby's stream continues here); its stream is
 * video-only. The microphone belongs to browser speech recognition inside
 * VoiceAnswerBox; "Mute mic" stops and disables dictation there. If the
 * camera fails or is off, the interview simply continues (D7).
 *
 * Privacy: the camera is only shown in a <video> element. Only the reviewed
 * answer text is sent to the server.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react"
import { Spinner } from "@/components/ui/states"
import CameraDeviceSelect from "./CameraDeviceSelect"
import CameraPreview from "./CameraPreview"
import EndInterviewControl from "./EndInterviewControl"
import { Countdown } from "./InterviewRoom"
import {
	describeError,
	draftKey,
	mockInterviewApi,
	readDraft,
	TYPE_LABELS,
	writeDraft,
	type InterviewSession,
} from "./mockInterviewApi"
import type { CameraHandle } from "./useCamera"
import { pageHasUserActivation, speechLang, useSpeechSynthesis } from "./useSpeech"
import { cameraStatusText, createActionGuard, micStatusText, type MicState } from "./videoInterviewFlow"
import VoiceAnswerBox, { type AnswerSource } from "./VoiceAnswerBox"

function subscribeOnline(cb: () => void) {
	window.addEventListener("online", cb)
	window.addEventListener("offline", cb)
	return () => {
		window.removeEventListener("online", cb)
		window.removeEventListener("offline", cb)
	}
}

/** Browser connectivity only (the app uses plain requests; there is no live connection). */
function useOnline(): boolean {
	return useSyncExternalStore(subscribeOnline, () => navigator.onLine, () => true)
}

function InterviewerTile({
	question,
	topic,
	followUp,
	speaking,
	thinking,
	questionRef,
}: {
	question: string
	topic: string | null
	followUp: boolean
	speaking: boolean
	thinking: boolean
	/** Phase 6.6: focus target for each new question (read by screen readers on focus). */
	questionRef: React.RefObject<HTMLDivElement | null>
}) {
	return (
		<section
			aria-label="AI interviewer"
			className="flex h-full flex-col rounded-2xl border border-slate-200 bg-gradient-to-br from-indigo-50 to-violet-50 p-4 sm:p-5 dark:border-white/10 dark:from-indigo-500/10 dark:to-violet-500/10"
		>
			<div className="flex items-center gap-3">
				<div
					className={`relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-semibold text-white ${
						speaking ? "ring-4 ring-indigo-300/60 dark:ring-indigo-400/40" : ""
					}`}
					aria-hidden="true"
				>
					AI
				</div>
				<div className="min-w-0">
					<p className="text-sm font-semibold">AI Interviewer</p>
					{/* Visual only: the question itself is announced via focus (no chatty live region). */}
					<p className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
						{speaking ? (
							<>
								<span className="flex h-3 items-end gap-0.5" aria-hidden="true">
									<span className="h-1.5 w-0.5 animate-pulse rounded bg-indigo-500" />
									<span className="h-3 w-0.5 animate-pulse rounded bg-indigo-500 [animation-delay:150ms]" />
									<span className="h-2 w-0.5 animate-pulse rounded bg-indigo-500 [animation-delay:300ms]" />
								</span>
								Speaking…
							</>
						) : thinking ? (
							<>
								<Spinner className="h-3 w-3" /> Preparing the next question…
							</>
						) : (
							"Listening to your answer"
						)}
					</p>
				</div>
			</div>
			<div
				ref={questionRef}
				tabIndex={-1}
				className="mt-4 flex-1 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/60"
			>
				{question ? (
					<>
						<p className="mb-1 text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
							Current question{topic ? ` · ${topic}` : ""}
							{followUp ? " · follow-up" : ""}
						</p>
						<p className="whitespace-pre-wrap text-base leading-relaxed text-slate-900 sm:text-lg dark:text-slate-100">
							{question}
						</p>
					</>
				) : (
					<p className="text-sm text-slate-500 dark:text-slate-400">
						{thinking ? "The interviewer is thinking…" : "Waiting for the next question."}
					</p>
				)}
			</div>
		</section>
	)
}

function ToolbarButton({
	pressed,
	onClick,
	disabled,
	children,
	label,
}: {
	pressed?: boolean
	onClick: () => void
	disabled?: boolean
	children: React.ReactNode
	/** Optional extra name; by default the visible text is the accessible name. */
	label?: string
}) {
	return (
		<button
			type="button"
			onClick={onClick}
			disabled={disabled}
			aria-pressed={pressed}
			aria-label={label}
			className={`rounded-lg border px-3 py-1.5 text-xs font-medium disabled:opacity-50 ${
				pressed
					? "border-amber-400 bg-amber-50 text-amber-800 dark:border-amber-400/40 dark:bg-amber-500/10 dark:text-amber-200"
					: "border-slate-200 text-slate-600 hover:border-indigo-300 dark:border-white/10 dark:text-slate-300"
			}`}
		>
			{children}
		</button>
	)
}

export default function VideoInterviewRoom({
	session,
	onChange,
	camera,
	answerMaxChars = 5000,
}: {
	session: InterviewSession
	onChange: (s: InterviewSession) => void
	camera: CameraHandle
	answerMaxChars?: number
}) {
	const [busy, setBusy] = useState<"answer" | "next" | "finish" | null>(null)
	const [error, setError] = useState<string | null>(null)
	const questionRef = useRef<HTMLDivElement>(null)
	const [micMuted, setMicMuted] = useState(false)
	const [mic, setMic] = useState<Omit<MicState, "muted">>({ supported: true, listening: false, blocked: false })
	const [voiceMuted, setVoiceMuted] = useState(false)
	const voiceMutedRef = useRef(false)
	const [guard] = useState(createActionGuard)
	const online = useOnline()
	const tts = useSpeechSynthesis()
	const { speak, cancel: cancelSpeech } = tts
	const lang = speechLang(session.language)

	const currentSeq = session.awaiting === "answer" ? session.current_turn_seq : null
	const currentTurn = currentSeq !== null ? session.turns.find((t) => t.seq === currentSeq) : undefined
	const currentQuestion = currentTurn?.question ?? ""
	const thinking = busy === "answer" || busy === "next"
	const lastSeq = session.turns.at(-1)?.seq
	const answeredTurns = session.turns.filter((t) => t.answer_text !== null)

	// Read each new question aloud once (same rule and storage key as the voice room).
	useEffect(() => {
		if (currentSeq === null || !currentQuestion) return
		const spokenKey = `mock-interview:spoken:${session.id}`
		if (readDraft(spokenKey) === String(currentSeq)) return
		if (voiceMutedRef.current || !pageHasUserActivation()) return
		speak(currentQuestion, lang, () => writeDraft(spokenKey, String(currentSeq)))
		return () => cancelSpeech()
	}, [session.id, currentSeq, currentQuestion, lang, speak, cancelSpeech])

	// Phase 6.6: move focus to each new question so keyboard and screen-reader
	// users are not left on the answer box that was just replaced.
	useEffect(() => {
		if (currentSeq !== null) questionRef.current?.focus({ preventScroll: true })
	}, [currentSeq])

	const onMicStateChange = useCallback((s: { supported: boolean; listening: boolean; blocked: boolean }) => {
		setMic(s)
	}, [])

	function toggleVoice() {
		const next = !voiceMuted
		voiceMutedRef.current = next
		setVoiceMuted(next)
		if (next) cancelSpeech()
	}

	async function resync(seq?: number) {
		try {
			const fresh = await mockInterviewApi.get(session.id)
			if (seq !== undefined && fresh.current_turn_seq !== seq) {
				writeDraft(draftKey(session.id, seq), "")
				writeDraft(`${draftKey(session.id, seq)}:stt`, "")
			}
			onChange(fresh)
		} catch {
			// keep the current view; the error banner offers a retry
		}
	}

	function submitAnswer(seq: number, text: string, source: AnswerSource, clear: () => void) {
		void guard.run("answer", async () => {
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
		})
	}

	function fetchNext() {
		void guard.run("next", async () => {
			setBusy("next")
			setError(null)
			try {
				onChange(await mockInterviewApi.next(session.id))
			} catch (err) {
				setError(describeError(err, "Could not load the next question."))
			} finally {
				setBusy(null)
			}
		})
	}

	function finish() {
		return guard.run("finish", async () => {
			cancelSpeech()
			setBusy("finish")
			setError(null)
			try {
				const done = await mockInterviewApi.finish(session.id)
				writeDraft(`mock-interview:spoken:${session.id}`, "")
				camera.stop() // released at once; the workspace also stops it on any terminal status
				onChange(done)
			} catch (err) {
				setError(describeError(err, "Could not end the interview."))
			} finally {
				setBusy(null)
			}
		})
	}

	const { progress } = session
	const budget = progress.question_budget ?? Math.max(progress.answered, 1)
	const pct = Math.min(100, Math.round((progress.answered / budget) * 100))
	const camOn = camera.status === "on"
	const micText = micStatusText({ ...mic, muted: micMuted, answering: session.awaiting === "answer" })

	return (
		<div className="flex flex-col gap-3">
			{/* Toolbar */}
			<header className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 dark:border-white/10 dark:bg-slate-900">
				<div className="min-w-0">
					{/* Wraps on phones: a no-wrap title would widen the dashboard shell. */}
					<p className="break-words text-sm font-semibold sm:truncate">
						Video interview · {TYPE_LABELS[session.interview_type] ?? session.interview_type} ·{" "}
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
				<div className="flex flex-wrap items-center gap-2">
					{/* Toggles keep one name; aria-pressed carries the state (a changing name
					    plus aria-pressed would contradict itself for screen readers). */}
					<ToolbarButton
						pressed={micMuted}
						onClick={() => setMicMuted((m) => !m)}
						disabled={!mic.supported || mic.blocked}
					>
						Mute mic
					</ToolbarButton>
					<ToolbarButton
						onClick={() => (camOn ? camera.stop() : void camera.start())}
						disabled={camera.status === "starting"}
					>
						{camera.status === "starting" ? "Starting…" : camOn ? "Stop camera" : "Start camera"}
					</ToolbarButton>
					{tts.supported ? (
						<ToolbarButton pressed={voiceMuted} onClick={toggleVoice}>
							Mute voice
						</ToolbarButton>
					) : null}
					{session.remaining_seconds !== null ? (
						<Countdown
							key={`${session.id}-${session.turns.length}-${session.awaiting}-${session.remaining_seconds}`}
							seconds={session.remaining_seconds}
						/>
					) : null}
					<EndInterviewControl busy={busy !== null} ending={busy === "finish"} onConfirm={finish} />
				</div>
				<div className="flex w-full flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
					<span data-testid="camera-status">{cameraStatusText(camera)}</span>
					<span data-testid="mic-status">{micText}</span>
					{!online ? (
						<span className="font-medium text-red-600 dark:text-red-400" role="status">
							You are offline. Your answer is kept here until the connection is back.
						</span>
					) : null}
				</div>
			</header>

			{/* Interviewer + candidate */}
			<div className="grid gap-3 lg:grid-cols-2">
				<InterviewerTile
					question={currentQuestion}
					topic={currentTurn?.topic ?? null}
					followUp={currentTurn?.is_follow_up ?? false}
					speaking={tts.speaking}
					thinking={thinking}
					questionRef={questionRef}
				/>
				<section aria-label="Your camera" className="space-y-2">
					<CameraPreview stream={camera.stream} status={camera.status} error={camera.error} />
					{!camOn ? (
						<div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
							<span>The camera is optional now; the interview continues without it.</span>
							<button
								type="button"
								onClick={() => void camera.start()}
								disabled={camera.status === "starting"}
								className="rounded-lg border border-slate-200 px-3 py-1 font-medium text-slate-700 hover:border-indigo-300 disabled:opacity-50 dark:border-white/10 dark:text-slate-200"
							>
								{camera.status === "error" ? "Try camera again" : "Turn camera on"}
							</button>
							{camera.error?.reloadSuggested ? (
								<button
									type="button"
									onClick={camera.reloadToEnableCamera}
									className="rounded-lg border border-indigo-300 px-3 py-1 font-medium text-indigo-700 dark:border-indigo-400/40 dark:text-indigo-300"
								>
									Reload to enable camera
								</button>
							) : null}
						</div>
					) : null}
					{camera.devices.filter((d) => d.deviceId).length > 1 ? (
						<details className="text-sm">
							<summary className="cursor-pointer text-xs text-slate-500 dark:text-slate-400">Change camera</summary>
							<div className="mt-2">
								<CameraDeviceSelect
									id="mi-room-camera-select"
									devices={camera.devices}
									selectedDeviceId={camera.selectedDeviceId}
									onSelect={(id) => void camera.switchDevice(id)}
									disabled={camera.status === "starting"}
								/>
							</div>
						</details>
					) : null}
				</section>
			</div>

			{error ? (
				<div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
					{error}
				</div>
			) : null}

			{/* Answer */}
			<section aria-label="Your answer" className="rounded-2xl border border-slate-200 bg-white p-3 sm:p-4 dark:border-white/10 dark:bg-slate-900">
				{session.awaiting === "answer" ? (
					<VoiceAnswerBox
						key={`${session.id}-${session.current_turn_seq}`}
						session={session}
						busy={busy !== null}
						maxChars={answerMaxChars}
						onSubmit={submitAnswer}
						onBeforeListen={cancelSpeech}
						onHearQuestion={tts.supported && currentQuestion ? () => speak(currentQuestion, lang) : undefined}
						speaking={tts.speaking}
						micMuted={micMuted}
						onMicStateChange={onMicStateChange}
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
				) : (
					<p className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400" role="status">
						<Spinner /> Interviewer is thinking…
					</p>
				)}
			</section>

			{answeredTurns.length > 0 ? (
				<details className="rounded-2xl border border-slate-200 bg-white p-3 text-sm sm:p-4 dark:border-white/10 dark:bg-slate-900">
					<summary className="cursor-pointer font-medium">Conversation so far ({answeredTurns.length})</summary>
					<ol className="mt-3 space-y-3">
						{answeredTurns.map((t) => (
							<li key={t.seq}>
								<p className="font-medium text-slate-800 dark:text-slate-100">
									Q{t.seq}. <span className="whitespace-pre-wrap">{t.question}</span>
								</p>
								<p className="mt-1 whitespace-pre-wrap text-slate-600 dark:text-slate-300">
									{t.answer_text}
									{t.answer_source === "stt" ? (
										<span className="ml-2 text-[11px] uppercase tracking-wide text-slate-400">· spoken</span>
									) : null}
								</p>
							</li>
						))}
					</ol>
				</details>
			) : null}
		</div>
	)
}
