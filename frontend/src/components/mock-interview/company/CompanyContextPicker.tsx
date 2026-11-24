"use client"

/**
 * Phase 8.4: optional company profile / role template for an HR or technical
 * interview, with the transparency panel (P8-D10, P8-D11) and the optional
 * skill map from a job description.
 *
 * Shows nothing while the server answers 404 (company profiles off for the
 * institute). Only PUBLISHED profiles of the student's own institute are
 * listed by the server. Everything is plain text; source URLs are never links.
 */

import { useEffect, useState } from "react"
import { useApiResource } from "@/lib/useApiResource"
import { Spinner } from "@/components/ui/states"
import { describeError } from "../mockInterviewApi"
import {
	companyApi,
	type CompanyProfile,
	type CompanyProfileBrief,
	type JDSkills,
	type RoleTemplate,
} from "./companyApi"
import { EVALUATION_NOTE, choiceValue, parseChoice, sourceText, type CompanyChoice } from "./companyFlow"

const selectClass =
	"mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-slate-900"

export default function CompanyContextPicker({
	value,
	onChange,
	jobDescription,
}: {
	value: CompanyChoice
	onChange: (c: CompanyChoice) => void
	/** The JD the student pasted in the setup form (for the skill map). */
	jobDescription: string
}) {
	const profiles = useApiResource<CompanyProfileBrief[]>(companyApi.profilesPath)
	const templates = useApiResource<RoleTemplate[]>(companyApi.templatesPath)
	const [detail, setDetail] = useState<CompanyProfile | null>(null)
	const [skills, setSkills] = useState<JDSkills | null>(null)
	const [busy, setBusy] = useState(false)
	const [error, setError] = useState<string | null>(null)
	const profileId = value.profileId

	useEffect(() => {
		let cancelled = false
		setDetail(null)
		setSkills(null)
		if (profileId === null) return
		companyApi
			.profile(profileId)
			.then((p) => {
				if (!cancelled) setDetail(p)
			})
			.catch(() => {
				if (!cancelled) setError("Could not load this company profile.")
			})
		return () => {
			cancelled = true
		}
	}, [profileId])

	// Hidden entirely when the feature is off (404) or still loading.
	if (profiles.error || templates.error || !profiles.data || !templates.data) return null
	if (!profiles.data.length && !templates.data.length) return null
	const template = templates.data.find((t) => t.key === value.templateKey) ?? null

	async function mapSkills() {
		if (busy) return
		setBusy(true)
		setError(null)
		try {
			const body =
				jobDescription.trim().length > 0
					? { job_description: jobDescription.trim() }
					: profileId !== null
						? { profile_id: profileId }
						: null
			if (!body) {
				setError("Paste a job description below, or choose a company profile that has one.")
				return
			}
			const result = await companyApi.jdSkills(body)
			setSkills(result)
			onChange({ ...value, focusSkills: result.focus_order })
		} catch (err) {
			setError(describeError(err, "Could not read the job description right now."))
		} finally {
			setBusy(false)
		}
	}

	return (
		<fieldset className="space-y-3 rounded-xl border border-slate-200 p-3 dark:border-white/10">
			<legend className="px-1 text-sm font-medium">
				Company or role focus <span className="text-xs font-normal text-slate-400">(optional)</span>
			</legend>
			<label htmlFor="mi-company-focus" className="sr-only">
				Company or role focus
			</label>
			<select
				id="mi-company-focus"
				value={choiceValue(value)}
				onChange={(e) => {
					setError(null)
					onChange(parseChoice(e.target.value))
				}}
				className={selectClass}
			>
				<option value="">No company or role focus</option>
				{profiles.data.length ? (
					<optgroup label="Company profiles from your institute">
						{profiles.data.map((p) => (
							<option key={p.id} value={`p:${p.id}`}>
								{p.name} - {p.role_title}
							</option>
						))}
					</optgroup>
				) : null}
				<optgroup label="General role templates">
					{templates.data.map((t) => (
						<option key={t.key} value={`t:${t.key}`}>
							{t.label}
						</option>
					))}
				</optgroup>
			</select>

			{detail ? (
				<div className="space-y-2 text-sm" aria-live="polite">
					<p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
						{detail.attribution}
					</p>
					{detail.summary ? <p className="whitespace-pre-wrap">{detail.summary}</p> : null}
					{detail.focus_skills.length ? <p>Focus skills: {detail.focus_skills.join(", ")}</p> : null}
					{detail.competencies.length ? <p>Competencies: {detail.competencies.join(", ")}</p> : null}
					{detail.sources.length ? (
						<div>
							<p className="text-xs text-slate-500 dark:text-slate-400">Sources the institute recorded:</p>
							<ul className="list-disc pl-5 text-xs">
								{detail.sources.map((s, i) => (
									<li key={i}>{sourceText(s)}</li>
								))}
							</ul>
						</div>
					) : null}
					<p className="rounded-lg bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
						{detail.disclaimer} {EVALUATION_NOTE}
					</p>
				</div>
			) : template ? (
				<div className="space-y-1 text-sm">
					<p>{template.description}</p>
					<p className="text-xs text-slate-500 dark:text-slate-400">
						A general role template written for practice. It contains no company information. {EVALUATION_NOTE}
					</p>
				</div>
			) : null}

			{value.profileId !== null || value.templateKey ? (
				<div className="space-y-2">
					<button
						type="button"
						onClick={() => void mapSkills()}
						disabled={busy}
						className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-sm dark:border-white/20"
					>
						{busy ? <Spinner /> : null}
						Map skills from the job description
					</button>
					{skills ? (
						<div className="space-y-1 text-sm">
							<p className="text-xs text-slate-500 dark:text-slate-400">{skills.note}</p>
							<ul className="space-y-1">
								{skills.skills.map((s) => (
									<li key={s.skill} className="text-xs">
										<span className="font-medium">{s.skill}</span>{" "}
										<span className={s.matched ? "text-emerald-700 dark:text-emerald-300" : "text-slate-500"}>
											({s.matched ? "in your verified skills" : "to practise"})
										</span>
										<span className="block text-slate-500 dark:text-slate-400">&ldquo;{s.evidence}&rdquo;</span>
									</li>
								))}
							</ul>
							{skills.focus_order.length ? (
								<p className="text-xs">Questions will focus on: {skills.focus_order.slice(0, 8).join(", ")}</p>
							) : null}
						</div>
					) : null}
				</div>
			) : null}
			{error ? (
				<p role="alert" className="text-sm text-red-700 dark:text-red-300">
					{error}
				</p>
			) : null}
		</fieldset>
	)
}
