"use client"

/** Recent changes to the interview controls (Phase 7.8; admins only - /audit is admin-only). */

import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states"
import { opsApi, useLoaded } from "./opsApi"

export default function ControlsAuditTrail() {
	const trail = useLoaded(() => opsApi.auditTrail(), [])
	return (
		<section aria-labelledby="ops-audit" className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-slate-900">
			<h2 id="ops-audit" className="text-lg font-semibold">Control changes</h2>
			{trail.loading ? (
				<LoadingState message="Loading audit trail…" />
			) : trail.error || !trail.data ? (
				<ErrorState message={trail.error ?? "Could not load the audit trail."} onRetry={trail.reload} requestId={trail.requestId} />
			) : trail.data.length === 0 ? (
				<EmptyState title="No changes yet." />
			) : (
				<ul className="mt-3 space-y-2 text-sm">
					{trail.data.map((a) => (
						<li key={a.id} className="rounded-lg border border-slate-100 p-3 dark:border-white/5">
							<p>{a.summary}</p>
							<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
								{new Date(a.created_at).toLocaleString()} · {a.actor_email ?? "(deleted user)"}
							</p>
						</li>
					))}
				</ul>
			)}
		</section>
	)
}
