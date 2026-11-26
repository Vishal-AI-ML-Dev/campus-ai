"use client"

/**
 * Phase 6.3: one video interview, from lobby to finish.
 *
 * The ONLY place that calls useCamera() for a video session. The lobby and the
 * room are its children and receive the same handle, so the stream the student
 * checked in the lobby continues into the room without reopening the camera.
 *
 *   setup / awaiting start -> VideoLobby (camera + acknowledgement required, D7/D8)
 *   in_progress            -> VideoInterviewRoom (camera optional from here, D7)
 *   ended                  -> InterviewFinished (report)
 *
 * Camera lifecycle: never started on load or after a refresh (only by a click
 * in the lobby or room); stopped on any terminal status (finish, abandon,
 * time-out, the interview ending itself) and when this component unmounts
 * (useCamera detaches), e.g. "Start another interview".
 */

import { useEffect, useState } from "react"
import InterviewFinished from "./InterviewFinished"
import {
	describeError,
	LANGUAGE_LABELS,
	mockInterviewApi,
	TYPE_LABELS,
	type InterviewSession,
} from "./mockInterviewApi"
import { useCamera } from "./useCamera"
import VideoInterviewRoom from "./VideoInterviewRoom"
import { createActionGuard, releaseCameraIfEnded, videoStage } from "./videoInterviewFlow"
import VideoLobby from "./VideoLobby"
import CompanyContextNotice from "./company/CompanyContextNotice"

export default function VideoInterviewWorkspace({
	session,
	onChange,
	onNew,
}: {
	session: InterviewSession
	onChange: (s: InterviewSession) => void
	onNew: () => void
}) {
	const camera = useCamera()
	const { stop: stopCamera } = camera
	const stage = videoStage(session)

	// Any terminal status releases the camera, whoever caused it.
	useEffect(() => {
		releaseCameraIfEnded({ stop: stopCamera }, session.status)
	}, [session.status, stopCamera])

	if (stage === "lobby") return <VideoLobbyStep session={session} onChange={onChange} camera={camera} />
	if (stage === "room") return <VideoInterviewRoom session={session} onChange={onChange} camera={camera} />
	return <InterviewFinished session={session} onNew={onNew} />
}

function VideoLobbyStep({
	session,
	onChange,
	camera,
}: {
	session: InterviewSession
	onChange: (s: InterviewSession) => void
	camera: ReturnType<typeof useCamera>
}) {
	const [busy, setBusy] = useState<"start" | "cancel" | null>(null)
	const [error, setError] = useState<string | null>(null)
	const [guard] = useState(createActionGuard)

	function run(kind: "start" | "cancel") {
		void guard.run(kind, async () => {
			setBusy(kind)
			setError(null)
			try {
				const next =
					kind === "start" ? await mockInterviewApi.start(session.id) : await mockInterviewApi.abandon(session.id)
				if (kind === "cancel") camera.stop()
				onChange(next)
			} catch (err) {
				setError(
					describeError(err, kind === "start" ? "Could not start the interview." : "Could not cancel the interview."),
				)
				// It may have been started or cancelled elsewhere (another tab).
				try {
					const fresh = await mockInterviewApi.get(session.id)
					if (fresh.status !== session.status || fresh.awaiting !== session.awaiting) onChange(fresh)
				} catch {
					// keep the error; the buttons stay available for a retry
				}
			} finally {
				setBusy(null)
			}
		})
	}

	return (
		<div className="space-y-3">
			<div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm dark:border-white/10 dark:bg-slate-900">
				<p className="font-semibold">
					Video interview · {TYPE_LABELS[session.interview_type] ?? session.interview_type} · {session.target_role}
				</p>
				<p className="text-slate-500 dark:text-slate-400">
					{session.duration_minutes} minutes · {LANGUAGE_LABELS[session.language] ?? session.language}
					{session.company_name ? ` · ${session.company_name}` : ""}. The interviewer reads each question aloud;
					answer by speaking or typing, then check the text before you submit. The timer starts when you start
					the interview.
				</p>
			</div>
			{/* Phase 8.4: company/role focus disclaimer (text only). */}
			{session.company_context ? <CompanyContextNotice context={session.company_context} /> : null}
			{error ? (
				<p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
					{error}
				</p>
			) : null}
			<VideoLobby
				camera={camera}
				onContinue={() => run("start")}
				onCancel={() => run("cancel")}
				busy={busy !== null}
				continueLabel={busy === "start" ? "Preparing your interview…" : "Start interview"}
			/>
		</div>
	)
}
