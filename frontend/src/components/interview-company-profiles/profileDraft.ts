/**
 * Phase 8.4: company profile draft logic (no React; unit-tested in Node).
 *
 * The server validates everything again (lengths, source kinds, same-institute
 * open drives, >= 1 source to publish). Public URLs are citations: they are
 * stored as text and never fetched or turned into links.
 */

import type { FocusSkill, ProfileInput, SourceKind, StaffProfile, StaffSource } from "./companyProfilesApi"

export type SourceDraft = { kind: SourceKind; label: string; url: string; driveId: string }

export type ProfileDraft = {
	name: string
	roleTitle: string
	templateKey: string
	summary: string
	jobDescription: string
	focusSkills: string
	competencies: string
	sources: SourceDraft[]
}

export const SOURCE_KINDS: { value: SourceKind; label: string }[] = [
	{ value: "jd", label: "Job description" },
	{ value: "institute_note", label: "Institute note" },
	{ value: "public_url", label: "Public page (citation only)" },
	{ value: "drive", label: "Placement drive" },
]

export const LIMITS = { name: 150, roleTitle: 150, summary: 2000, jobDescription: 10000, sources: 10, skills: 20, competencies: 8 }

export function emptyDraft(templateKey = "sde_fresher"): ProfileDraft {
	return {
		name: "",
		roleTitle: "",
		templateKey,
		summary: "",
		jobDescription: "",
		focusSkills: "",
		competencies: "",
		sources: [],
	}
}

function splitList(text: string, max: number): string[] {
	const out: string[] = []
	for (const part of text.split(/[,\n]/)) {
		const item = part.trim().replace(/\s+/g, " ")
		if (item && !out.some((x) => x.toLowerCase() === item.toLowerCase())) out.push(item)
	}
	return out.slice(0, max)
}

export function draftFromProfile(p: StaffProfile): ProfileDraft {
	return {
		name: p.name,
		roleTitle: p.role_title,
		templateKey: p.role_template_key,
		summary: p.summary ?? "",
		jobDescription: p.job_description ?? "",
		focusSkills: p.focus_skills.map((s) => s.name).join(", "),
		competencies: p.competencies.join(", "),
		sources: p.sources.map((s: StaffSource) => ({
			kind: s.kind,
			label: s.label,
			url: s.url ?? "",
			driveId: s.drive_id ? String(s.drive_id) : "",
		})),
	}
}

export function toInput(d: ProfileDraft): ProfileInput {
	const skills: FocusSkill[] = splitList(d.focusSkills, LIMITS.skills).map((name) => ({ name, weight: 2 }))
	return {
		name: d.name.trim(),
		role_title: d.roleTitle.trim(),
		role_template_key: d.templateKey,
		summary: d.summary.trim() || null,
		job_description: d.jobDescription.trim() || null,
		focus_skills: skills,
		competencies: splitList(d.competencies, LIMITS.competencies),
		sources: d.sources.map((s) => ({
			kind: s.kind,
			label: s.label.trim(),
			...(s.kind === "public_url" ? { url: s.url.trim() } : {}),
			...(s.kind === "drive" && s.driveId ? { drive_id: Number(s.driveId) } : {}),
		})),
	}
}

/** Problems that stop saving (null = OK). Publishing additionally needs a source. */
export function draftError(d: ProfileDraft): string | null {
	if (d.name.trim().length < 2) return "Enter the company name."
	if (d.roleTitle.trim().length < 2) return "Enter the role title."
	if (!d.templateKey) return "Choose a role template."
	for (const s of d.sources) {
		if (s.label.trim().length < 2) return "Describe every source (at least 2 characters)."
		if (s.kind === "public_url" && !/^https?:\/\/[^\s/]+/i.test(s.url.trim()))
			return "A public source needs an http(s) address."
		if (s.kind === "drive" && !s.driveId) return "Choose the placement drive for the drive source."
	}
	return null
}

export const STATUS_LABELS: Record<string, string> = { draft: "Draft", published: "Published", archived: "Archived" }
