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
import { MOCK_INTERVIEW_CODING_BUILD_ENABLED, MOCK_INTERVIEW_VIDEO_AVAILABLE } from "./mockInterviewFlags"
import RemainingQuota from "./RemainingQuota"
import { visibleModes } from "./mockInterviewModes"
import CodingSetupFields, { type CodingChoices } from "./coding/CodingSetupFields"
import { visibleInterviewTypes } from "./coding/codingFlow"
import CompanyContextPicker from "./company/CompanyContextPicker"
import { NO_COMPANY, companyCreateFields, type CompanyChoice } from "./company/companyFlow"

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
	const [mode, setMode] = useState("text")
	const [role, setRole] = useState("")
	const [company, setCompany] = useState("")
	const [resumeId, setResumeId] = useState("")
	const [jd, setJd] = useState("")
	// Phase 8.2: coding-round choices (only used when the type is "coding").
	const [codingChoices, setCodingChoices] = useState<CodingChoices>({
		languages: ["python313", "cpp17", "java21", "c11"],
		topics: [],
		problemKey: "",
		duration: "45",
	})
	// Phase 8.4: optional company profile / role template (hidden while the server says 404).
	const [companyChoice, setCompanyChoice] = useState<CompanyChoice>(NO_COMPANY)
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
	// Phase 6.1: "video" is dropped until the build flag is on and the video UI exists.
	const modes = visibleModes(opts.modes, MOCK_INTERVIEW_VIDEO_AVAILABLE)
	const voiceOffered = modes.some((m) => m.value === "voice")
	const voice = voiceOffered && mode === "voice"
	// Phase 6.3: video is in `modes` only when the server offers it AND this build has the video UI.
	const videoOffered = modes.some((m) => m.value === "video")
	const video = videoOffered && mode === "video"
	const modeChoice = voiceOffered || videoOffered
	// Phase 8.2: "coding" only when the server offers it AND this build has the coding UI.
	const types = visibleInterviewTypes(opts.interview_types, MOCK_INTERVIEW_CODING_BUILD_ENABLED)
	const coding = type === "coding" && types.some((t) => t.value === "coding")

	async function onSubmit(e: React.FormEvent) {
		e.preventDefault()
		if (submitting) return
		setError(null)
		if (role.trim().length < 2) {
			setError("Enter the job role you are preparing for.")
			return
		}
		if (coding && codingChoices.languages.length === 0) {
			setError("Choose at least one programming language.")
			return
		}
		setSubmitting(true)
		try {
			// Phase 8.2: a coding round is text-only and sends only its own fields.
			const created = coding
				? await mockInterviewApi.create({
						interview_type: "coding",
						difficulty,
						duration_minutes: Number(codingChoices.duration),
						target_role: role.trim(),
						language: "en",
						coding: {
							languages: codingChoices.languages,
							topics: codingChoices.topics,
							...(codingChoices.problemKey ? { problem_key: codingChoices.problemKey } : {}),
						},
					})
				: await mockInterviewApi.create({
				interview_type: type,
				difficulty,
				duration_minutes: Number(duration),
				target_role: role.trim(),
				company_name: company.trim() || undefined,
				resume_id: resumeId ? Number(resumeId) : undefined,
				job_description: jd.trim() || undefined,
				language,
				...(voice ? { mode: "voice" as const } : video ? { mode: "video" as const } : {}),
				// Phase 8.4: nothing is added when no company focus was chosen.
				...companyCreateFields(companyChoice),
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
			<RemainingQuota />

			<ChoiceGroup legend="Interview type" name="type" options={types} value={type} onChange={setType} />
			{coding ? (
				<p className="text-xs text-slate-500 dark:text-slate-400">
					A timed coding round: solve problems in an editor and submit your code. Review-only for now: your code
					is saved but not run, and there is no score yet.
				</p>
			) : null}
			{modeChoice && !coding ? (
				<div>
					<ChoiceGroup legend="Answer by" name="mode" options={modes} value={mode} onChange={setMode} />
					{voice ? (
						<p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
							The interviewer reads questions aloud and you answer by speaking (Chrome or Edge
							recommended; you can always type). Speech is converted to text by your browser, and some
							browsers send audio to their speech service to do this. Campus AI stores only the text you
							submit, never audio; unsent text stays in this browser tab until you submit it.
						</p>
					) : null}
					{video ? (
						<p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
							A video practice interview: you see yourself on camera while the interviewer reads each
							question aloud, and you answer by speaking or typing. Your camera is only a preview on this
							screen. It is never recorded, uploaded or analysed. Campus AI stores only the answer text you
							submit.
						</p>
					) : null}
				</div>
			) : null}
			<ChoiceGroup legend="Difficulty" name="difficulty" options={opts.difficulties} value={difficulty} onChange={setDifficulty} />
			{coding ? (
				<CodingSetupFields value={codingChoices} onChange={setCodingChoices} difficulty={difficulty} />
			) : null}
			<div className={coding ? "hidden" : "grid gap-5 sm:grid-cols-2"}>
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
				<div className={coding ? "hidden" : undefined}>
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

			<div className={coding ? "hidden" : undefined}>
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

			<div className={coding ? "hidden" : undefined}>
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

			{coding ? null : (
				<CompanyContextPicker value={companyChoice} onChange={setCompanyChoice} jobDescription={jd} />
			)}

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
