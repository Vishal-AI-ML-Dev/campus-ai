"use client"

/**
 * Interview setup: type, difficulty, duration, role, company, resume, JD and
 * language. Choices come from GET /mock-interview/options; the backend
 * validates everything again.
 */

import { useState } from "react"
import { useApiResource } from "@/lib/useApiResource"
import { ErrorState, LoadingState, Spinner } from "@/components/ui/states"
import {
	describeError,
	mockInterviewApi,
	type InterviewOptions,
	type InterviewSession,
} from "./mockInterviewApi"

const inputClass =
	"w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-400/30 dark:border-white/10 dark:bg-slate-900"
const labelClass = "block text-sm font-medium text-slate-700 dark:text-slate-200"

function ChoiceGroup({
	legend,
	name,
	options,
	value,
	onChange,
}: {
	legend: string
	name: string
	options: { value: string; label: string }[]
	value: string
	onChange: (v: string) => void
}) {
	return (
		<fieldset>
			<legend className={labelClass}>{legend}</legend>
			<div className="mt-2 flex flex-wrap gap-2">
				{options.map((o) => {
					const id = `${name}-${o.value}`
					const checked = value === o.value
					return (
						<label
							key={o.value}
							htmlFor={id}
							className={`cursor-pointer rounded-lg border px-3 py-1.5 text-sm transition focus-within:ring-2 focus-within:ring-indigo-400/40 ${
								checked
									? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
									: "border-slate-200 text-slate-600 hover:border-indigo-300 dark:border-white/10 dark:text-slate-300"
							}`}
						>
							<input
								id={id}
								type="radio"
								name={name}
								value={o.value}
								checked={checked}
								onChange={() => onChange(o.value)}
								className="sr-only"
							/>
							{o.label}
						</label>
					)
				})}
			</div>
		</fieldset>
	)
}

export default function InterviewSetupForm({
	onCreated,
	onConflict,
}: {
	onCreated: (s: InterviewSession) => void
	/** Another interview is already open (e.g. started in another tab). */
	onConflict: () => void
}) {
	const options = useApiResource<InterviewOptions>("/mock-interview/options", {
		fallbackMessage: "Could not load interview options.",
	})
	const [type, setType] = useState("hr")
	const [difficulty, setDifficulty] = useState("medium")
	const [duration, setDuration] = useState("30")
	const [language, setLanguage] = useState("en")
	const [role, setRole] = useState("")
	const [company, setCompany] = useState("")
	const [resumeId, setResumeId] = useState("")
	const [jd, setJd] = useState("")
	const [submitting, setSubmitting] = useState(false)
	const [error, setError] = useState<string | null>(null)

	if (options.loading) return <LoadingState message="Loading interview options…" />
	if (options.error || !options.data)
		return (
			<ErrorState
				message={options.error ?? "Could not load interview options."}
				onRetry={options.reload}
				requestId={options.requestId}
			/>
		)
	const opts = options.data
	const jdMax = opts.job_description_max_chars

	async function onSubmit(e: React.FormEvent) {
		e.preventDefault()
		if (submitting) return
		setError(null)
		if (role.trim().length < 2) {
			setError("Enter the job role you are preparing for.")
			return
		}
		setSubmitting(true)
		try {
			const created = await mockInterviewApi.create({
				interview_type: type,
				difficulty,
				duration_minutes: Number(duration),
				target_role: role.trim(),
				company_name: company.trim() || undefined,
				resume_id: resumeId ? Number(resumeId) : undefined,
				job_description: jd.trim() || undefined,
				language,
			})
			onCreated(created)
		} catch (err) {
			if (err && typeof err === "object" && "status" in err && err.status === 409) {
				onConflict()
				return
			}
			setError(describeError(err, "Could not create the interview."))
		} finally {
			setSubmitting(false)
		}
	}

	return (
		<form
			onSubmit={onSubmit}
			className="space-y-5 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6 dark:border-white/10 dark:bg-slate-900"
			aria-describedby={error ? "setup-error" : undefined}
		>
			<div>
				<h2 className="text-lg font-semibold">Set up your practice interview</h2>
				<p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
					The AI interviewer asks one question at a time and follows up on your
					answers. Questions use only your own resume and the job description you
					paste.
				</p>
			</div>

			<ChoiceGroup legend="Interview type" name="type" options={opts.interview_types} value={type} onChange={setType} />
			<ChoiceGroup legend="Difficulty" name="difficulty" options={opts.difficulties} value={difficulty} onChange={setDifficulty} />
			<div className="grid gap-5 sm:grid-cols-2">
				<ChoiceGroup
					legend="Duration"
					name="duration"
					options={opts.durations_minutes.map((d) => ({ value: String(d), label: `${d} min` }))}
					value={duration}
					onChange={setDuration}
				/>
				<ChoiceGroup legend="Language" name="language" options={opts.languages} value={language} onChange={setLanguage} />
			</div>

			<div className="grid gap-4 sm:grid-cols-2">
				<div>
					<label htmlFor="mi-role" className={labelClass}>
						Job role <span className="text-red-500" aria-hidden="true">*</span>
					</label>
					<input
						id="mi-role"
						required
						maxLength={150}
						value={role}
						onChange={(e) => setRole(e.target.value)}
						placeholder="e.g. Software Engineer, Data Analyst"
						className={`mt-1 ${inputClass}`}
					/>
				</div>
				<div>
					<label htmlFor="mi-company" className={labelClass}>
						Company <span className="text-xs font-normal text-slate-400">(optional)</span>
					</label>
					<input
						id="mi-company"
						maxLength={150}
						value={company}
						onChange={(e) => setCompany(e.target.value)}
						placeholder="e.g. TCS, Infosys, a startup"
						className={`mt-1 ${inputClass}`}
					/>
				</div>
			</div>

			<div>
				<label htmlFor="mi-resume" className={labelClass}>
					Resume <span className="text-xs font-normal text-slate-400">(optional)</span>
				</label>
				<select
					id="mi-resume"
					value={resumeId}
					onChange={(e) => setResumeId(e.target.value)}
					className={`mt-1 ${inputClass}`}
				>
					<option value="">Don&apos;t use a resume</option>
					{opts.resumes.map((r) => (
						<option key={r.id} value={r.id}>
							{r.title}
							{r.is_primary ? " (primary)" : ""}
							{r.target_role ? ` - ${r.target_role}` : ""}
						</option>
					))}
				</select>
				{opts.resumes.length === 0 ? (
					<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
						No saved resumes yet. You can create one in the Resume section.
					</p>
				) : null}
			</div>

			<div>
				<label htmlFor="mi-jd" className={labelClass}>
					Job description <span className="text-xs font-normal text-slate-400">(optional)</span>
				</label>
				<textarea
					id="mi-jd"
					rows={5}
					maxLength={jdMax}
					value={jd}
					onChange={(e) => setJd(e.target.value)}
					placeholder="Paste the job description to get questions on its requirements."
					className={`mt-1 ${inputClass}`}
					aria-describedby="mi-jd-count"
				/>
				<p id="mi-jd-count" className="mt-1 text-right text-xs text-slate-400">
					{jd.length.toLocaleString()} / {jdMax.toLocaleString()}
				</p>
			</div>

			{error ? (
				<p id="setup-error" role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">
					{error}
				</p>
			) : null}

			<button
				type="submit"
				disabled={submitting}
				className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50 sm:w-auto"
			>
				{submitting ? <Spinner /> : null}
				Continue
			</button>
		</form>
	)
}
