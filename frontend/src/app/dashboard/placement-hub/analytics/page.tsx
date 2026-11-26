/**
 * Campus AI - Placement Hub: placement analytics + CSV export (Phase 9.4).
 *
 * Filters: academic year, department, graduation year, company, drive and
 * application status. Every number comes from GET /placement/analytics, whose
 * metric definitions are documented in backend/app/services/placement_insights.py
 * (students are counted once, however many applications they have).
 * Exports (TPO/admin) are audited on the server and formula-injection safe.
 */

"use client"

import { useState } from "react"
import { EmptyState, ErrorState, SkeletonList } from "@/components/ui/states"
import {
	cardClass,
	Field,
	Flash,
	HubFrame,
	inputClass,
	secondaryButtonClass,
	StaffNav,
} from "@/components/placement-hub/HubUi"
import {
	downloadBlob,
	errorText,
	EXPORT_KINDS,
	insightsApi,
	type AnalyticsFilters,
	type ExportKind,
} from "@/components/placement-hub/placementApi"
import { APP_STATUSES, LIFECYCLE_LABEL, STATUS_LABEL } from "@/components/placement-hub/placementLogic"
import { useAsync } from "@/components/placement-hub/useAsync"

const EXPORT_LABEL: Record<ExportKind, string> = {
	summary: "Summary",
	companies: "Companies",
	departments: "Departments",
	drives: "Drives",
	applications: "Applications (student list)",
}

