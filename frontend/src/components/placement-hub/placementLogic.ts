/**
 * Placement Hub (Phase 9.2-9.4) - pure helpers: labels, rules mirrored from
 * the backend for display only (the server always re-checks), selection and
 * file checks. No imports, so the unit tests can load this file directly.
 */

export type AppStatus = "applied" | "shortlisted" | "selected" | "rejected" | "withdrawn"
export type Lifecycle = "draft" | "published" | "closed" | "completed" | "cancelled"
export type WindowState = "not_open_yet" | "open" | "closed" | "no_window"
export type PlacementStatus = "active" | "opted_out" | "debarred"

export const APP_STATUSES: AppStatus[] = ["applied", "shortlisted", "selected", "rejected", "withdrawn"]
export const LIFECYCLES: Lifecycle[] = ["draft", "published", "closed", "completed", "cancelled"]

export const STATUS_LABEL: Record<AppStatus, string> = {
	applied: "Applied",
	shortlisted: "Shortlisted",
	selected: "Selected",
	rejected: "Not selected",
	withdrawn: "Withdrawn",
}

export const LIFECYCLE_LABEL: Record<Lifecycle, string> = {
	draft: "Draft",
	published: "Published",
	closed: "Closed",
	completed: "Completed",
	cancelled: "Cancelled",
}

export const WINDOW_LABEL: Record<WindowState, string> = {
	not_open_yet: "Opens later",
	open: "Applications open",
	closed: "Window closed",
	no_window: "No window set",
}

export const PLACEMENT_STATUS_LABEL: Record<PlacementStatus, string> = {
	active: "Active",
	opted_out: "Opted out",
	debarred: "Debarred",
}

/** Tailwind classes for a status pill (text + background, both themes). */
export const STATUS_TONE: Record<AppStatus, string> = {
	applied: "bg-slate-500/15 text-slate-700 dark:text-slate-200",
	shortlisted: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
	selected: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-200",
	rejected: "bg-red-500/15 text-red-800 dark:text-red-200",
	withdrawn: "bg-slate-500/10 text-slate-500 dark:text-slate-400 line-through",
}

export const LIFECYCLE_TONE: Record<Lifecycle, string> = {
	draft: "bg-slate-500/15 text-slate-700 dark:text-slate-200",
	published: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-200",
	closed: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
	completed: "bg-indigo-500/15 text-indigo-800 dark:text-indigo-200",
	cancelled: "bg-red-500/15 text-red-800 dark:text-red-200",
}

/** Drive lifecycle moves the TPO may request (spec §5.4). */
export const DRIVE_MOVES: Record<Lifecycle, Lifecycle[]> = {
	draft: ["published", "cancelled"],
	published: ["closed", "cancelled"],
	closed: ["published", "completed", "cancelled"],
	completed: [],
	cancelled: [],
}

export const MOVE_LABEL: Record<Lifecycle, string> = {
	draft: "Move to draft",
	published: "Publish",
	closed: "Close applications",
	completed: "Mark completed",
	cancelled: "Cancel drive",
}

/** Single-application staff moves (spec §5.4); `withdrawn` is never a staff move. */
export const APP_MOVES: Record<AppStatus, AppStatus[]> = {
	applied: ["shortlisted", "rejected", "selected"],
	shortlisted: ["selected", "rejected"],
	rejected: ["shortlisted"],
	selected: ["rejected"],
	withdrawn: [],
}

export function noteRequired(from: AppStatus): boolean {
	return from === "rejected" || from === "selected"
}

export const BULK_TARGETS = ["shortlisted", "rejected"] as const
export type BulkTarget = (typeof BULK_TARGETS)[number]
export const BULK_MAX = 200

/** Which selected rows could move to `to` (client hint; the server decides). */
export function bulkCandidates<T extends { application_id: number; status: AppStatus }>(
	rows: T[],
	selected: Set<number>,
	to: BulkTarget,
): { valid: T[]; skipped: T[] } {
	const valid: T[] = []
	const skipped: T[] = []
	for (const row of rows) {
		if (!selected.has(row.application_id)) continue
		if (APP_MOVES[row.status].includes(to)) valid.push(row)
		else skipped.push(row)
	}
	return { valid, skipped }
}

export function toggleId(set: Set<number>, id: number): Set<number> {
	const next = new Set(set)
	if (next.has(id)) next.delete(id)
	else next.add(id)
	return next
}

export function parseStatusCounts(header: string | null): Partial<Record<AppStatus, number>> {
	const out: Partial<Record<AppStatus, number>> = {}
	if (!header) return out
	for (const part of header.split(",")) {
		const [key, value] = part.split(":")
		if ((APP_STATUSES as string[]).includes(key) && /^\d+$/.test(value ?? "")) {
			out[key as AppStatus] = Number(value)
		}
	}
	return out
}

