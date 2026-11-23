/**
 * AI Mock Interview (Phase 3, text) - types + API calls.
 *
 * Backend: /mock-interview/* (see docs/mock-interview/API.md). Every call is
 * safe to retry: start/next/finish/abandon are idempotent and an answer is
 * keyed by its question's `turn_seq`, so re-sending the same text is a no-op.
 */

import { api, ApiError } from "@/lib/api"

export type Choice = { value: string; label: string }

export type ResumeOption = {
	id: number
	title: string
	target_role: string | null
	is_primary: boolean
	created_at: string
}

export type InterviewOptions = {
	interview_types: Choice[]
	difficulties: Choice[]
	durations_minutes: number[]
	languages: Choice[]
	resumes: ResumeOption[]
	job_description_max_chars: number
	answer_max_chars: number
	/** Phase 5: [text] or [text, voice]; absent on older backends. */
	modes?: Choice[]
}

export type InterviewTurn = {
	seq: number
	topic: string | null
	question: string
	is_follow_up: boolean
	answer_text: string | null
	answered_at: string | null
	/** Phase 5: "stt" = dictated in the browser. */
	answer_source?: "typed" | "stt" | null
}

export type InterviewSession = {
	id: number
	interview_type: "hr" | "technical" | string
	mode: string
	status: "setup" | "in_progress" | "evaluating" | "completed" | "abandoned" | "failed"
	target_role: string | null
	company_name: string | null
	difficulty: string
	language: string
	duration_minutes: number
	created_at: string
	started_at: string | null
	completed_at: string | null
	resume_id: number | null
	has_job_description: boolean
	awaiting: "start" | "answer" | "next_question" | "none"
	current_turn_seq: number | null
	remaining_seconds: number | null
	progress: {
		answered: number
		question_budget: number | null
		topics_total: number | null
		topics_covered: number
	}
	turns: InterviewTurn[]
	closing_message: string | null
	end_reason: string | null
	/** Phase 8.1: present only for coding rounds (review-only). */
	coding_summary?: {
		problems: number
		problems_closed: number
		graded_submissions: number
		review_only: boolean
	}
}

export type ReportCategory = {
	key: string
	label: string
	weight: number
	description: string
	/** 0-100, null when nothing could be assessed. */
	score: number | null
	assessed: number
}

export type ReportScore = { key: string; label: string; score: number; evidence: string }

export type ReportQuestion = {
	seq: number
	topic: string | null
	question: string
	is_follow_up: boolean
	answer_text: string | null
	status: "scored" | "no_answer" | "dont_know" | "not_assessed"
	scores: ReportScore[]
	feedback: string
}

export type ReportItem = { text: string; seq: number | null; evidence: string }

export type InterviewReport = {
	session_id: number
	interview_type: string
	target_role: string | null
	company_name: string | null
	difficulty: string
	language: string
	duration_minutes: number
	started_at: string | null
	completed_at: string | null
	overall_score: number
	rubric_version: string
	created_at: string
	categories: ReportCategory[]
	questions: ReportQuestion[]
	strengths: ReportItem[]
	weaknesses: ReportItem[]
	recommendations: string[]
	disclaimer: string
}

export type HistoryItem = {
	id: number
	interview_type: string
	mode: string
	status: InterviewSession["status"]
	target_role: string | null
	company_name: string | null
	difficulty: string
	language: string
	duration_minutes: number
	created_at: string
	started_at: string | null
	completed_at: string | null
	answered: number
	report: { overall_score: number; created_at: string } | null
}

const inflightReports = new Map<number, Promise<InterviewReport>>()

/**
 * Load a session's report, generating it the first time. Generation (POST) is
 * idempotent server-side: it returns the stored report with no AI call, so a
 * retry never creates a second report. With `exists` (from /history) it reads
 * with GET first; otherwise it POSTs directly (no expected-404 round trip).
 */
