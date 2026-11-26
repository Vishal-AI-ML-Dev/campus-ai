"use client"

/**
 * Interview preparation for one drive (Phase 9.4). Reuses the existing AI Mock
 * Interview: the link opens its normal setup form pre-filled with this drive's
 * role, company and (when published and enabled) the company profile. Nothing
 * is started automatically and no interview data is shared with staff.
 */

import Link from "next/link"
import { ErrorState, LoadingState } from "@/components/ui/states"
import { insightsApi } from "./placementApi"
import { buttonClass, cardClass } from "./HubUi"
import { COMPLETION_LABEL, formatDateTime } from "./placementLogic"
import { useAsync } from "./useAsync"

export default function DrivePrepCard({ driveId }: { driveId: number }) {
	const res = useAsync(`prep:${driveId}`, () => insightsApi.preparation(driveId))
	if (res.loading && !res.data) return <LoadingState message="Loading preparation…" />
	if (res.error) return <ErrorState message={res.error} onRetry={res.reload} />
	const p = res.data
	if (!p) return null
	return (
		<section className={cardClass} aria-labelledby="prep-h">
			<h3 id="prep-h" className="text-lg font-semibold">
				Prepare for this interview
			</h3>
			{p.practice_available && p.practice_link ? (
				<>
					<p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
						Practise a mock interview for <strong>{p.suggested_role}</strong> at {p.suggested_company}
						{p.company_profile ? ` using the "${p.company_profile.name}" company profile` : ""}. Your answers and report
						stay private to you.
					</p>
					<Link href={p.practice_link} className={`${buttonClass} mt-3 inline-block`}>
						Practise now
					</Link>
				</>
			) : (
				<p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
					AI mock interview practice is not available for your institute yet.
				</p>
			)}
			<p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
				You have completed {p.my_completed_practice} practice interview{p.my_completed_practice === 1 ? "" : "s"}
				{p.my_last_practice_at ? `, most recently ${formatDateTime(p.my_last_practice_at)}` : ""}.
			</p>
			{p.campaigns.length ? (
				<ul className="mt-3 space-y-1 text-sm">
					{p.campaigns.map((c) => (
						<li key={c.id}>
							{c.title}: {COMPLETION_LABEL[c.completion]} (due {formatDateTime(c.due_at)})
						</li>
					))}
				</ul>
			) : null}
		</section>
	)
}
