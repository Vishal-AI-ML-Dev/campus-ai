"use client"

/**
 * Student Placement Hub (Phase 9.2): own academic profile (read-only - the
 * placement office maintains it), placement status, opportunities with an
 * eligibility summary, applications with history and withdrawal (P9-D8).
 */

import Link from "next/link"
import { useState } from "react"
import { EmptyState, ErrorState, LoadingState, SkeletonList } from "@/components/ui/states"
import { errorText, placementApi, type HistoryEvent, type MyApplication } from "./placementApi"
import {
	cardClass,
	dangerButtonClass,
	Flash,
	LifecyclePill,
	secondaryButtonClass,
	StatusPill,
	YesNo,
} from "./HubUi"
import { formatDate, formatDateTime, percent, PLACEMENT_STATUS_LABEL, STATUS_LABEL, WINDOW_LABEL } from "./placementLogic"
import { useAsync } from "./useAsync"
import StudentPrepPanel from "./StudentPrepPanel"
import StudentCampaigns from "./StudentCampaigns"

function ProfileCard() {
	const me = useAsync("me", () => placementApi.myProfile())
	if (me.loading && !me.data) return <SkeletonList rows={1} />
	if (me.error) return <ErrorState message={me.error} onRetry={me.reload} requestId={me.requestId} />
	if (!me.data) return null
	const { profile, metrics, department_name, placed } = me.data
	const items: [string, string][] = [
		["Roll number", profile.roll_number ?? "—"],
		["Department", department_name ?? "—"],
		["Graduation year", profile.graduation_year ? String(profile.graduation_year) : "—"],
		["10th", percent(profile.tenth_percent)],
		["12th", percent(profile.twelfth_percent)],
		["Diploma", percent(profile.diploma_percent)],
		["Active backlogs", profile.active_backlogs === null ? "—" : String(profile.active_backlogs)],
		["CGPA (verified)", String(metrics.cgpa)],
		["Attendance (verified)", `${metrics.attendance}%`],
		["Verified projects", String(metrics.verified_projects)],
	]
	return (
		<section aria-labelledby="profile-h" className={cardClass}>
			<div className="flex flex-wrap items-center justify-between gap-2">
				<h3 id="profile-h" className="text-lg font-semibold">
					My placement profile
				</h3>
				<div className="flex flex-wrap gap-2 text-xs">
					<span className="rounded-full bg-slate-500/15 px-2 py-0.5">
						Status: {PLACEMENT_STATUS_LABEL[profile.placement_status]}
					</span>
					{placed ? (
						<span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-emerald-800 dark:text-emerald-200">
							Placed
						</span>
					) : null}
				</div>
			</div>
			{!profile.recorded ? (
				<p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
					Your placement office has not recorded your academic details yet.
				</p>
			) : null}
			<dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-5">
				{items.map(([k, v]) => (
					<div key={k}>
						<dt className="text-xs text-slate-500 dark:text-slate-400">{k}</dt>
						<dd className="font-medium">{v}</dd>
					</div>
				))}
			</dl>
			<p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
				Academic details are entered by your placement office and cannot be edited here. Contact them if
				something is wrong. Verified skills: {metrics.verified_skills.length ? metrics.verified_skills.join(", ") : "none yet"}.
			</p>
		</section>
	)
}

function Opportunities() {
	const [scope, setScope] = useState<"open" | "all">("open")
	const res = useAsync(`drives:${scope}`, () => placementApi.myDrives(scope))
	return (
		<section aria-labelledby="opp-h" className="mt-6">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<h3 id="opp-h" className="text-lg font-semibold">
					Opportunities
				</h3>
				<div role="group" aria-label="Which drives to show" className="flex gap-1 text-sm">
					{(["open", "all"] as const).map((s) => (
						<button
							key={s}
							type="button"
							aria-pressed={scope === s}
							onClick={() => setScope(s)}
							className={`${secondaryButtonClass} ${scope === s ? "bg-slate-100 font-semibold dark:bg-white/10" : ""}`}
						>
							{s === "open" ? "Open now" : "All drives"}
						</button>
					))}
				</div>
			</div>
			<div className="mt-3">
				{res.loading && !res.data ? (
					<SkeletonList rows={2} />
				) : res.error ? (
					<ErrorState message={res.error} onRetry={res.reload} requestId={res.requestId} />
				) : !res.data || res.data.items.length === 0 ? (
					<EmptyState
						title={scope === "open" ? "No drives are open right now" : "No drives yet"}
						description="New drives appear here when your placement office publishes them."
					/>
				) : (
					<ul className="grid gap-3 md:grid-cols-2">
						{res.data.items.map((d) => (
							<li key={d.id} className={cardClass}>
								<div className="flex flex-wrap items-start justify-between gap-2">
									<div className="min-w-0">
										<h4 className="truncate font-semibold">{d.company_name}</h4>
										<p className="text-sm text-slate-600 dark:text-slate-300">
											{d.role_title}
											{d.location ? ` · ${d.location}` : ""}
											{d.package_lpa != null ? ` · ${d.package_lpa} LPA` : ""}
										</p>
									</div>
									<LifecyclePill lifecycle={d.lifecycle} />
								</div>
								<div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
									<YesNo ok={d.eligible} />
									<span className="text-slate-600 dark:text-slate-400">{WINDOW_LABEL[d.window]}</span>
									{d.applications_close_at ? (
										<span className="text-slate-600 dark:text-slate-400">
											Closes {formatDateTime(d.applications_close_at)}
										</span>
									) : null}
									{d.application_status ? <StatusPill status={d.application_status} /> : null}
								</div>
								{!d.eligible && d.failed_criteria.length ? (
									<p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
										Not met: {d.failed_criteria.join(", ")}
									</p>
								) : null}
								<Link
									href={`/dashboard/placement-hub/opportunities/${d.id}`}
									className={`${secondaryButtonClass} mt-3 inline-block`}
									aria-label={`View ${d.company_name} ${d.role_title}${d.can_apply ? " and apply" : ""}`}
								>
									{d.can_apply ? "View and apply" : "View details"}
								</Link>
							</li>
						))}
					</ul>
				)}
			</div>
		</section>
	)
}

