"use client"

/**
 * Phase 8.4: create / edit a company profile (admins and TPOs). Saving keeps
 * the current status; publishing (needs a source) and archiving are separate
 * actions in the list. Sessions already created keep their own frozen copy,
 * so edits never change a past interview.
 */

import { useState } from "react"
import { Spinner } from "@/components/ui/states"
import { describeError } from "@/components/mock-interview/mockInterviewApi"
import type { RoleTemplate } from "@/components/mock-interview/company/companyApi"
import { companyProfilesApi, type OpenDrive, type StaffProfile } from "./companyProfilesApi"
import SourcesEditor from "./SourcesEditor"
import { LIMITS, draftError, draftFromProfile, emptyDraft, toInput, type ProfileDraft } from "./profileDraft"

const field =
	"mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-slate-900"
const label = "block text-sm font-medium"

export default function ProfileForm({
	profile,
	templates,
	drives,
	onSaved,
	onCancel,
}: {
	profile: StaffProfile | null
	templates: RoleTemplate[]
	drives: OpenDrive[]
	onSaved: (p: StaffProfile) => void
	onCancel: () => void
}) {
	const [draft, setDraft] = useState<ProfileDraft>(() =>
		profile ? draftFromProfile(profile) : emptyDraft(templates[0]?.key),
	)
	const [busy, setBusy] = useState(false)
	const [error, setError] = useState<string | null>(null)
	const set = (patch: Partial<ProfileDraft>) => setDraft((d) => ({ ...d, ...patch }))
	const headingId = profile ? `company-profile-edit-${profile.id}` : "company-profile-new"

	async function save(e: React.FormEvent) {
		e.preventDefault()
		if (busy) return
		const problem = draftError(draft)
		if (problem) {
			setError(problem)
			return
		}
		setBusy(true)
		setError(null)
		try {
			const input = toInput(draft)
			onSaved(profile ? await companyProfilesApi.update(profile.id, input) : await companyProfilesApi.create(input))
		} catch (err) {
			setError(describeError(err, "Could not save the profile."))
		} finally {
			setBusy(false)
		}
	}

	return (
		<form
			onSubmit={save}
			aria-labelledby={headingId}
			className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6 dark:border-white/10 dark:bg-slate-900"
		>
			<h2 id={headingId} className="text-lg font-semibold">
				{profile ? `Edit ${profile.name}` : "New company profile"}
			</h2>
			<p className="text-xs text-slate-500 dark:text-slate-400">
				Students see this as information from your institute, never as verified company facts. It only changes
				which skills practice questions focus on; scoring is unchanged.
			</p>
			<div className="grid gap-4 sm:grid-cols-2">
				<label className={label}>
					Company name
					<input value={draft.name} maxLength={LIMITS.name} onChange={(e) => set({ name: e.target.value })} className={field} required />
				</label>
				<label className={label}>
					Role title
					<input value={draft.roleTitle} maxLength={LIMITS.roleTitle} onChange={(e) => set({ roleTitle: e.target.value })} className={field} required />
				</label>
			</div>
			<label className={label}>
				Role template
				<select value={draft.templateKey} onChange={(e) => set({ templateKey: e.target.value })} className={field}>
					{templates.map((t) => (
						<option key={t.key} value={t.key}>
							{t.label}
						</option>
					))}
				</select>
			</label>
			<label className={label}>
				Summary for students <span className="text-xs font-normal text-slate-400">(optional)</span>
				<textarea rows={3} value={draft.summary} maxLength={LIMITS.summary} onChange={(e) => set({ summary: e.target.value })} className={field} />
			</label>
			<label className={label}>
				Job description <span className="text-xs font-normal text-slate-400">(optional; used to pick question topics)</span>
				<textarea rows={5} value={draft.jobDescription} maxLength={LIMITS.jobDescription} onChange={(e) => set({ jobDescription: e.target.value })} className={field} />
			</label>
			<div className="grid gap-4 sm:grid-cols-2">
				<label className={label}>
					Focus skills <span className="text-xs font-normal text-slate-400">(comma separated, up to 20)</span>
					<input value={draft.focusSkills} onChange={(e) => set({ focusSkills: e.target.value })} className={field} />
				</label>
				<label className={label}>
					Competencies <span className="text-xs font-normal text-slate-400">(comma separated, up to 8)</span>
					<input value={draft.competencies} onChange={(e) => set({ competencies: e.target.value })} className={field} />
				</label>
			</div>
			<SourcesEditor sources={draft.sources} drives={drives} onChange={(sources) => set({ sources })} disabled={busy} />
			{error ? (
				<p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
					{error}
				</p>
			) : null}
			<div className="flex flex-wrap gap-2">
				<button
					type="submit"
					disabled={busy}
					className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
				>
					{busy ? <Spinner /> : null}
					Save
				</button>
				<button type="button" onClick={onCancel} className="min-h-[44px] rounded-lg border border-slate-300 px-4 text-sm dark:border-white/20">
					Cancel
				</button>
			</div>
		</form>
	)
}
