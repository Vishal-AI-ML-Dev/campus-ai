/**
 * Phase 8.4: student-side company context API (decisions P8-D9..D11).
 *
 * Backend: /mock-interview/company/* (docs/mock-interview/API.md, Phase 8.4).
 * Every route 404s while AI_MOCK_INTERVIEW_COMPANY_ENABLED is off for the
 * institute; the UI then shows nothing.
 */

import { api } from "@/lib/api"
import type { CompanySource } from "./companyFlow"

export type RoleTemplate = {
	key: string
	label: string
	description: string
	focus_skills: string[]
	competencies: string[]
}

export type CompanyProfileBrief = { id: number; name: string; role_title: string; role_template_label: string }

export type CompanyProfile = {
	id: number
	name: string
	role_title: string
	role_template_key: string
	role_template_label: string
	summary: string | null
	focus_skills: string[]
	competencies: string[]
	sources: CompanySource[]
	attribution: string
	disclaimer: string
	has_job_description: boolean
}

export type JDSkill = { skill: string; category: string; importance: number; evidence: string; matched: boolean }

export type JDSkills = {
	source: "job_description" | "drive" | "profile"
	skills: JDSkill[]
	matched: string[]
	missing: string[]
	focus_order: string[]
	note: string
}

/** On a session created with company/role context (frozen at create). */
export type CompanyContext = {
	profile_id: number | null
	name: string | null
	role_title: string
	role_template_label: string | null
	sources: CompanySource[]
	attribution: string
	disclaimer: string
	evaluation: "standard_rubric"
}

const base = "/mock-interview/company"

export const companyApi = {
	profilesPath: `${base}/profiles`,
	templatesPath: `${base}/role-templates`,
	profile: (id: number) => api.get<CompanyProfile>(`${base}/profiles/${id}`),
	jdSkills: (body: { job_description: string } | { profile_id: number }) =>
		api.post<JDSkills>(`${base}/jd-skills`, body),
}
