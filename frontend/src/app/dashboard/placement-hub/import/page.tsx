/**
 * Campus AI - Placement Hub: academic spreadsheet import (Phase 9.2).
 *
 * 1. Download the template. 2. Upload .csv or .xlsx (2 MB, 5 000 rows).
 * 3. The SERVER parses and validates it and returns a preview with row-level
 * errors and counts; nothing is saved. 4. Confirm: the same file is sent again
 * with the preview's SHA-256; the server re-validates and saves every valid
 * row in one transaction. Blank cells never overwrite stored values.
 *
 * Backend: GET /placement/import/template, POST /placement/import/preview,
 * POST /placement/import/commit (TPO/admin; audited with counts only).
 */

"use client"

import { useRef, useState } from "react"
import {
	buttonClass,
	cardClass,
	Flash,
	HubFrame,
	secondaryButtonClass,
	StaffNav,
} from "@/components/placement-hub/HubUi"
import {
	downloadBlob,
	errorText,
	placementApi,
	type ImportPreview,
	type ImportSummary,
} from "@/components/placement-hub/placementApi"
import { IMPORT_COLUMNS, importFileProblem } from "@/components/placement-hub/placementLogic"

const ACTION_LABEL = { create: "New record", update: "Update", unchanged: "No change", error: "Error" } as const

