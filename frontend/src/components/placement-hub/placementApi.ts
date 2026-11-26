/**
 * Placement Hub (Phase 9.2-9.4) - build flag, gate, types and API calls.
 *
 * Backend: /placement/* (backend/app/routers/placement_management.py and
 * placement_campaigns.py). Every route answers 404 unless the institute is
 * enabled (PLACEMENT_MANAGEMENT_ENABLED + PLACEMENT_MANAGEMENT_TENANT_IDS),
 * so a 404 from the /placement/status probe means "not enabled here".
 *
 * Build flag: NEXT_PUBLIC_PLACEMENT_MANAGEMENT_ENABLED === "true" (default OFF).
 * With the flag off no request is made, no nav entry appears and the existing
 * /dashboard/drives, /applications and /placements pages are unchanged.
 */

import { useCallback, useEffect, useState } from "react"
import { api, ApiError } from "@/lib/api"
import type { NavItem, Role } from "@/lib/auth"
import type { AppStatus, BulkTarget, Lifecycle, PlacementStatus, WindowState } from "./placementLogic"

export const PLACEMENT_HUB_BUILD_ENABLED = process.env.NEXT_PUBLIC_PLACEMENT_MANAGEMENT_ENABLED === "true"

export const HUB_ROLES: Role[] = ["student", "tpo", "admin", "teacher"]

export const PLACEMENT_HUB_NAV_ITEM: NavItem = {
	label: "Placement Hub",
	href: "/dashboard/placement-hub",
	icon: "\u{1F9ED}",
}

export type Gate =
	| { state: "off" }
	| { state: "loading" }
	| { state: "role" }
	| { state: "disabled" }
	| { state: "error"; message: string; requestId?: string; retry: () => void }
	| { state: "ready"; role: Role }

/** One probe per page: is the Placement Hub on for this user's institute? */
export function usePlacementGate(role: Role | undefined): Gate {
	const allowedRole = role !== undefined && HUB_ROLES.includes(role)
	const ask = PLACEMENT_HUB_BUILD_ENABLED && allowedRole
	const [result, setResult] = useState<{ status: number; message?: string; requestId?: string } | null>(null)
	const [tick, setTick] = useState(0)
	const retry = useCallback(() => setTick((t) => t + 1), [])
	useEffect(() => {
		if (!ask) return
		let live = true
		setResult(null)
		api
			.get<{ enabled: boolean }>("/placement/status")
			.then(() => live && setResult({ status: 200 }))
			.catch((e) => {
				if (!live) return
				if (e instanceof ApiError) setResult({ status: e.status, message: e.message, requestId: e.requestId })
				else setResult({ status: 0, message: "Could not reach the server." })
			})
		return () => {
			live = false
		}
	}, [ask, tick])
	if (!PLACEMENT_HUB_BUILD_ENABLED) return { state: "off" }
	if (role === undefined) return { state: "loading" }
	if (!allowedRole) return { state: "role" }
	if (result === null) return { state: "loading" }
	if (result.status === 200) return { state: "ready", role }
	if (result.status === 404) return { state: "disabled" }
	if (result.status === 403) return { state: "role" }
	return { state: "error", message: result.message ?? "Something went wrong.", requestId: result.requestId, retry }
}

/** Sidebar entry, or null. No request at all unless the build flag is on. */
export function usePlacementHubNavItem(role: Role | undefined): NavItem | null {
	const gate = usePlacementGate(role)
	return gate.state === "ready" ? PLACEMENT_HUB_NAV_ITEM : null
}

// --- Types (mirror backend/app/schemas/placement_management.py) -------------------
export type Reason = { criterion: string; passed: boolean; detail: string }

export type DriveCore = {
	id: number
	company_name: string
	role_title: string
	description: string | null
	location: string | null
	package_lpa: number | null
	min_cgpa: number
	min_attendance: number
	min_verified_projects: number
	required_skills: string | null
	is_open: boolean
	deadline: string | null
}

