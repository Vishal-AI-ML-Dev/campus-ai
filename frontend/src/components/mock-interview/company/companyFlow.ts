/**
 * Phase 8.4: company-context helpers (no React; unit-tested in Node).
 *
 * Decisions P8-D10/D11: institute content is shown as "From your institute",
 * never as verified company fact; public URLs are shown as plain text (label
 * and domain), never as links; company context changes question generation
 * only, so there is no company-fit or competency score anywhere.
 */

export type CompanySource = { kind: string; label: string; domain?: string | null }

export const SOURCE_KIND_LABELS: Record<string, string> = {
	drive: "Placement drive",
	jd: "Job description",
	institute_note: "Institute note",
	public_url: "Public page",
}

/** A source as plain text: "Public page: Careers page (example.com)". Never a URL. */
export function sourceText(s: CompanySource): string {
	const kind = SOURCE_KIND_LABELS[s.kind] ?? "Source"
	return s.kind === "public_url" && s.domain ? `${kind}: ${s.label} (${s.domain})` : `${kind}: ${s.label}`
}

export const EVALUATION_NOTE =
	"Your answers are scored with the standard HR or technical rubric. There is no company-fit score."

export type CompanyChoice = { profileId: number | null; templateKey: string | null; focusSkills: string[] }

export const NO_COMPANY: CompanyChoice = { profileId: null, templateKey: null, focusSkills: [] }

/** Encodes the picker value: "" (none), "p:<id>" (profile) or "t:<key>" (role template). */
export function choiceValue(c: CompanyChoice): string {
	if (c.profileId !== null) return `p:${c.profileId}`
	if (c.templateKey) return `t:${c.templateKey}`
	return ""
}

export function parseChoice(value: string, focusSkills: string[] = []): CompanyChoice {
	if (value.startsWith("p:")) {
		const id = Number(value.slice(2))
		return Number.isInteger(id) && id > 0 ? { profileId: id, templateKey: null, focusSkills } : NO_COMPANY
	}
	if (value.startsWith("t:") && value.length > 2) return { profileId: null, templateKey: value.slice(2), focusSkills }
	return NO_COMPANY
}

/** The optional create-request fields; empty when nothing was chosen (request unchanged). */
export function companyCreateFields(c: CompanyChoice): {
	company_profile_id?: number
	role_template_key?: string
	focus_skills?: string[]
} {
	const out: { company_profile_id?: number; role_template_key?: string; focus_skills?: string[] } = {}
	if (c.profileId !== null) out.company_profile_id = c.profileId
	else if (c.templateKey) out.role_template_key = c.templateKey
	const skills = c.focusSkills.map((s) => s.trim()).filter(Boolean).slice(0, 20)
	if (skills.length && (out.company_profile_id !== undefined || out.role_template_key !== undefined)) {
		out.focus_skills = skills
	}
	return out
}
