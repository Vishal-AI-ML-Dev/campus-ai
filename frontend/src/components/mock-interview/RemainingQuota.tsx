"use client"

/**
 * Phase 7.8: one line on the setup form saying how many practice interviews the
 * student can still start today (or why not). Only the student's own numbers.
 * Renders nothing unless the ops build flag is on, and nothing when the backend
 * answers 404 (institute operations off) or fails - the form works as before.
 */

import { INTERVIEW_OPS_BUILD_ENABLED, opsApi, useLoaded } from "@/components/interview-operations/opsApi"
import { quotaMessage } from "@/components/interview-operations/opsLogic"

export default function RemainingQuota() {
	const me = useLoaded(INTERVIEW_OPS_BUILD_ENABLED ? () => opsApi.usageMe() : null, [])
	const text = me.data ? quotaMessage(me.data) : null
	if (!text) return null
	return (
		<p
			role="status"
			className={`rounded-lg px-3 py-2 text-sm ${
				me.data?.available
					? "bg-slate-50 text-slate-600 dark:bg-white/5 dark:text-slate-300"
					: "bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200"
			}`}
		>
			{text}
		</p>
	)
}
