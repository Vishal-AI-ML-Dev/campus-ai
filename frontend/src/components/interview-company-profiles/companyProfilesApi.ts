/**
 * Phase 8.4: company profiles for institute admins and TPOs (decision P8-D9).
 *
 * Backend: /mock-interview/company/profiles/admin, /company/profiles/{id}
 * (PUT), /publish, /archive, /from-drive/{drive_id}; every write is audited
 * on the server with a content-free summary. 404 = company profiles are not
 * enabled for this institute (AI_MOCK_INTERVIEW_COMPANY_ENABLED); 403 = role.
 *
 * Sidebar entry: only for admins/TPOs, only in builds with the mock-interview
 * build flag, and only when the server answers (no new build flag).
 */

import { api } from "@/lib/api"
import type { NavItem, Role } from "@/lib/auth"
import { useApiResource } from "@/lib/useApiResource"

export type SourceKind = "drive" | "jd" | "institute_note" | "public_url"

export type StaffSource = {
	kind: SourceKind
	label: string
	url?: string | null
	domain?: string | null
	drive_id?: number | null
	recorded_at?: string | null
}

export type FocusSkill = { name: string; weight: number }

export type StaffProfile = {
	id: number
	name: string
	role_title: string
	role_template_key: string
	drive_id: number | null
	summary: string | null
	job_description: string | null
	focus_skills: FocusSkill[]
	competencies: string[]
	sources: StaffSource[]
	status: "draft" | "published" | "archived"
	can_publish: boolean
	publish_blockers: string[]
	created_at: string
	updated_at: string
	published_at: string | null
	archived_at: string | null
}

export type ProfileInput = {
	name: string
	role_title: string
	role_template_key: string
	summary: string | null
	job_description: string | null
	focus_skills: FocusSkill[]
	competencies: string[]
	sources: { kind: SourceKind; label: string; url?: string; drive_id?: number }[]
}

export type OpenDrive = { id: number; company_name: string; role_title: string }

const base = "/mock-interview/company"

export const companyProfilesApi = {
	listPath: `${base}/profiles/admin`,
	templatesPath: `${base}/role-templates`,
	openDrivesPath: "/drives?open_only=true",
	create: (input: ProfileInput) =>
		api.post<StaffProfile>(`${base}/profiles/admin`, input as unknown as Record<string, unknown>),
	update: (id: number, input: ProfileInput) =>
		api.put<StaffProfile>(`${base}/profiles/${id}`, input as unknown as Record<string, unknown>),
	publish: (id: number) => api.post<StaffProfile>(`${base}/profiles/${id}/publish`),
	archive: (id: number) => api.post<StaffProfile>(`${base}/profiles/${id}/archive`),
	fromDrive: (driveId: number) => api.post<StaffProfile>(`${base}/profiles/from-drive/${driveId}`),
}

export const COMPANY_PROFILES_NAV_ITEM: NavItem = {
	label: "Company Profiles",
	href: "/dashboard/interview-company-profiles",
	icon: "\u{1F3E2}",
}

export const COMPANY_PROFILES_BUILD_ENABLED = process.env.NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED === "true"

/** Sidebar entry for admins/TPOs when the server has company profiles on, else null. */
export function useCompanyProfilesNavItem(role: Role | undefined): NavItem | null {
	const shouldAsk = COMPANY_PROFILES_BUILD_ENABLED && (role === "admin" || role === "tpo")
	const { data } = useApiResource<unknown[]>(shouldAsk ? companyProfilesApi.templatesPath : null)
	return shouldAsk && Array.isArray(data) ? COMPANY_PROFILES_NAV_ITEM : null
}
