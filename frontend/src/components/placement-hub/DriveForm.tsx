"use client"

/**
 * Drive editor (Phase 9.2): the drive's own fields (company, role, legacy
 * verified-data criteria) and its placement settings (window, academic year,
 * graduation years, departments, 10th/12th %, backlogs, one-offer policy).
 * Client checks are friendly hints; the server validates everything again.
 */

import { useState } from "react"
import type { Department, DriveCore, DriveFields, DriveSettings, SettingsInput } from "./placementApi"
import { buttonClass, Field, Flash, inputClass } from "./HubUi"
import { isoToLocalInput, localInputToIso, optionalNumber, parseYears } from "./placementLogic"

type Form = {
	company_name: string
	role_title: string
	description: string
	location: string
	package_lpa: string
	min_cgpa: string
	min_attendance: string
	min_verified_projects: string
	required_skills: string
	open_at: string
	close_at: string
	academic_year: string
	graduation_years: string
	department_ids: number[]
	min_tenth: string
	min_twelfth: string
	max_backlogs: string
	exclude_placed: boolean
	// Legacy drive deadline: not edited here, but kept (PUT is a full replacement).
	deadline: string | null
}

function initial(drive?: DriveCore | null, s?: DriveSettings | null): Form {
	return {
		company_name: drive?.company_name ?? "",
		role_title: drive?.role_title ?? "",
		description: drive?.description ?? "",
		location: drive?.location ?? "",
		package_lpa: drive?.package_lpa != null ? String(drive.package_lpa) : "",
		min_cgpa: String(drive?.min_cgpa ?? 0),
		min_attendance: String(drive?.min_attendance ?? 0),
		min_verified_projects: String(drive?.min_verified_projects ?? 0),
		required_skills: drive?.required_skills ?? "",
		open_at: isoToLocalInput(s?.applications_open_at),
		close_at: isoToLocalInput(s?.applications_close_at),
		academic_year: s?.academic_year ?? "",
		graduation_years: (s?.graduation_years ?? []).join(", "),
		department_ids: s?.department_ids ?? [],
		min_tenth: s?.min_tenth_percent != null ? String(s.min_tenth_percent) : "",
		min_twelfth: s?.min_twelfth_percent != null ? String(s.min_twelfth_percent) : "",
		max_backlogs: s?.max_active_backlogs != null ? String(s.max_active_backlogs) : "",
		exclude_placed: s?.exclude_placed ?? false,
		deadline: drive?.deadline ?? null,
	}
}

export type DrivePayload = { fields: DriveFields; settings: SettingsInput }

function build(f: Form): { payload?: DrivePayload; problem?: string } {
	if (f.company_name.trim().length < 1 || f.role_title.trim().length < 1)
		return { problem: "Company and role title are required." }
	const years = parseYears(f.graduation_years)
	if (years === null) return { problem: "Graduation years must be up to six 4-digit years, e.g. 2027, 2028." }
	if (f.academic_year && !/^\d{4}-\d{2}$/.test(f.academic_year.trim()))
		return { problem: "Academic year must look like 2026-27." }
	const open = localInputToIso(f.open_at)
	const close = localInputToIso(f.close_at)
	if (open && close && open >= close) return { problem: "Applications must open before they close." }
	const fields: DriveFields = {
		company_name: f.company_name.trim(),
		role_title: f.role_title.trim(),
		description: f.description.trim() || null,
		location: f.location.trim() || null,
		package_lpa: optionalNumber(f.package_lpa),
		min_cgpa: optionalNumber(f.min_cgpa) ?? 0,
		min_attendance: optionalNumber(f.min_attendance) ?? 0,
		min_verified_projects: optionalNumber(f.min_verified_projects) ?? 0,
		required_skills: f.required_skills.trim() || null,
		deadline: f.deadline,
	}
	const settings: SettingsInput = {
		applications_open_at: open,
		applications_close_at: close,
		academic_year: f.academic_year.trim() || null,
		graduation_years: years,
		department_ids: f.department_ids,
		min_tenth_percent: optionalNumber(f.min_tenth),
		min_twelfth_percent: optionalNumber(f.min_twelfth),
		max_active_backlogs: optionalNumber(f.max_backlogs),
		exclude_placed: f.exclude_placed,
	}
	return { payload: { fields, settings } }
}

