/**
 * Campus AI - Interview Operations (AI Mock Interview, Phase 7.8).
 *
 * Institute admins and TPOs: usage, tokens, latency, estimated cost (only with an
 * approved price table), AI service health, per-student counts and content-free
 * interview diagnostics. Admins also edit the institute controls and see the
 * audit trail of those changes; TPOs get a read-only view (decision P7-D3).
 *
 * Feature-flagged: build flag NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED (default off)
 * on top of NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED, and the backend's
 * AI_MOCK_INTERVIEW_OPS_ENABLED per institute (404 = not enabled). Deliberately
 * NOT under /dashboard/mock-interview, so the camera permission policy of that
 * route never applies here.
 */

"use client"

import { useState } from "react"
import { useCurrentUser } from "@/lib/auth"
import { ErrorState, LoadingState } from "@/components/ui/states"
import { INTERVIEW_OPS_BUILD_ENABLED, opsApi, useLoaded } from "@/components/interview-operations/opsApi"
import { canViewOps } from "@/components/interview-operations/opsLogic"
import UsageOverview from "@/components/interview-operations/UsageOverview"
import ProviderHealth from "@/components/interview-operations/ProviderHealth"
import ControlsPanel from "@/components/interview-operations/ControlsPanel"
import StudentUsageTable from "@/components/interview-operations/StudentUsageTable"
import SessionDiagnostics from "@/components/interview-operations/SessionDiagnostics"
import ControlsAuditTrail from "@/components/interview-operations/ControlsAuditTrail"

const PERIODS = [7, 30, 90]

function Notice({ title, text }: { title: string; text: string }) {
	return (
		<div>
			<h1 className="text-2xl font-bold">{title}</h1>
			<p className="mt-2 text-slate-500 dark:text-slate-400">{text}</p>
		</div>
	)
}

export default function InterviewOperationsPage() {
	const { user } = useCurrentUser()
	const [days, setDays] = useState(30)
	const allowed = INTERVIEW_OPS_BUILD_ENABLED && canViewOps(user?.role)
	// Cheap probe: 404 = institute operations not enabled for this institute.
	const probe = useLoaded(allowed ? () => opsApi.health() : null, [allowed])

	if (!INTERVIEW_OPS_BUILD_ENABLED)
		return <Notice title="Interview Operations" text="Interview operations are not available in this build." />
	if (!user) return <LoadingState />
	if (!canViewOps(user.role))
		return <Notice title="Interview Operations" text="Interview operations are available to institute admins and TPOs only." />
	if (probe.loading) return <LoadingState message="Loading interview operations…" />
	if (probe.status === 404)
		return <Notice title="Interview Operations" text="Interview operations are not enabled for your institute." />
	if (probe.error)
		return <ErrorState message={probe.error} onRetry={probe.reload} requestId={probe.requestId} />

	return (
		<div className="space-y-6">
			<div className="flex flex-wrap items-end justify-between gap-3">
				<div>
					<h1 className="text-2xl font-bold">Interview Operations</h1>
					<p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
						AI Mock Interview usage for your institute. Only counts, timings and statuses are shown - never
						questions, answers, resumes or job descriptions.
					</p>
				</div>
				<label className="text-sm">
					<span className="mr-2 text-slate-500 dark:text-slate-400">Period</span>
					<select
						value={days}
						onChange={(e) => setDays(Number(e.target.value))}
						className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-slate-900"
					>
						{PERIODS.map((p) => (
							<option key={p} value={p}>Last {p} days</option>
						))}
					</select>
				</label>
			</div>
			<ProviderHealth />
			<UsageOverview days={days} />
			<ControlsPanel role={user.role} />
			<StudentUsageTable days={days} />
			<SessionDiagnostics />
			{user.role === "admin" ? <ControlsAuditTrail /> : null}
		</div>
	)
}
