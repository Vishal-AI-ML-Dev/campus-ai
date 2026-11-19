"use client"

/**
 * Placement Preparation -> AI Mock Interview (landing).
 *
 * Feature-flagged (build flag + GET /mock-interview/status). Phase 3: text HR
 * and technical interviews. The server is the source of truth: on load,
 * GET /mock-interview/sessions/active decides which step to show, so a
 * refresh resumes an interview exactly where it was.
 *
 *   no active session -> setup form
 *   setup             -> instructions + start
 *   in_progress       -> interview room
 *   ended             -> closing message + transcript
 */

import { useState } from "react"

import { useCurrentUser } from "@/lib/auth"
import { useApiResource } from "@/lib/useApiResource"
import { ErrorState, LoadingState } from "@/components/ui/states"
import InterviewFinished from "@/components/mock-interview/InterviewFinished"
import InterviewInstructions from "@/components/mock-interview/InterviewInstructions"
import InterviewRoom from "@/components/mock-interview/InterviewRoom"
import InterviewSetupForm from "@/components/mock-interview/InterviewSetupForm"
import type { InterviewSession } from "@/components/mock-interview/mockInterviewApi"
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
	const session = local === undefined ? active.data : local

	if (local === undefined && active.loading)
		return <LoadingState message="Loading your interview…" />
	if (local === undefined && active.error)
		return (
			<ErrorState message={active.error} onRetry={active.reload} requestId={active.requestId} />
		)

	if (!session)
		return (
			<InterviewSetupForm
				onCreated={setLocal}
				onConflict={() => {
					setLocal(undefined)
					active.reload()
				}}
			/>
		)
	if (session.status === "setup" || session.awaiting === "start")
		return <InterviewInstructions session={session} onChange={setLocal} />
	if (session.status === "in_progress")
		return <InterviewRoom session={session} onChange={setLocal} />
	return <InterviewFinished session={session} onNew={() => setLocal(null)} />
}
