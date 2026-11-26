/**
 * Campus AI - Placement Hub: student directory + academic profile editor
 * (Phase 9.2). TPO and admin only (the server enforces it). Students can never
 * edit these records; verified metrics (CGPA, attendance, skills, projects)
 * are read-only here and come from the verified-data system.
 *
 * Backend: GET /placement/students, GET /placement/students/{id},
 * PUT /placement/students/{id}/profile, GET /admin/departments.
 */

"use client"

import { useState } from "react"
import { EmptyState, ErrorState, LoadingState, SkeletonList } from "@/components/ui/states"
import {
	buttonClass,
	cardClass,
	Field,
	Flash,
	HubFrame,
	inputClass,
	Pager,
	secondaryButtonClass,
	StaffNav,
	StatusPill,
} from "@/components/placement-hub/HubUi"
import { errorText, placementApi, type ProfileValues, type StudentDetail } from "@/components/placement-hub/placementApi"
import {
	optionalNumber,
	PLACEMENT_STATUS_LABEL,
	type PlacementStatus,
} from "@/components/placement-hub/placementLogic"
import { useAsync } from "@/components/placement-hub/useAsync"

const LIMIT = 25

type ProfileForm = Record<
	"roll_number" | "graduation_year" | "tenth_percent" | "twelfth_percent" | "diploma_percent" | "active_backlogs" | "status_note",
	string
> & { placement_status: PlacementStatus }

function toForm(d: StudentDetail): ProfileForm {
	const p = d.profile
	const s = (v: number | string | null) => (v === null ? "" : String(v))
	return {
		roll_number: s(p.roll_number),
		graduation_year: s(p.graduation_year),
		tenth_percent: s(p.tenth_percent),
		twelfth_percent: s(p.twelfth_percent),
		diploma_percent: s(p.diploma_percent),
		active_backlogs: s(p.active_backlogs),
		status_note: s(p.status_note),
		placement_status: p.placement_status,
	}
}

function toValues(f: ProfileForm): { values?: ProfileValues; problem?: string } {
	const roll = f.roll_number.trim()
	if (roll && !/^[A-Za-z0-9/_.-]{1,40}$/.test(roll)) return { problem: "Roll number: letters, digits and / _ . - only (max 40)." }
	const checks: [keyof ProfileForm, string, number, number, boolean][] = [
		["graduation_year", "Graduation year", 2000, 2100, true],
		["tenth_percent", "10th %", 0, 100, false],
		["twelfth_percent", "12th %", 0, 100, false],
		["diploma_percent", "Diploma %", 0, 100, false],
		["active_backlogs", "Active backlogs", 0, 99, true],
	]
	const nums: Record<string, number | null> = {}
	for (const [k, label, lo, hi, int] of checks) {
		const raw = String(f[k]).trim()
		const n = optionalNumber(raw)
		if (raw && n === null) return { problem: `${label} must be a number.` }
		if (n !== null && (n < lo || n > hi || (int && !Number.isInteger(n))))
			return { problem: `${label} must be ${int ? "a whole number " : ""}between ${lo} and ${hi}.` }
		nums[k] = n
	}
	return {
		values: {
			roll_number: roll || null,
			graduation_year: nums.graduation_year,
			tenth_percent: nums.tenth_percent,
			twelfth_percent: nums.twelfth_percent,
			diploma_percent: nums.diploma_percent,
			active_backlogs: nums.active_backlogs,
			placement_status: f.placement_status,
			status_note: f.status_note.trim() || null,
		},
	}
}

function ProfileEditor({ studentId, onSaved }: { studentId: number; onSaved: () => void }) {
	const res = useAsync(`stu:${studentId}`, () => placementApi.student(studentId))
	if (res.loading && !res.data) return <LoadingState message="Loading student…" />
	if (res.error) return <ErrorState message={res.error} onRetry={res.reload} requestId={res.requestId} />
	if (!res.data) return null
	return <EditorBody key={res.data.profile.updated_at ?? "new"} detail={res.data} onSaved={() => { res.reload(); onSaved() }} />
}