export type DriveSettings = {
	drive_id: number
	configured: boolean
	lifecycle: Lifecycle
	is_open: boolean
	applications_open_at: string | null
	applications_close_at: string | null
	academic_year: string | null
	graduation_years: number[]
	department_ids: number[]
	min_tenth_percent: number | null
	min_twelfth_percent: number | null
	max_active_backlogs: number | null
	exclude_placed: boolean
}

export type SettingsInput = Omit<DriveSettings, "drive_id" | "configured" | "lifecycle" | "is_open">

export type DriveFields = {
	company_name: string
	role_title: string
	description: string | null
	location: string | null
	package_lpa: number | null
	min_cgpa: number
	min_attendance: number
	min_verified_projects: number
	required_skills: string | null
	deadline: string | null
}

export type StaffDrive = {
	id: number
	company_name: string
	role_title: string
	location: string | null
	package_lpa: number | null
	is_open: boolean
	deadline: string | null
	created_at: string
	lifecycle: Lifecycle
	configured: boolean
	academic_year: string | null
	applications_open_at: string | null
	applications_close_at: string | null
	window: WindowState
	applications_total: number
	status_counts: Partial<Record<AppStatus, number>>
}

export type Applicant = {
	application_id: number
	student_id: number
	full_name: string
	roll_number: string | null
	department_name: string | null
	graduation_year: number | null
	status: AppStatus
	eligible: boolean
	failed_criteria: string[]
	cgpa: number
	attendance: number
	verified_skills: number
	verified_projects: number
	placed: boolean
	note: string | null
	offer_status: string | null
	contact_revealed: boolean
	applied_at: string
	decided_at: string | null
}

export type EligibilityRow = {
	student_id: number
	full_name: string
	department_name: string | null
	graduation_year: number | null
	eligible: boolean
	cgpa: number
	attendance: number
	verified_skills: number
	verified_projects: number
	placed: boolean
	placement_status: PlacementStatus
	applied: boolean
	reasons: Reason[]
}

export type HistoryEvent = {
	id: number
	from_status: AppStatus | null
	to_status: AppStatus
	actor_role: string | null
	note: string | null
	created_at: string
}

export type BulkResult = {
	application_id: number
	ok: boolean
	from_status: AppStatus | null
	to_status: AppStatus
	error_code: string | null
	detail: string | null
}

export type BulkResponse = {
	dry_run: boolean
	to_status: AppStatus
	succeeded: number
	failed: number
	results: BulkResult[]
}

export type ProfileValues = {
	roll_number: string | null
	graduation_year: number | null
	tenth_percent: number | null
	twelfth_percent: number | null
	diploma_percent: number | null
	active_backlogs: number | null
	placement_status: PlacementStatus
	status_note: string | null
}

export type StudentProfile = ProfileValues & { recorded: boolean; updated_at: string | null }

export type Metrics = { cgpa: number; attendance: number; verified_skills: string[]; verified_projects: number }

export type StudentListItem = {
	student_id: number
	full_name: string
	email: string
	section_id: number | null
	section_name: string | null
	department_id: number | null
	department_name: string | null
	roll_number: string | null
	graduation_year: number | null
	placement_status: PlacementStatus
	profile_recorded: boolean
	placed: boolean
}

export type StudentDetail = StudentListItem & {
	profile: StudentProfile
	metrics: Metrics
	applications: {
		application_id: number
		drive_id: number
		company_name: string
		role_title: string
		status: AppStatus
		created_at: string
	}[]
}

export type MyProfile = {
	profile: StudentProfile
	metrics: Metrics
	department_id: number | null
	department_name: string | null
	placed: boolean
}

export type StudentDrive = {
	id: number
	company_name: string
	role_title: string
	description: string | null
	location: string | null
	package_lpa: number | null
	required_skills: string | null
	lifecycle: Lifecycle
	window: WindowState
	applications_open_at: string | null
	applications_close_at: string | null
	eligible: boolean
	failed_criteria: string[]
	already_applied: boolean
	application_status: AppStatus | null
	can_apply: boolean
}

export type MyEligibility = {
	drive_id: number
	eligible: boolean
	lifecycle: Lifecycle
	window: WindowState
	applications_open_at: string | null
	applications_close_at: string | null
	already_applied: boolean
	can_apply: boolean
	blockers: string[]
	cgpa: number
	attendance: number
	verified_skills: number
	verified_projects: number
	reasons: Reason[]
}