export default function DriveForm({
	drive,
	settings,
	departments,
	submitLabel,
	onSubmit,
	readOnlyFields = false,
}: {
	drive?: DriveCore | null
	settings?: DriveSettings | null
	departments: Department[]
	submitLabel: string
	onSubmit: (p: DrivePayload) => Promise<void>
	readOnlyFields?: boolean
}) {
	const [f, setF] = useState<Form>(() => initial(drive, settings))
	const [busy, setBusy] = useState(false)
	const [problem, setProblem] = useState<string | null>(null)
	const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((prev) => ({ ...prev, [k]: v }))

	async function submit(e: React.FormEvent) {
		e.preventDefault()
		const { payload, problem: p } = build(f)
		if (!payload) {
			setProblem(p ?? "Check the form.")
			return
		}
		setProblem(null)
		setBusy(true)
		try {
			await onSubmit(payload)
		} catch (err) {
			setProblem(err instanceof Error ? err.message : "Could not save.")
		} finally {
			setBusy(false)
		}
	}

	const text = (id: keyof Form, label: string, opts: { type?: string; hint?: string; required?: boolean; step?: string } = {}) => (
		<Field id={`df-${id}`} label={label + (opts.required ? " *" : "")} hint={opts.hint}>
			<input
				id={`df-${id}`}
				type={opts.type ?? "text"}
				step={opts.step}
				required={opts.required}
				disabled={readOnlyFields}
				aria-describedby={opts.hint ? `df-${id}-hint` : undefined}
				value={f[id] as string}
				onChange={(e) => set(id, e.target.value as never)}
				className={inputClass}
			/>
		</Field>
	)

	return (
		<form onSubmit={submit} className="space-y-6" noValidate>
			<fieldset className="grid gap-4 sm:grid-cols-2">
				<legend className="mb-2 text-sm font-semibold">Job details</legend>
				{text("company_name", "Company", { required: true })}
				{text("role_title", "Role title", { required: true })}
				{text("location", "Location")}
				{text("package_lpa", "Package (LPA)", { type: "number", step: "0.1" })}
				<div className="sm:col-span-2">
					<Field id="df-description" label="Description">
						<textarea
							id="df-description"
							rows={3}
							disabled={readOnlyFields}
							value={f.description}
							onChange={(e) => set("description", e.target.value)}
							className={inputClass}
						/>
					</Field>
				</div>
			</fieldset>

			<fieldset className="grid gap-4 sm:grid-cols-3">
				<legend className="mb-2 text-sm font-semibold">Verified-data criteria</legend>
				{text("min_cgpa", "Minimum CGPA", { type: "number", step: "0.1" })}
				{text("min_attendance", "Minimum attendance %", { type: "number", step: "1" })}
				{text("min_verified_projects", "Minimum verified projects", { type: "number", step: "1" })}
				<div className="sm:col-span-3">
					{text("required_skills", "Required verified skills", { hint: "Comma-separated, e.g. SQL, Python" })}
				</div>
			</fieldset>

			<fieldset className="grid gap-4 sm:grid-cols-3">
				<legend className="mb-2 text-sm font-semibold">Academic criteria and window</legend>
				{text("open_at", "Applications open", { type: "datetime-local", hint: "Your local time. Optional." })}
				{text("close_at", "Applications close", { type: "datetime-local", hint: "Your local time. Optional." })}
				{text("academic_year", "Academic year", { hint: "e.g. 2026-27" })}
				{text("graduation_years", "Graduation years", { hint: "Empty = any. e.g. 2027, 2028" })}
				{text("min_tenth", "Minimum 10th %", { type: "number", step: "0.1" })}
				{text("min_twelfth", "Minimum 12th / diploma %", { type: "number", step: "0.1" })}
				{text("max_backlogs", "Maximum active backlogs", { type: "number", step: "1" })}
				<div className="flex items-end">
					<label className="flex items-center gap-2 text-sm">
						<input
							type="checkbox"
							checked={f.exclude_placed}
							onChange={(e) => set("exclude_placed", e.target.checked)}
							className="h-4 w-4"
						/>
						Exclude students already placed
					</label>
				</div>
				<div className="sm:col-span-3">
					<p className="text-sm text-slate-700 dark:text-slate-300" id="df-depts-label">
						Departments (none selected = all departments)
					</p>
					{departments.length === 0 ? (
						<p className="mt-1 text-xs text-slate-500">No departments are set up for your institute.</p>
					) : (
						<div role="group" aria-labelledby="df-depts-label" className="mt-2 flex flex-wrap gap-3">
							{departments.map((d) => (
								<label key={d.id} className="flex items-center gap-2 text-sm">
									<input
										type="checkbox"
										className="h-4 w-4"
										checked={f.department_ids.includes(d.id)}
										onChange={(e) =>
											set(
												"department_ids",
												e.target.checked
													? [...f.department_ids, d.id]
													: f.department_ids.filter((x) => x !== d.id),
											)
										}
									/>
									{d.name}
								</label>
							))}
						</div>
					)}
				</div>
			</fieldset>

			{problem ? <Flash kind="error" text={problem} /> : null}
			<button type="submit" className={buttonClass} disabled={busy}>
				{busy ? "Saving…" : submitLabel}
			</button>
		</form>
	)
}
