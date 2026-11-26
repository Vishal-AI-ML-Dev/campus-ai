/**
 * Campus AI - Placement Hub: drives (TPO manages, admin reads) - Phase 9.2.
 *
 * Search, lifecycle and academic-year filters, pagination, application counts
 * per drive and "new drive" (always created as a DRAFT, invisible to students
 * until published).
 *
 * Backend: GET /placement/drives, POST /placement/drives, GET /admin/departments.
 */

"use client"

import Link from "next/link"
import { useState } from "react"
import { EmptyState, ErrorState, SkeletonList } from "@/components/ui/states"
import {
	buttonClass,
	cardClass,
	Field,
	Flash,
	HubFrame,
	inputClass,
	LifecyclePill,
	Pager,
	secondaryButtonClass,
	StaffNav,
} from "@/components/placement-hub/HubUi"
import DriveForm from "@/components/placement-hub/DriveForm"
import { errorText, placementApi } from "@/components/placement-hub/placementApi"
import {
	formatDateTime,
	LIFECYCLE_LABEL,
	LIFECYCLES,
	STATUS_LABEL,
	WINDOW_LABEL,
	type AppStatus,
} from "@/components/placement-hub/placementLogic"
import { useAsync } from "@/components/placement-hub/useAsync"
import type { Role } from "@/lib/auth"

const LIMIT = 20

function Drives({ role }: { role: Role }) {
	const [q, setQ] = useState("")
	const [query, setQuery] = useState("")
	const [lifecycle, setLifecycle] = useState("")
	const [year, setYear] = useState("")
	const [offset, setOffset] = useState(0)
	const [creating, setCreating] = useState(false)
	const [flash, setFlash] = useState<string | null>(null)
	const key = JSON.stringify({ query, lifecycle, year, offset })
	const list = useAsync(key, () =>
		placementApi.drives({ q: query, lifecycle, academic_year: year, limit: LIMIT, offset }),
	)
	const depts = useAsync(creating ? "depts" : null, () => placementApi.departments())
	const canWrite = role === "tpo"

	return (
		<div>
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<h2 className="text-2xl font-bold">Placement drives</h2>
					<p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
						Drafts are hidden from students until you publish them.
					</p>
				</div>
				{canWrite ? (
					<button type="button" className={buttonClass} aria-expanded={creating} onClick={() => setCreating((v) => !v)}>
						{creating ? "Close" : "+ New drive"}
					</button>
				) : null}
			</div>

			{flash ? (
				<div className="mt-4">
					<Flash kind="ok" text={flash} />
				</div>
			) : null}

			{creating ? (
				<section className={`${cardClass} mt-6`} aria-label="New drive">
					{depts.loading && !depts.data ? (
						<SkeletonList rows={1} />
					) : depts.error ? (
						<ErrorState message={depts.error} onRetry={depts.reload} />
					) : (
						<DriveForm
							departments={depts.data ?? []}
							submitLabel="Save as draft"
							onSubmit={async ({ fields, settings }) => {
								try {
									await placementApi.createDraft(fields, settings)
								} catch (e) {
									throw new Error(errorText(e, "Could not create the drive."))
								}
								setCreating(false)
								setFlash("Drive saved as a draft. Open it to review and publish.")
								setOffset(0)
								list.reload()
							}}
						/>
					)}
				</section>
			) : null}

			<form
				className="mt-6 grid gap-3 sm:grid-cols-4"
				role="search"
				onSubmit={(e) => {
					e.preventDefault()
					setOffset(0)
					setQuery(q.trim())
				}}
			>
				<div className="sm:col-span-2">
					<Field id="drv-q" label="Search company or role">
						<input id="drv-q" className={inputClass} value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} />
					</Field>
				</div>
				<Field id="drv-lc" label="Status">
					<select
						id="drv-lc"
						className={inputClass}
						value={lifecycle}
						onChange={(e) => {
							setOffset(0)
							setLifecycle(e.target.value)
						}}
					>
						<option value="">All</option>
						{LIFECYCLES.map((l) => (
							<option key={l} value={l}>
								{LIFECYCLE_LABEL[l]}
							</option>
						))}
					</select>
				</Field>
				<Field id="drv-year" label="Academic year">
					<input
						id="drv-year"
						className={inputClass}
						placeholder="2026-27"
						value={year}
						maxLength={9}
						onChange={(e) => {
							setOffset(0)
							setYear(e.target.value.trim())
						}}
					/>
				</Field>
				<button type="submit" className={`${secondaryButtonClass} sm:col-span-4 sm:justify-self-start`}>
					Search
				</button>
			</form>

			<div className="mt-6">
				{list.loading && !list.data ? (
					<SkeletonList rows={3} />
				) : list.error ? (
					<ErrorState message={list.error} onRetry={list.reload} requestId={list.requestId} />
				) : !list.data || list.data.items.length === 0 ? (
					<EmptyState
						title="No drives match"
						description={canWrite ? "Create a drive to get started." : "Your placement officer has not created drives yet."}
					/>
				) : (
					<>
						<ul className="space-y-3" aria-busy={list.loading}>
							{list.data.items.map((d) => (
								<li key={d.id} className={cardClass}>
									<div className="flex flex-wrap items-start justify-between gap-3">
										<div className="min-w-0">
											<div className="flex flex-wrap items-center gap-2">
												<h3 className="text-lg font-semibold">{d.company_name}</h3>
												<LifecyclePill lifecycle={d.lifecycle} />
												{!d.configured ? (
													<span className="text-xs text-slate-500" title="Created before placement management was enabled">
														legacy
													</span>
												) : null}
											</div>
											<p className="text-sm text-slate-600 dark:text-slate-300">
												{d.role_title}
												{d.location ? ` · ${d.location}` : ""}
												{d.package_lpa != null ? ` · ${d.package_lpa} LPA` : ""}
												{d.academic_year ? ` · ${d.academic_year}` : ""}
											</p>
											<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
												{WINDOW_LABEL[d.window]}
												{d.applications_close_at ? ` · closes ${formatDateTime(d.applications_close_at)}` : ""}
											</p>
										</div>
										<Link
											href={`/dashboard/placement-hub/drives/${d.id}`}
											className={secondaryButtonClass}
											aria-label={`Manage ${d.company_name} ${d.role_title}`}
										>
											Manage {"→"}
										</Link>
									</div>
									<p className="mt-3 text-xs text-slate-600 dark:text-slate-300">
										{d.applications_total} application{d.applications_total === 1 ? "" : "s"}
										{Object.entries(d.status_counts).map(([k, v]) => ` · ${STATUS_LABEL[k as AppStatus]} ${v}`)}
									</p>
								</li>
							))}
						</ul>
						<Pager offset={offset} limit={LIMIT} total={list.data.total} onChange={setOffset} />
					</>
				)}
			</div>
		</div>
	)
}

export default function PlacementDrivesPage() {
	return (
		<HubFrame title="Placement drives" roles={["tpo", "admin"]}>
			{(role) => (
				<>
					<StaffNav role={role} />
					<Drives role={role} />
				</>
			)}
		</HubFrame>
	)
}