export type MyApplication = {
	application_id: number
	drive_id: number
	company_name: string
	role_title: string
	lifecycle: Lifecycle
	status: AppStatus
	note: string | null
	offer_status: string | null
	applied_at: string
	decided_at: string | null
	can_withdraw: boolean
	withdraw_blocker: string | null
}

export type ImportRow = {
	row: number
	action: "create" | "update" | "unchanged" | "error"
	student_id: number | null
	student_name: string | null
	email: string | null
	roll_number: string | null
	values: Record<string, string | number | null>
	changed_fields: string[]
	errors: string[]
}

export type ImportPreview = {
	file_sha256: string
	file_type: "csv" | "xlsx"
	total_rows: number
	valid_rows: number
	invalid_rows: number
	to_create: number
	to_update: number
	unchanged: number
	ignored_columns: string[]
	rows: ImportRow[]
}

export type ImportSummary = {
	file_sha256: string
	total_rows: number
	created: number
	updated: number
	unchanged: number
	invalid: number
}

export type Department = { id: number; name: string; code?: string }

export type Paged<T> = { items: T[]; total: number; headers: Headers }

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
	const sp = new URLSearchParams()
	for (const [k, v] of Object.entries(params)) {
		if (v === null || v === undefined || v === "") continue
		sp.set(k, String(v))
	}
	const s = sp.toString()
	return s ? `?${s}` : ""
}

async function paged<T>(path: string): Promise<Paged<T>> {
	const { data, headers } = await api.getWithHeaders<T[]>(path)
	const total = Number(headers.get("X-Total-Count") ?? data.length)
	return { items: data, total: Number.isFinite(total) ? total : data.length, headers }
}

export const placementApi = {
	qs,
	// staff: drives
	drives: (p: { lifecycle?: string; academic_year?: string; q?: string; limit?: number; offset?: number }) =>
		paged<StaffDrive>(`/placement/drives${qs(p)}`),
	drive: (id: number) => api.get<DriveCore>(`/drives/${id}`),
	createDraft: (fields: DriveFields, settings: SettingsInput) =>
		api.post<{ drive: DriveCore; settings: DriveSettings }>("/placement/drives", { ...fields, settings }),
	updateDrive: (id: number, fields: DriveFields) => api.put<DriveCore>(`/placement/drives/${id}`, fields),
	settings: (id: number) => api.get<DriveSettings>(`/placement/drives/${id}/settings`),
	saveSettings: (id: number, s: SettingsInput) => api.put<DriveSettings>(`/placement/drives/${id}/settings`, s),
	lifecycle: (id: number, to: Lifecycle) => api.post<DriveSettings>(`/placement/drives/${id}/lifecycle`, { to }),
	applicants: (id: number, p: { status?: string; eligible?: string; q?: string; limit?: number; offset?: number }) =>
		paged<Applicant>(`/placement/drives/${id}/applicants${qs(p)}`),
	eligibility: (id: number, p: { eligible_only?: boolean; limit?: number; offset?: number }) =>
		paged<EligibilityRow>(`/placement/drives/${id}/eligibility${qs(p)}`),
	setStatus: (applicationId: number, status: AppStatus, expected: AppStatus, note: string | null) =>
		api.patch(`/placement/applications/${applicationId}/status`, { status, expected_status: expected, note }),
	bulk: (
		driveId: number,
		to: BulkTarget,
		items: { application_id: number; expected_status: AppStatus }[],
		note: string | null,
		dryRun: boolean,
	) =>
		api.post<BulkResponse>(`/placement/drives/${driveId}/applications/bulk-status`, {
			to_status: to,
			items,
			note,
			dry_run: dryRun,
		}),
	history: (applicationId: number) => api.get<HistoryEvent[]>(`/placement/applications/${applicationId}/history`),
	departments: () => api.get<Department[]>("/admin/departments"),
	// staff: students + import
	students: (p: Record<string, string | number | boolean | null | undefined>) =>
		paged<StudentListItem>(`/placement/students${qs(p)}`),
	student: (id: number) => api.get<StudentDetail>(`/placement/students/${id}`),
	saveProfile: (id: number, values: ProfileValues) =>
		api.put<StudentProfile>(`/placement/students/${id}/profile`, values),
	importTemplate: () => api.getBlob("/placement/import/template"),
	importPreview: (file: File) => {
		const fd = new FormData()
		fd.append("file", file)
		return api.postFile<ImportPreview>("/placement/import/preview", fd)
	},
	importCommit: (file: File, sha: string) => {
		const fd = new FormData()
		fd.append("file", file)
		fd.append("file_sha256", sha)
		return api.postFile<ImportSummary>("/placement/import/commit", fd)
	},
	// student
	myProfile: () => api.get<MyProfile>("/placement/me/profile"),
	myDrives: (scope: "open" | "all") => paged<StudentDrive>(`/placement/me/drives${qs({ scope, limit: 200 })}`),
	myEligibility: (id: number) => api.get<MyEligibility>(`/placement/drives/${id}/my-eligibility`),
	apply: (id: number) => api.post(`/placement/drives/${id}/apply`),
	myApplications: () => paged<MyApplication>("/placement/me/applications?limit=200"),
	withdraw: (applicationId: number, expected: AppStatus) =>
		api.post(`/placement/applications/${applicationId}/withdraw`, { expected_status: expected }),
}