function Importer() {
	const inputRef = useRef<HTMLInputElement>(null)
	const [file, setFile] = useState<File | null>(null)
	const [preview, setPreview] = useState<ImportPreview | null>(null)
	const [summary, setSummary] = useState<ImportSummary | null>(null)
	const [busy, setBusy] = useState<"" | "template" | "preview" | "commit">("")
	const [err, setErr] = useState<string | null>(null)
	const [onlyErrors, setOnlyErrors] = useState(false)

	async function template() {
		setBusy("template")
		setErr(null)
		try {
			downloadBlob(await placementApi.importTemplate(), "placement_academic_template.csv")
		} catch (e) {
			setErr(errorText(e))
		} finally {
			setBusy("")
		}
	}

	async function runPreview() {
		const problem = importFileProblem(file)
		if (problem || !file) {
			setErr(problem)
			return
		}
		setBusy("preview")
		setErr(null)
		setSummary(null)
		try {
			setPreview(await placementApi.importPreview(file))
		} catch (e) {
			setPreview(null)
			setErr(errorText(e))
		} finally {
			setBusy("")
		}
	}

	async function commit() {
		if (!file || !preview) return
		setBusy("commit")
		setErr(null)
		try {
			setSummary(await placementApi.importCommit(file, preview.file_sha256))
			setPreview(null)
			setFile(null)
			if (inputRef.current) inputRef.current.value = ""
		} catch (e) {
			setErr(errorText(e))
		} finally {
			setBusy("")
		}
	}

	const rows = preview ? (onlyErrors ? preview.rows.filter((r) => r.action === "error") : preview.rows) : []
	const changes = preview ? preview.to_create + preview.to_update : 0

	return (
		<div className="space-y-6">
			<div>
				<h2 className="text-2xl font-bold">Import academic records</h2>
				<p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
					Upload roll numbers, graduation years, 10th/12th/diploma percentages and active backlogs for many students at
					once. Nothing is saved until you confirm the preview.
				</p>
			</div>

			<section className={cardClass} aria-labelledby="step1">
				<h3 id="step1" className="font-semibold">
					1. Prepare the file
				</h3>
				<p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
					Columns: <code className="text-xs">{IMPORT_COLUMNS.join(", ")}</code>. Students are matched by email (or by an
					existing roll number). Leave a cell blank to keep the stored value. Placement status and verified data (CGPA,
					attendance, skills, projects) cannot be imported.
				</p>
				<button type="button" className={`${secondaryButtonClass} mt-3`} disabled={busy !== ""} onClick={template}>
					{busy === "template" ? "Downloading…" : "Download template (.csv)"}
				</button>
			</section>

			<section className={cardClass} aria-labelledby="step2">
				<h3 id="step2" className="font-semibold">
					2. Upload and preview
				</h3>
				<label htmlFor="imp-file" className="mt-2 block text-sm">
					Spreadsheet (.csv or .xlsx, up to 2 MB)
				</label>
				<input
					ref={inputRef}
					id="imp-file"
					type="file"
					accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
					className="mt-1 block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-2 dark:file:bg-white/10"
					onChange={(e) => {
						setFile(e.target.files?.[0] ?? null)
						setPreview(null)
						setSummary(null)
						setErr(null)
					}}
				/>
				<button type="button" className={`${buttonClass} mt-3`} disabled={!file || busy !== ""} onClick={runPreview}>
					{busy === "preview" ? "Checking…" : "Preview"}
				</button>
			</section>

			{err ? <Flash kind="error" text={err} /> : null}

			{summary ? (
				<section className={cardClass} aria-live="polite" aria-labelledby="sum-h">
					<h3 id="sum-h" className="font-semibold">
						Import complete
					</h3>
					<p className="mt-2 text-sm">
						{summary.created} new, {summary.updated} updated, {summary.unchanged} unchanged, {summary.invalid} rows with
						errors were skipped (of {summary.total_rows}). Reference: <code className="text-xs">{summary.file_sha256.slice(0, 12)}</code>
					</p>
				</section>
			) : null}

			{preview ? (
				<section className={cardClass} aria-labelledby="step3">
					<h3 id="step3" className="font-semibold">
						3. Review and confirm
					</h3>
					<ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-sm" aria-live="polite">
						<li>Rows: {preview.total_rows}</li>
						<li className="text-emerald-700 dark:text-emerald-300">Valid: {preview.valid_rows}</li>
						<li className="text-red-700 dark:text-red-300">With errors: {preview.invalid_rows}</li>
						<li>New: {preview.to_create}</li>
						<li>Updates: {preview.to_update}</li>
						<li>No change: {preview.unchanged}</li>
					</ul>
					{preview.ignored_columns.length ? (
						<p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
							Ignored columns: {preview.ignored_columns.join(", ")}
						</p>
					) : null}
					<label className="mt-3 flex items-center gap-2 text-sm">
						<input type="checkbox" className="h-4 w-4" checked={onlyErrors} onChange={(e) => setOnlyErrors(e.target.checked)} />
						Show only rows with errors
					</label>
					<div className="mt-3 max-h-[28rem] overflow-auto relative [contain:inline-size] rounded-xl border border-slate-200 dark:border-white/10">
						<table className="min-w-full text-left text-sm">
							<caption className="sr-only">Import preview</caption>
							<thead className="sticky top-0 bg-slate-50 text-xs uppercase text-slate-600 dark:bg-slate-800 dark:text-slate-300">
								<tr>
									<th scope="col" className="px-3 py-2">Row</th>
									<th scope="col" className="px-3 py-2">Student</th>
									<th scope="col" className="px-3 py-2">Result</th>
									<th scope="col" className="px-3 py-2">Details</th>
								</tr>
							</thead>
							<tbody className="divide-y divide-slate-200 dark:divide-white/10">
								{rows.map((r) => (
									<tr key={r.row} className={r.action === "error" ? "bg-red-50/60 dark:bg-red-500/5" : ""}>
										<td className="px-3 py-2">{r.row}</td>
										<td className="px-3 py-2">
											<div>{r.student_name ?? "—"}</div>
											<div className="text-xs text-slate-500 dark:text-slate-400">{r.email ?? r.roll_number ?? ""}</div>
										</td>
										<td className="px-3 py-2">{ACTION_LABEL[r.action]}</td>
										<td className="px-3 py-2 text-xs">
											{r.errors.length
												? r.errors.join("; ")
												: r.changed_fields.length
													? `Changes: ${r.changed_fields.join(", ")}`
													: "—"}
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
					<div className="mt-4 flex flex-wrap gap-2">
						<button type="button" className={buttonClass} disabled={busy !== "" || changes === 0} onClick={commit}>
							{busy === "commit" ? "Saving…" : `Save ${changes} valid record${changes === 1 ? "" : "s"}`}
						</button>
						<button
							type="button"
							className={secondaryButtonClass}
							onClick={() => {
								setPreview(null)
							}}
						>
							Cancel
						</button>
					</div>
					{preview.invalid_rows ? (
						<p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
							Rows with errors are skipped. Fix them in the file and import it again later.
						</p>
					) : null}
				</section>
			) : null}
		</div>
	)
}

export default function PlacementImportPage() {
	return (
		<HubFrame title="Import academic records" roles={["tpo", "admin"]}>
			{(role) => (
				<>
					<StaffNav role={role} />
					<Importer />
				</>
			)}
		</HubFrame>
	)
}
