/**
 * Campus AI - Placement Hub: one drive, as a student sees it (Phase 9.2/9.4).
 *
 * Shows the drive, the per-criterion eligibility explanation (verified data +
 * placement-office records), the application window, apply, and links to
 * interview preparation (Phase 9.4: existing AI Mock Interview; nothing new).
 *
 * Backend: GET /drives/{id}, GET /placement/drives/{id}/my-eligibility,
 * POST /placement/drives/{id}/apply, GET /placement/drives/{id}/preparation.
 */

"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useState } from "react"
import { ErrorState, LoadingState } from "@/components/ui/states"
import { buttonClass, cardClass, Flash, HubFrame, LifecyclePill, YesNo } from "@/components/placement-hub/HubUi"
import { errorText, placementApi } from "@/components/placement-hub/placementApi"
import { formatDateTime, WINDOW_LABEL } from "@/components/placement-hub/placementLogic"
import { useAsync } from "@/components/placement-hub/useAsync"
import DrivePrepCard from "@/components/placement-hub/DrivePrepCard"

function DriveView({ driveId }: { driveId: number }) {
	const drive = useAsync(`d:${driveId}`, () => placementApi.drive(driveId))
	const elig = useAsync(`e:${driveId}`, () => placementApi.myEligibility(driveId))
	const [busy, setBusy] = useState(false)
	const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null)

	async function apply() {
		setBusy(true)
		setMsg(null)
		try {
			await placementApi.apply(driveId)
			setMsg({ kind: "ok", text: "Application submitted. Track it under My applications." })
			elig.reload()
		} catch (e) {
			setMsg({ kind: "error", text: errorText(e) })
		} finally {
			setBusy(false)
		}
	}

	if ((drive.loading && !drive.data) || (elig.loading && !elig.data)) return <LoadingState message="Loading drive…" />
	if (drive.error) return <ErrorState message={drive.error} onRetry={drive.reload} requestId={drive.requestId} />
	if (elig.error) return <ErrorState message={elig.error} onRetry={elig.reload} requestId={elig.requestId} />
	if (!drive.data || !elig.data) return null
	const d = drive.data
	const e = elig.data
	return (
		<div className="space-y-6">
			<Link href="/dashboard/placement-hub" className="text-sm text-indigo-700 hover:underline dark:text-indigo-300">
				{"←"} Back to Placement Hub
			</Link>
			<section className={cardClass} aria-labelledby="drive-h">
				<div className="flex flex-wrap items-start justify-between gap-2">
					<div className="min-w-0">
						<h2 id="drive-h" className="text-2xl font-bold">
							{d.company_name}
						</h2>
						<p className="text-slate-600 dark:text-slate-300">
							{d.role_title}
							{d.location ? ` · ${d.location}` : ""}
							{d.package_lpa != null ? ` · ${d.package_lpa} LPA` : ""}
						</p>
					</div>
					<LifecyclePill lifecycle={e.lifecycle} />
				</div>
				{d.description ? <p className="mt-4 whitespace-pre-line text-sm">{d.description}</p> : null}
				<dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
					<div>
						<dt className="text-xs text-slate-500 dark:text-slate-400">Application window</dt>
						<dd>{WINDOW_LABEL[e.window]}</dd>
					</div>
					<div>
						<dt className="text-xs text-slate-500 dark:text-slate-400">Opens</dt>
						<dd>{formatDateTime(e.applications_open_at)}</dd>
					</div>
					<div>
						<dt className="text-xs text-slate-500 dark:text-slate-400">Closes</dt>
						<dd>{formatDateTime(e.applications_close_at)}</dd>
					</div>
				</dl>
			</section>

			<section className={cardClass} aria-labelledby="elig-h">
				<div className="flex flex-wrap items-center justify-between gap-2">
					<h3 id="elig-h" className="text-lg font-semibold">
						Your eligibility
					</h3>
					<YesNo ok={e.eligible} />
				</div>
				<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
					Checked against your verified records and the details your placement office recorded.
				</p>
				<ul className="mt-3 divide-y divide-slate-200 text-sm dark:divide-white/10">
					{e.reasons.map((r) => (
						<li key={r.criterion} className="flex flex-wrap items-center justify-between gap-2 py-2">
							<span className="font-medium">{r.criterion}</span>
							<span className="flex items-center gap-2">
								<span className="text-slate-600 dark:text-slate-300">{r.detail}</span>
								<span
									aria-label={r.passed ? "met" : "not met"}
									className={r.passed ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300"}
								>
									{r.passed ? "✓" : "✗"}
								</span>
							</span>
						</li>
					))}
				</ul>
				<div className="mt-4 space-y-2">
					{e.blockers.length && !e.can_apply ? (
						<ul className="list-inside list-disc text-sm text-slate-600 dark:text-slate-400">
							{e.blockers.map((b) => (
								<li key={b}>{b}</li>
							))}
						</ul>
					) : null}
					<button type="button" className={buttonClass} disabled={!e.can_apply || busy} onClick={apply}>
						{busy ? "Submitting…" : e.already_applied ? "Already applied" : "Apply"}
					</button>
					{msg ? <Flash kind={msg.kind} text={msg.text} /> : null}
				</div>
			</section>

			<DrivePrepCard driveId={driveId} />
		</div>
	)
}

export default function OpportunityPage() {
	const params = useParams<{ id: string }>()
	const driveId = Number(params.id)
	return (
		<HubFrame title="Placement drive" roles={["student"]}>
			{() =>
				Number.isInteger(driveId) && driveId > 0 ? (
					<DriveView driveId={driveId} />
				) : (
					<ErrorState message="This drive link is not valid." />
				)
			}
		</HubFrame>
	)
}