// --- Phase 9.3: campaigns + dashboard ------------------------------------------------
export type CampaignStatus = "draft" | "active" | "closed" | "cancelled" | "archived"
export type Completion = "pending" | "completed" | "completed_late"

export type Campaign = {
	id: number
	title: string
	description: string | null
	status: CampaignStatus
	interview_type: "hr" | "technical" | "coding" | null
	target_role: string | null
	drive_id: number | null
	drive_label: string | null
	starts_at: string
	due_at: string
	created_by_role: string
	created_by_name: string | null
	is_mine: boolean
	assigned: number
	completed: number
	completed_late: number
	created_at: string
}

export type CampaignInput = {
	title: string
	description: string | null
	interview_type: Campaign["interview_type"]
	target_role: string | null
	drive_id: number | null
	starts_at: string
	due_at: string
}

export type Candidate = {
	student_id: number
	full_name: string
	section_id: number | null
	section_name: string | null
	department_name: string | null
	assigned: boolean
}

/** Completion status ONLY (decision P9-D7) - there is no score field. */
export type ProgressRow = {
	student_id: number
	full_name: string
	section_name: string | null
	completion: Completion
	completed_at: string | null
	assigned_at: string
}

export type MyCampaign = {
	id: number
	title: string
	description: string | null
	status: CampaignStatus
	interview_type: Campaign["interview_type"]
	target_role: string | null
	drive_id: number | null
	drive_label: string | null
	starts_at: string
	due_at: string
	completion: Completion
	completed_at: string | null
}

export type Dashboard = {
	academic_year: string | null
	drives: { total: number; by_lifecycle: Record<Lifecycle, number>; active: number }
	upcoming_deadlines: { drive_id: number; company_name: string; role_title: string; closes_at: string; kind: string }[]
	applications: { total: number; by_status: Record<AppStatus, number> }
	shortlisting: { decided: number; pending: number; decided_percentage: number | null }
	students: {
		registered: number
		available_for_placement: number
		placed: number
		selected: number
		without_academic_records: number
	}
	campaigns: { active: number; assigned: number; completed: number }
	pending_actions: { kind: string; count: number; label: string }[]
	notes: string[]
}

// --- Phase 9.4: analytics + preparation -------------------------------------------------
export type AnalyticsFilters = {
	academic_year?: string
	department_id?: string
	graduation_year?: string
	company?: string
	drive_id?: string
	status?: string
}

