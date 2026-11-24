/**
 * Campus AI - Placement Hub: manage one drive (Phase 9.2).
 *
 * Lifecycle (draft -> published -> closed -> completed, or cancelled),
 * applicants (current eligibility v2, single + bulk status changes, history),
 * eligibility across all students, and the drive's details and criteria.
 * TPO writes; admin reads. The server enforces every rule again.
 *
 * Backend: GET /drives/{id}, GET|PUT /placement/drives/{id}/settings,
 * PUT /placement/drives/{id}, POST /placement/drives/{id}/lifecycle, plus the
 * applicant / eligibility / bulk routes used by the panels.
 */

"use client"

import Link from "next/link"
import { useParams } from "next/navigation"
import { useState } from "react"
import { ErrorState, LoadingState, SkeletonList } from "@/components/ui/states"
import type { Role } from "@/lib/auth"
import {
	cardClass,
	dangerButtonClass,
	Flash,
	HubFrame,
	LifecyclePill,
	secondaryButtonClass,
	StaffNav,
} from "@/components/placement-hub/HubUi"
import ApplicantsPanel from "@/components/placement-hub/ApplicantsPanel"
import EligibilityPanel from "@/components/placement-hub/EligibilityPanel"
import DriveForm from "@/components/placement-hub/DriveForm"
import { errorText, placementApi } from "@/components/placement-hub/placementApi"
import {
	DRIVE_MOVES,
	formatDateTime,
	MOVE_LABEL,
	WINDOW_LABEL,
	type Lifecycle,
} from "@/components/placement-hub/placementLogic"
import { useAsync } from "@/components/placement-hub/useAsync"

type Tab = "applicants" | "eligibility" | "details"
const TABS: { id: Tab; label: string }[] = [
	{ id: "applicants", label: "Applicants" },
	{ id: "eligibility", label: "Eligibility" },
	{ id: "details", label: "Details & criteria" },
]

function LifecycleBar({
	driveId,
	lifecycle,
	canWrite,
	onChanged,
}: {
	driveId: number
	lifecycle: Lifecycle
	canWrite: boolean
	onChanged: (msg: string) => void
}) {
	const [confirm, setConfirm] = useState<Lifecycle | null>(null)
	const [busy, setBusy] = useState(false)
	const [err, setErr] = useState<string | null>(null)
	if (!canWrite) return null
	const moves = DRIVE_MOVES[lifecycle]
	if (!moves.length)
		return <p className="text-sm text-slate-600 dark:text-slate-400">This drive is {lifecycle}; it can no longer change.</p>

	async function go(to: Lifecycle) {
		setBusy(true)
		setErr(null)
		try {
			await placementApi.lifecycle(driveId, to)
			setConfirm(null)
			onChanged(
				to === "published" && lifecycle === "draft"
					? "Published. Eligible students were notified in the app."
					: `Drive moved to ${to}.`,
			)
		} catch (e) {
			setErr(errorText(e))
		} finally {
			setBusy(false)
		}
	}

	return (
		<div className="space-y-2">
			<div className="flex flex-wrap gap-2">
				{moves.map((to) => (
					<button
						key={to}
						type="button"
						disabled={busy}
						className={to === "cancelled" ? dangerButtonClass : secondaryButtonClass}
						onClick={() => (to === "cancelled" || to === "completed" ? setConfirm(to) : go(to))}
					>
						{MOVE_LABEL[to]}
					</button>
				))}
			</div>
			{confirm ? (
				<div role="alertdialog" aria-labelledby="lc-confirm" className="rounded-lg border border-red-300 p-3 dark:border-red-500/40">
					<p id="lc-confirm" className="text-sm">
						{confirm === "cancelled"
							? "Cancelling is final: applications are frozen and the drive cannot be reopened."
							: "Completing is final: the drive cannot be reopened or edited."}
					</p>
					<div className="mt-2 flex gap-2">
						<button type="button" className={dangerButtonClass} disabled={busy} onClick={() => go(confirm)}>
							{busy ? "Saving…" : `Yes, ${MOVE_LABEL[confirm].toLowerCase()}`}
						</button>
						<button type="button" className={secondaryButtonClass} onClick={() => setConfirm(null)}>
							Keep as is
						</button>
					</div>
				</div>
			) : null}
			{err ? <Flash kind="error" text={err} /> : null}
		</div>
	)
}

