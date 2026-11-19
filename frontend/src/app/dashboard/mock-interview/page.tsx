"use client"

/**
 * Placement Preparation -> AI Mock Interview (landing).
 *
 * Feature-flagged (build flag + GET /mock-interview/status). Phase 3: text HR
 * and technical interviews; Phase 4: feedback reports + history. The server is the source of truth: on load,
 * GET /mock-interview/sessions/active decides which step to show, so a
 * refresh resumes an interview exactly where it was.
 *
 *   no active session -> setup form
 *   setup             -> instructions + start
 *   in_progress       -> interview room
 *   ended             -> closing message + feedback report
 * Under the setup form: past interviews; opening one shows its report.
 */

import { useState } from "react"

import { useCurrentUser } from "@/lib/auth"
import { useApiResource } from "@/lib/useApiResource"
import { ErrorState, LoadingState } from "@/components/ui/states"
import InterviewFinished from "@/components/mock-interview/InterviewFinished"
import InterviewHistory from "@/components/mock-interview/InterviewHistory"
import InterviewInstructions from "@/components/mock-interview/InterviewInstructions"
import InterviewRoom from "@/components/mock-interview/InterviewRoom"
import InterviewSetupForm from "@/components/mock-interview/InterviewSetupForm"
import VideoInterviewWorkspace from "@/components/mock-interview/VideoInterviewWorkspace"
import type { HistoryItem, InterviewSession } from "@/components/mock-interview/mockInterviewApi"
import MockInterviewUnavailableNotice from "@/components/mock-interview/MockInterviewUnavailableNotice"
import {
	MOCK_INTERVIEW_BUILD_ENABLED,
	type MockInterviewStatus,
} from "@/components/mock-interview/mockInterviewFlags"

export default function MockInterviewPage() {
	return (
		<div className="space-y-4">
			<div>
				<h1 className="text-xl font-semibold text-gray-900 dark:text-slate-100">
					AI Mock Interview
				</h1>
				<p className="text-sm text-gray-500 dark:text-slate-400">
					Practice placement interviews and get feedback on your answers.
				</p>
			</div>
			{MOCK_INTERVIEW_BUILD_ENABLED ? (
				<MockInterviewLanding />
			) : (
				<MockInterviewUnavailableNotice />
			)}
		</div>
	)
}

function MockInterviewLanding() {
	const { user } = useCurrentUser()
	const status = useApiResource<MockInterviewStatus>("/mock-interview/status", {
		fallbackMessage: "Could not check whether mock interviews are available.",
	})

	if (status.loading) return <LoadingState message="Checking availability…" />
	if (status.error)
		return (
			<ErrorState
				message={status.error}
				onRetry={status.reload}
				requestId={status.requestId}
			/>
		)
	if (!status.data?.enabled || user?.role !== "student")
		return <MockInterviewUnavailableNotice />

	return <InterviewWorkspace />
}

function InterviewWorkspace() {
	const active = useApiResource<InterviewSession | null>("/mock-interview/sessions/active", {
		fallbackMessage: "Could not load your interview.",
	})
	// undefined = follow the server's active session; null = show the setup form.
	const [local, setLocal] = useState<InterviewSession | null | undefined>(undefined)
	// A past interview opened from the history list.
	const [viewing, setViewing] = useState<HistoryItem | null>(null)
	const session = local === undefined ? active.data : local

	if (local === undefined && active.loading)
		return <LoadingState message="Loading your interview…" />
	if (local === undefined && active.error)
		return (
			<ErrorState message={active.error} onRetry={active.reload} requestId={active.requestId} />
		)

	if (!session && viewing !== null)
		return <PastInterview item={viewing} onBack={() => setViewing(null)} />
	if (!session)
		return (
			<div className="space-y-6">
				<InterviewSetupForm
					onCreated={setLocal}
					onConflict={() => {
						setLocal(undefined)
						active.reload()
					}}
				/>
				<InterviewHistory onOpen={setViewing} />
			</div>
		)
	// Phase 6.3: a video session (lobby -> room -> finished) has its own
	// workspace, the single owner of the camera. Text and voice are unchanged.
	if (session.mode === "video")
		return <VideoInterviewWorkspace session={session} onChange={setLocal} onNew={() => setLocal(null)} />
	if (session.status === "setup" || session.awaiting === "start")
		return <InterviewInstructions session={session} onChange={setLocal} />
	if (session.status === "in_progress")
		return <InterviewRoom session={session} onChange={setLocal} />
	return <InterviewFinished session={session} onNew={() => setLocal(null)} />
}

function PastInterview({ item, onBack }: { item: HistoryItem; onBack: () => void }) {
	const past = useApiResource<InterviewSession>(`/mock-interview/sessions/${item.id}`, {
		fallbackMessage: "Could not load this interview.",
	})
	if (past.loading) return <LoadingState message="Loading interview…" />
	if (past.error || !past.data)
		return (
			<div className="space-y-3">
				<ErrorState
					message={past.error ?? "Could not load this interview."}
					onRetry={past.reload}
					requestId={past.requestId}
				/>
				<button type="button" onClick={onBack} className="text-sm font-medium text-indigo-600 dark:text-indigo-400">
					Back to interviews
				</button>
			</div>
		)
	return (
		<InterviewFinished
			session={past.data}
			onNew={onBack}
			newLabel="Back to interviews"
			reportExists={item.report !== null}
		/>
	)
}