export function loadOrGenerateReport(
	id: number,
	{ exists = false }: { exists?: boolean } = {},
): Promise<InterviewReport> {
	// Share one in-flight request per session (React dev mode runs effects
	// twice; a second parallel POST would only get "already being prepared").
	const pending = inflightReports.get(id)
	if (pending) return pending
	const request = (async () => {
		if (!exists) return mockInterviewApi.generateReport(id)
		try {
			return await mockInterviewApi.getReport(id)
		} catch (err) {
			if (err instanceof ApiError && err.status === 404) return mockInterviewApi.generateReport(id)
			throw err
		}
	})().finally(() => inflightReports.delete(id))
	inflightReports.set(id, request)
	return request
}

export type CreateInterviewInput = {
	interview_type: string
	difficulty: string
	duration_minutes: number
	target_role: string
	company_name?: string
	resume_id?: number
	job_description?: string
	language: string
	/** Phase 5 voice, Phase 6 video; omitted for text so the request is unchanged. */
	mode?: "voice" | "video"
	/** Phase 8.1: coding-round choices; sent only for interview_type "coding". */
	coding?: { languages: string[]; topics: string[]; problem_key?: string }
}

const base = "/mock-interview/sessions"

export const mockInterviewApi = {
	create: (input: CreateInterviewInput) =>
		api.post<InterviewSession>(base, input as unknown as Record<string, unknown>),
	get: (id: number) => api.get<InterviewSession>(`${base}/${id}`),
	start: (id: number) => api.post<InterviewSession>(`${base}/${id}/start`),
	/** `source` is sent only for voice sessions; text answers keep the Phase 3 payload. */
	answer: (id: number, turnSeq: number, answer: string, source?: "typed" | "stt") =>
		api.post<InterviewSession>(
			`${base}/${id}/answer`,
			source ? { turn_seq: turnSeq, answer, source } : { turn_seq: turnSeq, answer },
		),
	next: (id: number) => api.post<InterviewSession>(`${base}/${id}/next`),
	finish: (id: number) => api.post<InterviewSession>(`${base}/${id}/finish`),
	abandon: (id: number) => api.post<InterviewSession>(`${base}/${id}/abandon`),
	/** Stored report; 404 "Report not generated yet" until POST creates it. */
	getReport: (id: number) => api.get<InterviewReport>(`${base}/${id}/report`),
	/** Generate (once) or return the existing report. Idempotent. */
	generateReport: (id: number) => api.post<InterviewReport>(`${base}/${id}/report`),
	history: () => api.get<HistoryItem[]>("/mock-interview/history"),
}

/** A readable message for any failure, including offline / dropped connections. */
export function describeError(err: unknown, fallback: string): string {
	if (err instanceof ApiError) return err.message
	if (err instanceof TypeError) return "Network problem. Check your connection and try again."
	return fallback
}

export function labelFor(choices: Choice[] | undefined, value: string): string {
	return choices?.find((c) => c.value === value)?.label ?? value
}

export const TYPE_LABELS: Record<string, string> = { hr: "HR", technical: "Technical", coding: "Coding" }
export const MODE_LABELS: Record<string, string> = { text: "Text", voice: "Voice", video: "Video" }
export const DIFFICULTY_LABELS: Record<string, string> = {
	easy: "Beginner",
	medium: "Intermediate",
	hard: "Advanced",
}
export const LANGUAGE_LABELS: Record<string, string> = {
	en: "English",
	hi: "Hindi",
	hinglish: "Hinglish",
}

/** Per-tab draft of an unsent answer (survives a refresh; best-effort). */
export function draftKey(sessionId: number, seq: number): string {
	return `mock-interview:draft:${sessionId}:${seq}`
}

export function readDraft(key: string): string {
	try {
		return window.sessionStorage.getItem(key) ?? ""
	} catch {
		return ""
	}
}

export function writeDraft(key: string, value: string): void {
	try {
		if (value) window.sessionStorage.setItem(key, value)
		else window.sessionStorage.removeItem(key)
	} catch {
		// storage blocked (private mode etc.): drafts are a convenience only
	}
}
