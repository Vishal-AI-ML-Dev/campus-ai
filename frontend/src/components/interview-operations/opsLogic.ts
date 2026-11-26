/**
 * Interview operations (Phase 7.8) - pure display logic, no React, no "@/" imports,
 * so `node --test` can load it directly (tests/unit/interviewOperations.test.mjs).
 *
 * Rules encoded here:
 *   - cost is shown ONLY when the backend reports an estimate from an approved
 *     price table; "pricing not configured" is never shown as 0 (decision P7-D9);
 *   - unknown token counts are shown as unknown, never as 0;
 *   - only an institute admin may edit controls; TPO is read-only (P7-D3) - the
 *     backend enforces this too (403), the UI just does not offer it.
 */

export type Role = "student" | "teacher" | "tpo" | "admin" | "recruiter"

export type CostInfo = {
	status: string
	currency: string | null
	total_micros: number | null
	priced_calls: number
	unpriced_calls: number
	price_versions: string[]
}

export type UsageMe = {
	available: boolean
	reason: string | null
	daily_limit: number
	used_today: number
	remaining_today: number
	voice_allowed: boolean
	video_allowed: boolean
}

export const LIMIT_FIELDS = [
	{ key: "student_sessions_per_day", label: "Interviews per student per day" },
	{ key: "tenant_sessions_per_month", label: "Interviews per month (whole institute)" },
	{ key: "tenant_tokens_per_month", label: "AI tokens per month (whole institute)" },
] as const

export type LimitKey = (typeof LIMIT_FIELDS)[number]["key"]

export function canViewOps(role: Role | undefined): boolean {
	return role === "admin" || role === "tpo"
}

/** Editing needs BOTH the admin role and the server's can_edit flag. */
export function canEditControls(role: Role | undefined, serverCanEdit: boolean | undefined): boolean {
	return role === "admin" && serverCanEdit === true
}

const NUMBER = new Intl.NumberFormat("en-IN")

export function formatCount(n: number | null | undefined): string {
	return typeof n === "number" && Number.isFinite(n) ? NUMBER.format(n) : "unknown"
}

// Token budgets count provider-reported tokens only (spec 15.12, option a): say so
// whenever some calls reported no or only part of their usage.
export function tokenUsageHint(unknown: number | null | undefined, partial: number | null | undefined): string {
	const parts: string[] = []
	if ((unknown ?? 0) > 0) parts.push(`${formatCount(unknown)} call(s) with unknown usage`)
	if ((partial ?? 0) > 0) parts.push(`${formatCount(partial)} partly reported`)
	return parts.length ? `${parts.join(" · ")} (not counted)` : "Provider-reported counts"
}

export function formatLatency(ms: number | null | undefined): string {
	if (typeof ms !== "number" || !Number.isFinite(ms)) return "–"
	return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`
}

export function formatRate(rate: number | null | undefined): string {
	if (typeof rate !== "number" || !Number.isFinite(rate)) return "–"
	return `${Math.round(rate * 1000) / 10}%`
}

/** Micro-units (1e-6 of the table currency) as a currency amount string. */
export function formatMicros(micros: number, currency: string): string {
	const amount = micros / 1_000_000
	const digits = amount !== 0 && Math.abs(amount) < 1 ? 4 : 2
	return `${currency} ${amount.toFixed(digits)}`
}

export function describeCost(cost: CostInfo | null | undefined): { headline: string; detail: string } {
	if (!cost) return { headline: "Unavailable", detail: "" }
	switch (cost.status) {
		case "estimated":
			return cost.total_micros !== null && cost.currency
				? {
						headline: `${formatMicros(cost.total_micros, cost.currency)} (estimate)`,
						detail: `From the approved price table (${cost.price_versions.join(", ")}).`,
					}
				: { headline: "Unavailable", detail: "" }
		case "partial":
			return cost.total_micros !== null && cost.currency
				? {
						headline: `At least ${formatMicros(cost.total_micros, cost.currency)} (estimate)`,
						detail: `${cost.unpriced_calls} AI call(s) could not be priced (unknown model or token counts).`,
					}
				: { headline: "Unavailable", detail: "" }
		case "mixed_currency":
			return { headline: "Not shown", detail: "Calls were priced in more than one currency." }
		case "no_priced_calls":
			return { headline: "No priced calls", detail: "No AI call in this period could be priced." }
		case "pricing_not_configured":
		default:
			return {
				headline: "Not configured",
				detail:
					"No approved price table is configured, so no cost is estimated. Token usage is still tracked.",
			}
	}
}

/** One line for the student's setup form. */
export function quotaMessage(me: UsageMe | null | undefined): string | null {
	if (!me) return null
	if (me.reason === "paused") return "Practice interviews are paused by your institute right now."
	if (me.reason === "institute_limit")
		return "Your institute has reached its practice interview limit for this month."
	if (me.reason === "daily_limit" || me.remaining_today <= 0)
		return `You have used all ${me.daily_limit} of today's practice interviews.`
	const n = me.remaining_today
	return `You can start ${n} more practice interview${n === 1 ? "" : "s"} today.`
}

/** "" -> null (no institute limit), a positive whole number -> number, else "invalid". */
export function parseLimitInput(text: string): number | null | "invalid" {
	const t = text.trim()
	if (t === "") return null
	if (!/^\d+$/.test(t)) return "invalid"
	const n = Number(t)
	return Number.isSafeInteger(n) && n >= 1 ? n : "invalid"
}

/** Highest value an institute may enter for a limit (the platform limit). */
export function limitCeiling(
	key: LimitKey,
	platformLimits: Record<string, number | null>,
): number | null {
	const v = platformLimits[key]
	return typeof v === "number" ? v : null
}

export function validateLimit(
	key: LimitKey,
	text: string,
	platformLimits: Record<string, number | null>,
): string | null {
	const parsed = parseLimitInput(text)
	if (parsed === "invalid") return "Enter a whole number of 1 or more, or leave it empty."
	const ceiling = limitCeiling(key, platformLimits)
	if (parsed !== null && ceiling !== null && parsed > ceiling)
		return `Must be at most ${formatCount(ceiling)} (the platform limit).`
	return null
}

export const STEP_LABELS: Record<string, string> = {
	plan: "Planning",
	turn: "Questions",
	report: "Reports",
	create: "New interview",
}

export const OUTCOME_LABELS: Record<string, string> = {
	ok: "OK",
	fallback: "OK (fallback used)",
	provider_error: "AI provider error",
	worker_error: "AI service error",
	timeout: "Timed out",
	unreachable: "AI service unreachable",
	circuit_open: "Skipped (circuit open)",
	quota_blocked: "Blocked by quota",
}

export function outcomeLabel(outcome: string): string {
	return OUTCOME_LABELS[outcome] ?? outcome
}

export function breakerLabel(state: string): string {
	switch (state) {
		case "closed":
			return "Healthy"
		case "half_open":
			return "Recovering (trial call)"
		case "open":
			return "Paused after failures"
		case "disabled":
			return "Breaker disabled"
		default:
			return state
	}
}

/** Bar heights (0-100) for a small daily chart; all-zero series stay flat. */
export function barHeights(values: number[]): number[] {
	const max = Math.max(0, ...values)
	return values.map((v) => (max > 0 ? Math.round((v / max) * 100) : 0))
}
