"use client"

/**
 * Placement Preparation -> AI Mock Interview (landing).
 *
 * Feature-flagged. Phase 1 only decides whether the feature is available and
 * says so; interview setup arrives in Phase 3.
 *
 * Backend: GET /mock-interview/status -> { enabled }
 */

import { useCurrentUser } from "@/lib/auth"
import { useApiResource } from "@/lib/useApiResource"
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states"
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

	return (
		<EmptyState
			title="Interview setup is coming soon"
			description="Mock interviews are switched on for your institute. HR and technical practice interviews will appear here first."
		/>
	)
}