export type Analytics = {
	filters: string[]
	summary: {
		registered_students: number
		eligible_students: number
		eligible_basis: string
		applications: number
		applications_by_status: Record<AppStatus, number>
		applicants: number
		shortlisted_students: number
		selected_students: number
		offers: number
		offers_by_status: Record<string, number>
		placed_students: number
		placement_denominator: number
		placement_percentage: number | null
		drives: number
	}
	companies: {
		company: string
		drives: number
		applications: number
		applicants: number
		shortlisted_students: number
		selected_students: number
		offers_accepted: number
		placed_students: number
	}[]
	departments: {
		department: string
		registered_students: number
		applicants: number
		selected_students: number
		placed_students: number
		placement_percentage: number | null
	}[]
	drives: {
		drive_id: number
		company: string
		role_title: string
		lifecycle: Lifecycle
		academic_year: string | null
		applications: number
		by_status: Record<AppStatus, number>
		placed_students: number
	}[]
}

export type AnalyticsOptions = {
	departments: { id: number; name: string }[]
	companies: string[]
	drives: { id: number; label: string }[]
	academic_years: string[]
}

export type Preparation = {
	drive_id: number
	practice_available: boolean
	company_profile: { id: number; name: string } | null
	suggested_role: string
	suggested_company: string
	practice_link: string | null
	my_completed_practice: number
	my_last_practice_at: string | null
	campaigns: { id: number; title: string; due_at: string; completion: Completion }[]
}

export const EXPORT_KINDS = ["summary", "companies", "departments", "drives", "applications"] as const
export type ExportKind = (typeof EXPORT_KINDS)[number]

export const insightsApi = {
	dashboard: (academicYear: string) => api.get<Dashboard>(`/placement/dashboard${qs({ academic_year: academicYear })}`),
	campaigns: (p: { status?: string; include_archived?: boolean; limit?: number; offset?: number }) =>
		paged<Campaign>(`/placement/campaigns${qs(p)}`),
	campaign: (id: number) => api.get<Campaign>(`/placement/campaigns/${id}`),
	createCampaign: (c: CampaignInput) => api.post<Campaign>("/placement/campaigns", c),
	editCampaign: (id: number, c: CampaignInput) => api.put<Campaign>(`/placement/campaigns/${id}`, c),
	campaignStatus: (id: number, to: CampaignStatus) => api.post<Campaign>(`/placement/campaigns/${id}/status`, { to }),
	sections: () => api.get<{ id: number; name: string; department_name: string | null }[]>("/placement/campaigns/sections"),
	candidates: (id: number, p: Record<string, string | number | undefined>) =>
		paged<Candidate>(`/placement/campaigns/${id}/candidates${qs(p)}`),
	assign: (id: number, body: { student_ids?: number[]; section_ids?: number[] }) =>
		api.post<{ added: number; already_assigned: number; total_assigned: number }>(
			`/placement/campaigns/${id}/students`,
			body,
		),
	unassign: (id: number, studentId: number) => api.delete<void>(`/placement/campaigns/${id}/students/${studentId}`),
	progress: (id: number, p: { completion?: string; limit?: number; offset?: number }) =>
		paged<ProgressRow>(`/placement/campaigns/${id}/progress${qs(p)}`),
	myCampaigns: () => paged<MyCampaign>("/placement/me/campaigns?limit=200"),
	analytics: (f: AnalyticsFilters) => api.get<Analytics>(`/placement/analytics${qs(f)}`),
	analyticsOptions: () => api.get<AnalyticsOptions>("/placement/analytics/options"),
	exportCsv: (kind: ExportKind, f: AnalyticsFilters) => api.getBlob(`/placement/analytics/export${qs({ kind, ...f })}`),
	preparation: (driveId: number) => api.get<Preparation>(`/placement/drives/${driveId}/preparation`),
}

/** Save a Blob as a file in the browser. */
export function downloadBlob(blob: Blob, filename: string): void {
	const url = URL.createObjectURL(blob)
	const a = document.createElement("a")
	a.href = url
	a.download = filename
	document.body.appendChild(a)
	a.click()
	a.remove()
	URL.revokeObjectURL(url)
}

/** Message for any thrown error. */
export function errorText(e: unknown, fallback = "Something went wrong. Please try again."): string {
	return e instanceof ApiError ? e.message : fallback
}