function EditorBody({ detail, onSaved }: { detail: StudentDetail; onSaved: () => void }) {
	const [f, setF] = useState<ProfileForm>(() => toForm(detail))
	const [busy, setBusy] = useState(false)
	const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null)
	const set = (k: keyof ProfileForm, v: string) => setF((p) => ({ ...p, [k]: v }))
	const m = detail.metrics

	async function save(e: React.FormEvent) {
		e.preventDefault()
		const { values, problem } = toValues(f)
		if (!values) {
			setMsg({ kind: "error", text: problem ?? "Check the form." })
			return
		}
		setBusy(true)
		setMsg(null)
		try {
			await placementApi.saveProfile(detail.student_id, values)
			setMsg({ kind: "ok", text: "Profile saved." })
			onSaved()
		} catch (err) {
			setMsg({ kind: "error", text: errorText(err) })
		} finally {
			setBusy(false)
		}
	}

	const input = (k: keyof ProfileForm, label: string, type = "text", step?: string) => (
		<Field id={`pf-${k}`} label={label}>
			<input id={`pf-${k}`} type={type} step={step} className={inputClass} value={f[k]} onChange={(e) => set(k, e.target.value)} />
		</Field>
	)

	return (
		<div className="space-y-4">
			<div>
				<h4 className="font-semibold">{detail.full_name}</h4>
				<p className="text-xs text-slate-500 dark:text-slate-400">
					{detail.email} {"·"} {detail.department_name ?? "No department"} {"·"} {detail.section_name ?? "No section"}
				</p>
				<p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
					Verified: CGPA {m.cgpa} {"·"} attendance {m.attendance}% {"·"} {m.verified_projects} projects {"·"}{" "}
					{m.verified_skills.length} skills {detail.placed ? "· placed" : ""}
				</p>
			</div>
			<form onSubmit={save} className="grid gap-3 sm:grid-cols-2" noValidate>
				{input("roll_number", "Roll number")}
				{input("graduation_year", "Graduation year", "number", "1")}
				{input("tenth_percent", "10th %", "number", "0.01")}
				{input("twelfth_percent", "12th %", "number", "0.01")}
				{input("diploma_percent", "Diploma %", "number", "0.01")}
				{input("active_backlogs", "Active backlogs", "number", "1")}
				<Field id="pf-status" label="Placement status">
					<select
						id="pf-status"
						className={inputClass}
						value={f.placement_status}
						onChange={(e) => setF((p) => ({ ...p, placement_status: e.target.value as PlacementStatus }))}
					>
						{(Object.keys(PLACEMENT_STATUS_LABEL) as PlacementStatus[]).map((s) => (
							<option key={s} value={s}>
								{PLACEMENT_STATUS_LABEL[s]}
							</option>
						))}
					</select>
				</Field>
				{input("status_note", "Status note (internal)")}
				<div className="sm:col-span-2">
					{msg ? <Flash kind={msg.kind} text={msg.text} /> : null}
					<button type="submit" className={`${buttonClass} mt-2`} disabled={busy}>
						{busy ? "Saving…" : "Save profile"}
					</button>
				</div>
			</form>
			{detail.applications.length ? (
				<div>
					<h5 className="text-sm font-semibold">Applications</h5>
					<ul className="mt-1 space-y-1 text-sm">
						{detail.applications.map((a) => (
							<li key={a.application_id} className="flex flex-wrap items-center gap-2">
								<StatusPill status={a.status} /> {a.company_name} {"—"} {a.role_title}
							</li>
						))}
					</ul>
				</div>
			) : null}
		</div>
	)
}