// --- Academic import --------------------------------------------------------------
export const IMPORT_MAX_BYTES = 2 * 1024 * 1024
export const IMPORT_COLUMNS = [
	"email",
	"roll_number",
	"graduation_year",
	"tenth_percent",
	"twelfth_percent",
	"diploma_percent",
	"active_backlogs",
] as const

/** First, friendly client check. The server re-validates everything. */
export function importFileProblem(file: { name: string; size: number } | null): string | null {
	if (!file) return "Choose a .csv or .xlsx file."
	const name = file.name.toLowerCase()
	if (!name.endsWith(".csv") && !name.endsWith(".xlsx")) return "Only .csv and .xlsx files are accepted."
	if (file.size === 0) return "The file is empty."
	if (file.size > IMPORT_MAX_BYTES) return "The file is larger than 2 MB."
	return null
}

// --- Formatting -------------------------------------------------------------------
export function formatDateTime(iso: string | null | undefined): string {
	if (!iso) return "—"
	const d = new Date(iso)
	if (Number.isNaN(d.getTime())) return "—"
	return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

export function formatDate(iso: string | null | undefined): string {
	if (!iso) return "—"
	const d = new Date(iso)
	if (Number.isNaN(d.getTime())) return "—"
	return d.toLocaleDateString(undefined, { dateStyle: "medium" })
}

export function percent(value: number | null | undefined): string {
	return value === null || value === undefined ? "—" : `${value}%`
}

/** "2026-11-30T10:00" (datetime-local, browser time) -> ISO with offset, or null. */
export function localInputToIso(value: string): string | null {
	if (!value) return null
	const d = new Date(value)
	return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

/** ISO -> "YYYY-MM-DDTHH:MM" for a datetime-local input (browser time). */
export function isoToLocalInput(iso: string | null | undefined): string {
	if (!iso) return ""
	const d = new Date(iso)
	if (Number.isNaN(d.getTime())) return ""
	const pad = (n: number) => String(n).padStart(2, "0")
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** "2027, 2028" -> [2027, 2028]; null when any entry is not a plausible year. */
export function parseYears(text: string): number[] | null {
	const parts = text.split(/[\s,]+/).filter(Boolean)
	const years: number[] = []
	for (const p of parts) {
		if (!/^\d{4}$/.test(p)) return null
		const y = Number(p)
		if (y < 2000 || y > 2100) return null
		if (!years.includes(y)) years.push(y)
	}
	return years.length > 6 ? null : years.sort((a, b) => a - b)
}

export function optionalNumber(text: string): number | null {
	const t = text.trim()
	if (!t) return null
	const n = Number(t)
	return Number.isFinite(n) ? n : null
}

// --- Campaigns (Phase 9.3) - completion status ONLY (decision P9-D7) --------------
export type Completion = "pending" | "completed" | "completed_late"
export type CampaignStatus = "draft" | "active" | "closed" | "cancelled" | "archived"

export const COMPLETION_LABEL: Record<Completion, string> = {
	pending: "Not completed yet",
	completed: "Completed",
	completed_late: "Completed after due date",
}

export const CAMPAIGN_STATUS_LABEL: Record<CampaignStatus, string> = {
	draft: "Draft",
	active: "Active",
	closed: "Closed",
	cancelled: "Cancelled",
	archived: "Archived",
}

export const CAMPAIGN_MOVES: Record<CampaignStatus, CampaignStatus[]> = {
	draft: ["active", "cancelled"],
	active: ["closed", "cancelled"],
	closed: ["active", "archived"],
	cancelled: ["archived"],
	archived: [],
}

export const CAMPAIGN_MOVE_LABEL: Record<CampaignStatus, string> = {
	draft: "Move to draft",
	active: "Activate",
	closed: "Close",
	cancelled: "Cancel",
	archived: "Archive",
}

/** Percent with one decimal, or a dash when there is nothing to divide by. */
export function ratio(n: number, d: number): string {
	return d > 0 ? `${Math.round((n / d) * 1000) / 10}%` : "—"
}

/** Keys of a practice link built by the backend; read by the interview setup form. */
export const PREFILL_KEYS = { role: "prep_role", company: "prep_company", profile: "prep_profile" } as const

/** Parse and bound the mock-interview prefill parameters (never trusted as-is). */
export function parsePracticePrefill(search: string): { role: string; company: string; profileId: number | null } {
	const sp = new URLSearchParams(search)
	const clean = (v: string | null) => (v ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 150)
	const rawId = sp.get(PREFILL_KEYS.profile)
	const id = rawId && /^\d{1,9}$/.test(rawId) ? Number(rawId) : null
	return { role: clean(sp.get(PREFILL_KEYS.role)), company: clean(sp.get(PREFILL_KEYS.company)), profileId: id && id > 0 ? id : null }
}
