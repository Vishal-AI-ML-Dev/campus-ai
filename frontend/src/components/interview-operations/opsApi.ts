/**
 * Interview operations (Phase 7.8) - types, API calls and the feature flag.
 *
 * Backend: /mock-interview/admin/* (see docs/mock-interview/API.md, Phase 7.7).
 * Every response is content-free (counts, statuses, ids, timings, costs).
 *
 * Build flag: NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED === "true" (default OFF) on
 * top of NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED. The backend decides per institute
 * (AI_MOCK_INTERVIEW_OPS_ENABLED) and per role; a 404 means "not enabled".
 */

import { useCallback, useEffect, useState } from "react"
import { api, ApiError } from "@/lib/api"
import type { NavItem, Role } from "@/lib/auth"
import { useApiResource } from "@/lib/useApiResource"
import type { CostInfo, UsageMe } from "./opsLogic"

export const INTERVIEW_OPS_BUILD_ENABLED =
	process.env.NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED === "true" &&
	process.env.NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED === "true"

export const INTERVIEW_OPS_NAV_ITEM: NavItem = {
	label: "Interview Ops",
	href: "/dashboard/interview-operations",
	icon: "\u{1F4CA}",
}

/** Sidebar entry for admins/TPOs, or null. No request unless the build flag is on. */
export function useInterviewOpsNavItem(role: Role | undefined): NavItem | null {
	const shouldAsk = INTERVIEW_OPS_BUILD_ENABLED && (role === "admin" || role === "tpo")
	const { data } = useApiResource<{ enabled: boolean }>(shouldAsk ? "/mock-interview/status" : null)
	return shouldAsk && data?.enabled ? INTERVIEW_OPS_NAV_ITEM : null
}

export type UsageTotals = {
	sessions_created: number
	sessions_completed: number
	sessions_abandoned: number
	sessions_in_progress: number
	ai_calls: number
	ai_failures: number
	circuit_open: number
	quota_blocked: number
	llm_calls: number
	input_tokens: number
	output_tokens: number
	calls_with_unknown_tokens: number
	calls_with_partial_tokens?: number
	avg_latency_ms: number | null
	p95_latency_ms: number | null
}

export type DailyUsage = {
	day: string
	sessions_created: number
	ai_calls: number
	ai_failures: number
	input_tokens: number
	output_tokens: number
}

export type BreakdownRow = {
	key: string
	ai_calls: number
	ai_failures: number
	input_tokens: number
	output_tokens: number
	avg_latency_ms: number | null
}

export type UsageSummary = {
	days: number
	since: string
	totals: UsageTotals
	daily: DailyUsage[]
	by_step: BreakdownRow[]
	by_model: BreakdownRow[]
	by_outcome: Record<string, number>
	modes: Record<string, number>
	cost: CostInfo
}

export type StudentUsage = {
	student_id: number
	full_name: string
	sessions_created: number
	sessions_completed: number
	ai_calls: number
	ai_failures: number
	input_tokens: number
	output_tokens: number
	last_activity: string | null
}

export type Controls = {
	feature: string
	configured: boolean
	paused: boolean
	voice_allowed: boolean
	video_allowed: boolean
	limits: Record<string, number | null>
	effective_limits: Record<string, number | null>
	platform_limits: Record<string, number | null>
	platform_voice_enabled: boolean
	platform_video_enabled: boolean
	updated_at: string | null
	can_edit: boolean
}

export type ControlsUpdate = {
	paused: boolean
	voice_allowed: boolean
	video_allowed: boolean
	limits: Record<string, number | null>
}

export type DiagnosticEvent = {
	event_id: string
	created_at: string
	step: string
	outcome: string
	http_status: number | null
	error_class: string | null
	latency_ms: number
	llm_latency_ms: number | null
	llm_calls: number | null
	input_tokens: number | null
	output_tokens: number | null
	fallback_used: boolean | null
	provider: string | null
	model: string | null
	request_id: string | null
}

export type SessionDiagnostics = {
	session_id: number
	student_id: number
	status: string
	mode: string
	interview_type: string
	duration_minutes: number
	created_at: string
	started_at: string | null
	completed_at: string | null
	turns: number
	answered: number
	report_generated: boolean
	events: DiagnosticEvent[]
}

export type StepHealth = {
	step: string
	state: string
	consecutive_failures: number
	retry_in_seconds: number | null
	last_hour_calls: number
	last_hour_failures: number
	last_hour_error_rate: number | null
}

export type Health = { circuit_enabled: boolean; steps: StepHealth[]; note: string }

export type AuditEntry = {
	id: number
	actor_email: string | null
	action: string
	summary: string
	created_at: string
}

const BASE = "/mock-interview/admin"

export const opsApi = {
	usage: (days: number) => api.get<UsageSummary>(`${BASE}/usage?days=${days}`),
	students: (days: number, limit: number, offset: number) =>
		api.get<StudentUsage[]>(`${BASE}/usage/students?days=${days}&limit=${limit}&offset=${offset}`),
	controls: () => api.get<Controls>(`${BASE}/controls`),
	updateControls: (body: ControlsUpdate) => api.put<Controls>(`${BASE}/controls`, body),
	diagnostics: (sessionId: number) => api.get<SessionDiagnostics>(`${BASE}/sessions/${sessionId}/diagnostics`),
	health: () => api.get<Health>(`${BASE}/health`),
	auditTrail: () =>
		api.get<AuditEntry[]>(`/audit?action=${encodeURIComponent("mock_interview.controls_update")}&limit=20`),
	usageMe: () => api.get<UsageMe>("/mock-interview/usage/me"),
}

export type Loaded<T> = {
	data: T | null
	loading: boolean
	error: string | null
	status: number | null
	requestId?: string
	reload: () => void
}

/** Like useApiResource, but keeps the HTTP status (404 = not enabled, 403 = role). */
export function useLoaded<T>(load: (() => Promise<T>) | null, deps: unknown[]): Loaded<T> {
	const [state, setState] = useState<Omit<Loaded<T>, "reload">>({
		data: null,
		loading: load !== null,
		error: null,
		status: null,
	})
	const [tick, setTick] = useState(0)
	const reload = useCallback(() => setTick((t) => t + 1), [])
	useEffect(() => {
		if (load === null) return
		let live = true
		setState((s) => ({ ...s, loading: true, error: null }))
		load()
			.then((data) => live && setState({ data, loading: false, error: null, status: 200 }))
			.catch((err: unknown) => {
				if (!live) return
				const e = err instanceof ApiError ? err : null
				setState({
					data: null,
					loading: false,
					error: e?.message ?? "Something went wrong. Please try again.",
					status: e?.status ?? null,
					requestId: e?.requestId,
				})
			})
		return () => {
			live = false
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [...deps, tick])
	return { ...state, reload }
}