function Directory() {
	const [q, setQ] = useState("")
	const [filters, setFilters] = useState({ q: "", department_id: "", graduation_year: "", placement_status: "", placed: "" })
	const [offset, setOffset] = useState(0)
	const [editing, setEditing] = useState<number | null>(null)
	const list = useAsync(JSON.stringify({ filters, offset }), () =>
		placementApi.students({ ...filters, limit: LIMIT, offset }),
	)
	const depts = useAsync("depts", () => placementApi.departments())
	const setFilter = (k: keyof typeof filters, v: string) => {
		setOffset(0)
		setFilters((p) => ({ ...p, [k]: v }))
	}

	return (
		<div>
			<h2 className="text-2xl font-bold">Students</h2>
			<p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
				Academic details used for placement eligibility. Only the placement office can change them.
			</p>
			<form
				role="search"
				className="mt-4 grid gap-3 sm:grid-cols-3 lg:grid-cols-6"
				onSubmit={(e) => {
					e.preventDefault()
					setFilter("q", q.trim())
				}}
			>
				<div className="sm:col-span-3 lg:col-span-2">
					<Field id="st-q" label="Search name, email or roll number">
						<input id="st-q" className={inputClass} maxLength={100} value={q} onChange={(e) => setQ(e.target.value)} />
					</Field>
				</div>
				<Field id="st-dept" label="Department">
					<select id="st-dept" className={inputClass} value={filters.department_id} onChange={(e) => setFilter("department_id", e.target.value)}>
						<option value="">All</option>
						{(depts.data ?? []).map((d) => (
							<option key={d.id} value={d.id}>
								{d.name}
							</option>
						))}
					</select>
				</Field>
				<Field id="st-year" label="Graduation year">
					<input
						id="st-year"
						className={inputClass}
						inputMode="numeric"
						maxLength={4}
						value={filters.graduation_year}
						onChange={(e) => {
							const v = e.target.value.replace(/\D/g, "")
							if (v === "" || v.length === 4) setFilter("graduation_year", v)
						}}
					/>
				</Field>
				<Field id="st-status" label="Placement status">
					<select id="st-status" className={inputClass} value={filters.placement_status} onChange={(e) => setFilter("placement_status", e.target.value)}>
						<option value="">All</option>
						{(Object.keys(PLACEMENT_STATUS_LABEL) as PlacementStatus[]).map((s) => (
							<option key={s} value={s}>
								{PLACEMENT_STATUS_LABEL[s]}
							</option>
						))}
					</select>
				</Field>
				<Field id="st-placed" label="Placed">
					<select id="st-placed" className={inputClass} value={filters.placed} onChange={(e) => setFilter("placed", e.target.value)}>
						<option value="">All</option>
						<option value="true">Placed</option>
						<option value="false">Not placed</option>
					</select>
				</Field>
				<button type="submit" className={`${secondaryButtonClass} sm:justify-self-start`}>
					Search
				</button>
			</form>

			<div className="mt-6 grid gap-6 lg:grid-cols-5">
				<div className="lg:col-span-3">
					{list.loading && !list.data ? (
						<SkeletonList rows={4} />
					) : list.error ? (
						<ErrorState message={list.error} onRetry={list.reload} requestId={list.requestId} />
					) : !list.data || list.data.items.length === 0 ? (
						<EmptyState title="No students match" />
					) : (
						<>
							<ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 dark:divide-white/10 dark:border-white/10">
								{list.data.items.map((s) => (
									<li key={s.student_id} className="flex flex-wrap items-center justify-between gap-2 p-3">
										<div className="min-w-0">
											<p className="font-medium">{s.full_name}</p>
											<p className="text-xs text-slate-500 dark:text-slate-400">
												{[s.roll_number, s.department_name, s.graduation_year].filter(Boolean).join(" · ") || "No academic record yet"}
												{" · "}
												{PLACEMENT_STATUS_LABEL[s.placement_status]}
												{s.placed ? " · placed" : ""}
											</p>
										</div>
										<button
											type="button"
											className={secondaryButtonClass}
											aria-pressed={editing === s.student_id}
											onClick={() => setEditing(s.student_id)}
										>
											{s.profile_recorded ? "Edit" : "Add details"}
										</button>
									</li>
								))}
							</ul>
							<Pager offset={offset} limit={LIMIT} total={list.data.total} onChange={setOffset} />
						</>
					)}
				</div>
				<aside className={`${cardClass} lg:col-span-2`} aria-label="Student academic profile">
					{editing === null ? (
						<p className="text-sm text-slate-600 dark:text-slate-400">Choose a student to view or edit their academic details.</p>
					) : (
						<ProfileEditor studentId={editing} onSaved={list.reload} />
					)}
					<p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
						Every change is recorded in the audit log (field names only, never the values).
					</p>
				</aside>
			</div>
		</div>
	)
}

export default function PlacementStudentsPage() {
	return (
		<HubFrame title="Students" roles={["tpo", "admin"]}>
			{(role) => (
				<>
					<StaffNav role={role} />
					<Directory />
				</>
			)}
		</HubFrame>
	)
}