function HistoryList({ applicationId }: { applicationId: number }) {
	const res = useAsync(`h:${applicationId}`, () => placementApi.history(applicationId))
	if (res.loading && !res.data) return <LoadingState message="Loading history…" />
	if (res.error) return <ErrorState message={res.error} onRetry={res.reload} />
	const events: HistoryEvent[] = res.data ?? []
	if (!events.length)
		return <p className="text-xs text-slate-500">No recorded history yet (history starts with the next change).</p>
	return (
		<ol className="space-y-1 text-xs">
			{events.map((e) => (
				<li key={e.id}>
					<span className="font-medium">{formatDateTime(e.created_at)}</span>
					{" — "}
					{e.from_status ? `${STATUS_LABEL[e.from_status]} → ` : ""}
					{STATUS_LABEL[e.to_status]}
					{e.note ? ` (${e.note})` : ""}
				</li>
			))}
		</ol>
	)
}

function ApplicationRow({ a, onChanged }: { a: MyApplication; onChanged: () => void }) {
	const [open, setOpen] = useState(false)
	const [confirming, setConfirming] = useState(false)
	const [busy, setBusy] = useState(false)
	const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null)

	async function withdraw() {
		setBusy(true)
		setMsg(null)
		try {
			await placementApi.withdraw(a.application_id, a.status)
			setMsg({ kind: "ok", text: "Your application was withdrawn." })
			setConfirming(false)
			onChanged()
		} catch (e) {
			setMsg({ kind: "error", text: errorText(e) })
		} finally {
			setBusy(false)
		}
	}

	return (
		<li className={cardClass}>
			<div className="flex flex-wrap items-start justify-between gap-2">
				<div className="min-w-0">
					<h4 className="font-semibold">{a.company_name}</h4>
					<p className="text-sm text-slate-600 dark:text-slate-300">{a.role_title}</p>
					<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
						Applied {formatDate(a.applied_at)}
						{a.offer_status ? ` · Offer: ${a.offer_status}` : ""}
					</p>
				</div>
				<StatusPill status={a.status} />
			</div>
			{a.note ? <p className="mt-2 text-sm">Note from the placement office: {a.note}</p> : null}
			<div className="mt-3 flex flex-wrap gap-2">
				<button
					type="button"
					className={secondaryButtonClass}
					aria-expanded={open}
					aria-controls={`hist-${a.application_id}`}
					onClick={() => setOpen((v) => !v)}
				>
					{open ? "Hide history" : "Show history"}
				</button>
				{a.can_withdraw && !confirming ? (
					<button type="button" className={dangerButtonClass} onClick={() => setConfirming(true)}>
						Withdraw
					</button>
				) : null}
			</div>
			{!a.can_withdraw && a.withdraw_blocker && a.status !== "withdrawn" ? (
				<p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{a.withdraw_blocker}</p>
			) : null}
			{confirming ? (
				<div
					role="alertdialog"
					aria-labelledby={`wd-${a.application_id}`}
					className="mt-3 rounded-lg border border-red-300 p-3 dark:border-red-500/40"
				>
					<p id={`wd-${a.application_id}`} className="text-sm">
						Withdrawing is final. You cannot apply to this drive again. Continue?
					</p>
					<div className="mt-2 flex gap-2">
						<button type="button" className={dangerButtonClass} disabled={busy} onClick={withdraw}>
							{busy ? "Withdrawing…" : "Yes, withdraw"}
						</button>
						<button type="button" className={secondaryButtonClass} onClick={() => setConfirming(false)}>
							Keep application
						</button>
					</div>
				</div>
			) : null}
			{msg ? (
				<div className="mt-2">
					<Flash kind={msg.kind} text={msg.text} />
				</div>
			) : null}
			{open ? (
				<div id={`hist-${a.application_id}`} className="mt-3">
					<HistoryList applicationId={a.application_id} />
				</div>
			) : null}
		</li>
	)
}

function Applications() {
	const res = useAsync("apps", () => placementApi.myApplications())
	return (
		<section aria-labelledby="apps-h" className="mt-6">
			<h3 id="apps-h" className="text-lg font-semibold">
				My applications
			</h3>
			<div className="mt-3">
				{res.loading && !res.data ? (
					<SkeletonList rows={2} />
				) : res.error ? (
					<ErrorState message={res.error} onRetry={res.reload} requestId={res.requestId} />
				) : !res.data || res.data.items.length === 0 ? (
					<EmptyState title="No applications yet" description="Apply to an open drive to see it here." />
				) : (
					<ul className="grid gap-3 md:grid-cols-2">
						{res.data.items.map((a) => (
							<ApplicationRow key={a.application_id} a={a} onChanged={res.reload} />
						))}
					</ul>
				)}
			</div>
		</section>
	)
}

export default function StudentHub() {
	return (
		<div>
			<h2 className="text-2xl font-bold">Placement Hub</h2>
			<p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
				Your placement profile, open drives, applications and interview preparation.
			</p>
			<div className="mt-6">
				<ProfileCard />
			</div>
			<StudentCampaigns />
			<Opportunities />
			<Applications />
			<StudentPrepPanel />
		</div>
	)
}
