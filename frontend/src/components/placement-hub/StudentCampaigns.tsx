"use client"

/** The student's assigned interview-practice campaigns (Phase 9.3). Own data only. */

import Link from "next/link"
import { ErrorState } from "@/components/ui/states"
import { insightsApi } from "./placementApi"
import { cardClass } from "./HubUi"
import { COMPLETION_LABEL, formatDateTime } from "./placementLogic"
import { useAsync } from "./useAsync"

export default function StudentCampaigns() {
	const res = useAsync("my-campaigns", () => insightsApi.myCampaigns())
	if (res.error) return <div className="mt-6"><ErrorState message={res.error} onRetry={res.reload} /></div>
	const items = res.data?.items ?? []
	if (!items.length) return null
	return (
		<section aria-labelledby="mc-h" className="mt-6">
			<h3 id="mc-h" className="text-lg font-semibold">
				Interview practice assigned to you
			</h3>
			<ul className="mt-3 grid gap-3 md:grid-cols-2">
				{items.map((c) => (
					<li key={c.id} className={cardClass}>
						<h4 className="font-semibold">{c.title}</h4>
						<p className="text-xs text-slate-600 dark:text-slate-400">
							Due {formatDateTime(c.due_at)}
							{c.interview_type ? ` · ${c.interview_type} interview` : ""}
							{c.drive_label ? ` · for ${c.drive_label}` : ""}
						</p>
						{c.description ? <p className="mt-2 whitespace-pre-line text-sm">{c.description}</p> : null}
						<p className="mt-2 text-sm font-medium">
							<span aria-hidden="true">{c.completion === "pending" ? "○ " : "✓ "}</span>
							{COMPLETION_LABEL[c.completion]}
						</p>
						{c.completion === "pending" && c.status === "active" ? (
							<Link
								href={c.drive_id ? `/dashboard/placement-hub/opportunities/${c.drive_id}` : "/dashboard/mock-interview"}
								className="mt-2 inline-block text-sm text-indigo-700 hover:underline dark:text-indigo-300"
							>
								Start practising {"→"}
							</Link>
						) : null}
					</li>
				))}
			</ul>
		</section>
	)
}