function Num({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
	return (
		<div className={cardClass}>
			<p className="text-xs text-slate-600 dark:text-slate-400">{label}</p>
			<p className="mt-1 text-2xl font-bold tabular-nums">{value}</p>
			{hint ? <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p> : null}
		</div>
	)
}

function Table({ caption, head, rows }: { caption: string; head: string[]; rows: (string | number | null)[][] }) {
	if (!rows.length) return <EmptyState title={`No ${caption.toLowerCase()} for these filters`} />
	return (
		<div className="overflow-x-auto relative [contain:inline-size] rounded-xl border border-slate-200 dark:border-white/10">
			<table className="min-w-full text-left text-sm">
				<caption className="sr-only">{caption}</caption>
				<thead className="bg-slate-50 text-xs uppercase text-slate-600 dark:bg-white/5 dark:text-slate-300">
					<tr>
						{head.map((h, i) => (
							<th key={h} scope="col" className={`px-3 py-2 ${i ? "text-right" : ""}`}>
								{h}
							</th>
						))}
					</tr>
				</thead>
				<tbody className="divide-y divide-slate-200 dark:divide-white/10">
					{rows.map((r, i) => (
						<tr key={i}>
							{r.map((c, j) =>
								j === 0 ? (
									<th key={j} scope="row" className="px-3 py-2 font-medium">
										{c ?? "—"}
									</th>
								) : (
									<td key={j} className="px-3 py-2 text-right tabular-nums">
										{c ?? "—"}
									</td>
								),
							)}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	)
}

function Analytics() {
	const [f, setF] = useState<AnalyticsFilters>({})
	const [busy, setBusy] = useState<ExportKind | null>(null)
	const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null)
	const opts = useAsync("an-opts", () => insightsApi.analyticsOptions())
	const res = useAsync(JSON.stringify(f), () => insightsApi.analytics(f))
	const set = (k: keyof AnalyticsFilters, v: string) =>
		setF((p) => {
			const next = { ...p }
			if (v) next[k] = v
			else delete next[k]
			return next
		})

	async function exportCsv(kind: ExportKind) {
		setBusy(kind)
		setMsg(null)
		try {
			downloadBlob(await insightsApi.exportCsv(kind, f), `placement_${kind}.csv`)
			setMsg({ kind: "ok", text: `${EXPORT_LABEL[kind]} export downloaded. Exports are recorded in the audit log.` })
		} catch (e) {
			setMsg({ kind: "error", text: errorText(e) })
		} finally {
			setBusy(null)
		}
	}

	const o = opts.data
	const a = res.data
	const s = a?.summary
	return (
		<div className="space-y-6">
			<div>
				<h2 className="text-2xl font-bold">Placement analytics</h2>
				<p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
					Students are counted once however many applications they have. Placed = selected or accepted an offer.
				</p>
			</div>

			<form className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6" onSubmit={(e) => e.preventDefault()} aria-label="Analytics filters">
				<Field id="an-year" label="Academic year">
					<select id="an-year" className={inputClass} value={f.academic_year ?? ""} onChange={(e) => set("academic_year", e.target.value)}>
						<option value="">All</option>
						{(o?.academic_years ?? []).map((y) => (
							<option key={y} value={y}>
								{y}
							</option>
						))}
					</select>
				</Field>
				<Field id="an-dept" label="Department">
					<select id="an-dept" className={inputClass} value={f.department_id ?? ""} onChange={(e) => set("department_id", e.target.value)}>
						<option value="">All</option>
						{(o?.departments ?? []).map((d) => (
							<option key={d.id} value={d.id}>
								{d.name}
							</option>
						))}
					</select>
				</Field>
				<Field id="an-grad" label="Graduation year">
					<input
						id="an-grad"
						className={inputClass}
						inputMode="numeric"
						maxLength={4}
						value={f.graduation_year ?? ""}
						onChange={(e) => {
							const v = e.target.value.replace(/\D/g, "")
							if (v === "" || v.length === 4) set("graduation_year", v)
						}}
					/>
				</Field>
				<Field id="an-company" label="Company">
					<select id="an-company" className={inputClass} value={f.company ?? ""} onChange={(e) => set("company", e.target.value)}>
						<option value="">All</option>
						{(o?.companies ?? []).map((c) => (
							<option key={c} value={c}>
								{c}
							</option>
						))}
					</select>
				</Field>
				<Field id="an-drive" label="Drive">
					<select id="an-drive" className={inputClass} value={f.drive_id ?? ""} onChange={(e) => set("drive_id", e.target.value)}>
						<option value="">All</option>
						{(o?.drives ?? []).map((d) => (
							<option key={d.id} value={d.id}>
								{d.label}
							</option>
						))}
					</select>
				</Field>
				<Field id="an-status" label="Application status">
					<select id="an-status" className={inputClass} value={f.status ?? ""} onChange={(e) => set("status", e.target.value)}>
						<option value="">All</option>
						{APP_STATUSES.map((st) => (
							<option key={st} value={st}>
								{STATUS_LABEL[st]}
							</option>
						))}
					</select>
				</Field>
			</form>

			{res.loading && !a ? (
				<SkeletonList rows={3} />
			) : res.error ? (
				<ErrorState message={res.error} onRetry={res.reload} requestId={res.requestId} />
			) : !a || !s ? null : (
				<div className="space-y-6" aria-busy={res.loading}>
					<section aria-label="Summary" className="grid grid-cols-2 gap-3 md:grid-cols-5">
						<Num label="Registered students" value={s.registered_students} />
						<Num label="Eligible students" value={s.eligible_students} hint={s.eligible_basis} />
						<Num label="Applications" value={s.applications} hint={`${s.applicants} students applied`} />
						<Num label="Shortlisted students" value={s.shortlisted_students} />
						<Num label="Selected students" value={s.selected_students} />
						<Num label="Offers" value={s.offers} hint={`${s.offers_by_status.accepted ?? 0} accepted`} />
						<Num label="Placed students" value={s.placed_students} />
						<Num
							label="Placement rate"
							value={s.placement_percentage === null ? "—" : `${s.placement_percentage}%`}
							hint={`placed ÷ ${s.placement_denominator} active or placed students`}
						/>
						<Num label="Drives" value={s.drives} />
					</section>

					<section aria-labelledby="an-co">
						<h3 id="an-co" className="mb-2 font-semibold">
							By company
						</h3>
						<Table
							caption="Company-wise results"
							head={["Company", "Drives", "Applications", "Applicants", "Shortlisted", "Selected", "Offers accepted", "Placed"]}
							rows={a.companies.map((c) => [c.company, c.drives, c.applications, c.applicants, c.shortlisted_students, c.selected_students, c.offers_accepted, c.placed_students])}
						/>
					</section>
					<section aria-labelledby="an-de">
						<h3 id="an-de" className="mb-2 font-semibold">
							By department
						</h3>
						<Table
							caption="Department-wise results"
							head={["Department", "Registered", "Applicants", "Selected", "Placed", "Placement rate"]}
							rows={a.departments.map((d) => [d.department, d.registered_students, d.applicants, d.selected_students, d.placed_students, d.placement_percentage === null ? null : `${d.placement_percentage}%`])}
						/>
					</section>
					<section aria-labelledby="an-dr">
						<h3 id="an-dr" className="mb-2 font-semibold">
							By drive
						</h3>
						<Table
							caption="Drive-wise results"
							head={["Drive", "Status", "Applications", ...APP_STATUSES.map((x) => STATUS_LABEL[x]), "Placed"]}
							rows={a.drives.map((d) => [`${d.company} — ${d.role_title}`, LIFECYCLE_LABEL[d.lifecycle], d.applications, ...APP_STATUSES.map((x) => d.by_status[x]), d.placed_students])}
						/>
					</section>
				</div>
			)}

			<section className={cardClass} aria-labelledby="an-ex">
				<h3 id="an-ex" className="font-semibold">
					Export CSV
				</h3>
				<p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
					Uses the filters above. The student list contains names, roll numbers, departments and application statuses
					only (no contact details or marks).
				</p>
				<div className="mt-3 flex flex-wrap gap-2">
					{EXPORT_KINDS.map((k) => (
						<button key={k} type="button" className={secondaryButtonClass} disabled={busy !== null} onClick={() => exportCsv(k)}>
							{busy === k ? "Preparing…" : EXPORT_LABEL[k]}
						</button>
					))}
				</div>
				{msg ? (
					<div className="mt-3">
						<Flash kind={msg.kind} text={msg.text} />
					</div>
				) : null}
			</section>
		</div>
	)
}

export default function PlacementAnalyticsV2Page() {
	return (
		<HubFrame title="Placement analytics" roles={["tpo", "admin"]}>
			{(role) => (
				<>
					<StaffNav role={role} />
					<Analytics />
				</>
			)}
		</HubFrame>
	)
}