function DriveManager({ driveId, role }: { driveId: number; role: Role }) {
	const canWrite = role === "tpo"
	const [tab, setTab] = useState<Tab>("applicants")
	const [flash, setFlash] = useState<string | null>(null)
	const drive = useAsync(`drive:${driveId}`, () => placementApi.drive(driveId))
	const settings = useAsync(`settings:${driveId}`, () => placementApi.settings(driveId))
	const depts = useAsync(tab === "details" ? "depts" : null, () => placementApi.departments())

	function changed(msg: string) {
		setFlash(msg)
		drive.reload()
		settings.reload()
	}

	if ((drive.loading && !drive.data) || (settings.loading && !settings.data)) return <LoadingState message="Loading drive…" />
	if (drive.error) return <ErrorState message={drive.error} onRetry={drive.reload} requestId={drive.requestId} />
	if (settings.error) return <ErrorState message={settings.error} onRetry={settings.reload} requestId={settings.requestId} />
	if (!drive.data || !settings.data) return null
	const d = drive.data
	const s = settings.data
	const editable = canWrite && s.lifecycle !== "completed" && s.lifecycle !== "cancelled"

	return (
		<div className="space-y-6">
			<Link href="/dashboard/placement-hub/drives" className="text-sm text-indigo-700 hover:underline dark:text-indigo-300">
				{"←"} All drives
			</Link>
			<section className={cardClass} aria-labelledby="drive-h">
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div className="min-w-0">
						<div className="flex flex-wrap items-center gap-2">
							<h2 id="drive-h" className="text-2xl font-bold">
								{d.company_name}
							</h2>
							<LifecyclePill lifecycle={s.lifecycle} />
						</div>
						<p className="text-slate-600 dark:text-slate-300">
							{d.role_title}
							{d.location ? ` · ${d.location}` : ""}
							{d.package_lpa != null ? ` · ${d.package_lpa} LPA` : ""}
							{s.academic_year ? ` · ${s.academic_year}` : ""}
						</p>
						<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
							{s.applications_open_at || s.applications_close_at
								? `Window: ${formatDateTime(s.applications_open_at)} → ${formatDateTime(s.applications_close_at)}`
								: WINDOW_LABEL.no_window}
						</p>
					</div>
					<LifecycleBar driveId={driveId} lifecycle={s.lifecycle} canWrite={canWrite} onChanged={changed} />
				</div>
				{flash ? (
					<div className="mt-3">
						<Flash kind="ok" text={flash} />
					</div>
				) : null}
			</section>

			<div role="tablist" aria-label="Drive sections" className="flex gap-1 overflow-x-auto relative [contain:inline-size] border-b border-slate-200 dark:border-white/10">
				{TABS.map((t) => (
					<button
						key={t.id}
						type="button"
						role="tab"
						id={`tab-${t.id}`}
						aria-selected={tab === t.id}
						aria-controls={`panel-${t.id}`}
						onClick={() => setTab(t.id)}
						onKeyDown={(e) => {
							const i = TABS.findIndex((x) => x.id === tab)
							if (e.key === "ArrowRight") setTab(TABS[(i + 1) % TABS.length].id)
							if (e.key === "ArrowLeft") setTab(TABS[(i + TABS.length - 1) % TABS.length].id)
						}}
						tabIndex={tab === t.id ? 0 : -1}
						className={`whitespace-nowrap px-3 py-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
							tab === t.id
								? "border-b-2 border-indigo-500 font-semibold text-indigo-700 dark:text-indigo-300"
								: "text-slate-600 dark:text-slate-400"
						}`}
					>
						{t.label}
					</button>
				))}
			</div>

			<div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
				{tab === "applicants" ? <ApplicantsPanel driveId={driveId} canWrite={canWrite && s.lifecycle !== "cancelled"} /> : null}
				{tab === "eligibility" ? <EligibilityPanel driveId={driveId} /> : null}
				{tab === "details" ? (
					depts.loading && !depts.data ? (
						<SkeletonList rows={2} />
					) : depts.error ? (
						<ErrorState message={depts.error} onRetry={depts.reload} />
					) : (
						<section className={cardClass}>
							{!editable ? (
								<p className="mb-4 text-sm text-slate-600 dark:text-slate-400">
									{canWrite ? "Completed and cancelled drives cannot be edited." : "Read-only view."}
								</p>
							) : null}
							<DriveForm
								key={`${d.id}-${s.lifecycle}`}
								drive={d}
								settings={s}
								departments={depts.data ?? []}
								readOnlyFields={!editable}
								submitLabel="Save changes"
								onSubmit={async ({ fields, settings: next }) => {
									if (!editable) throw new Error("This drive cannot be edited.")
									try {
										await placementApi.updateDrive(driveId, fields)
										await placementApi.saveSettings(driveId, next)
									} catch (e) {
										throw new Error(errorText(e, "Could not save the drive."))
									}
									changed("Drive saved.")
								}}
							/>
						</section>
					)
				) : null}
			</div>
		</div>
	)
}

export default function ManageDrivePage() {
	const params = useParams<{ id: string }>()
	const driveId = Number(params.id)
	return (
		<HubFrame title="Placement drive" roles={["tpo", "admin"]}>
			{(role) => (
				<>
					<StaffNav role={role} />
					{Number.isInteger(driveId) && driveId > 0 ? (
						<DriveManager driveId={driveId} role={role} />
					) : (
						<ErrorState message="This drive link is not valid." />
					)}
				</>
			)}
		</HubFrame>
	)
}
