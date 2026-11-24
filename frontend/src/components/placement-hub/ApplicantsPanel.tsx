"use client"

/**
 * Applicants of one drive (Phase 9.2): current eligibility v2, filters,
 * search, pagination, single status changes (with the status the TPO saw, so
 * another staff member's change is never overwritten), history, and bulk
 * shortlist / reject with a server-side preview before anything is written.
 */

import { Fragment, useState } from "react"
import { EmptyState, ErrorState, LoadingState, SkeletonList } from "@/components/ui/states"
import { errorText, placementApi, type Applicant, type BulkResponse } from "./placementApi"
import {
	buttonClass,
	dangerButtonClass,
	Field,
	Flash,
	inputClass,
	Pager,
	secondaryButtonClass,
	StatusPill,
	YesNo,
} from "./HubUi"
import {
	APP_MOVES,
	APP_STATUSES,
	BULK_MAX,
	bulkCandidates,
	formatDateTime,
	noteRequired,
	parseStatusCounts,
	STATUS_LABEL,
	toggleId,
	type AppStatus,
	type BulkTarget,
} from "./placementLogic"
import { useAsync } from "./useAsync"

const LIMIT = 50

function History({ applicationId }: { applicationId: number }) {
	const res = useAsync(`h:${applicationId}`, () => placementApi.history(applicationId))
	if (res.loading && !res.data) return <LoadingState message="Loading history…" />
	if (res.error) return <ErrorState message={res.error} onRetry={res.reload} />
	if (!res.data?.length)
		return (
			<p className="text-xs text-slate-500">
				No recorded history. Applications made before placement management was enabled start their history at
				their next change.
			</p>
		)
	return (
		<ol className="space-y-1 text-xs">
			{res.data.map((e) => (
				<li key={e.id}>
					<span className="font-medium">{formatDateTime(e.created_at)}</span> {"—"}{" "}
					{e.from_status ? `${STATUS_LABEL[e.from_status]} → ` : ""}
					{STATUS_LABEL[e.to_status]} by {e.actor_role ?? "system"}
					{e.note ? ` (${e.note})` : ""}
				</li>
			))}
		</ol>
	)
}

function RowActions({ a, canWrite, onDone }: { a: Applicant; canWrite: boolean; onDone: (msg: string) => void }) {
	const [to, setTo] = useState<AppStatus | "">("")
	const [note, setNote] = useState("")
	const [busy, setBusy] = useState(false)
	const [err, setErr] = useState<string | null>(null)
	const moves = APP_MOVES[a.status]
	if (!canWrite || moves.length === 0) return null
	const needNote = noteRequired(a.status)

	async function save() {
		if (!to) return
		setBusy(true)
		setErr(null)
		try {
			await placementApi.setStatus(a.application_id, to, a.status, note.trim() || null)
			onDone(`${a.full_name}: ${STATUS_LABEL[a.status]} → ${STATUS_LABEL[to]}`)
		} catch (e) {
			setErr(errorText(e))
		} finally {
			setBusy(false)
		}
	}

	return (
		<div className="mt-2 flex flex-wrap items-end gap-2">
			<div>
				<label htmlFor={`mv-${a.application_id}`} className="sr-only">
					New status for {a.full_name}
				</label>
				<select
					id={`mv-${a.application_id}`}
					className={inputClass}
					value={to}
					onChange={(e) => setTo(e.target.value as AppStatus)}
				>
					<option value="">Change status…</option>
					{moves.map((m) => (
						<option key={m} value={m}>
							{STATUS_LABEL[m]}
						</option>
					))}
				</select>
			</div>
			<div className="min-w-40 flex-1">
				<label htmlFor={`nt-${a.application_id}`} className="sr-only">
					Note {needNote ? "(required)" : "(optional)"}
				</label>
				<input
					id={`nt-${a.application_id}`}
					className={inputClass}
					maxLength={500}
					placeholder={needNote ? "Note (required for a correction)" : "Note (optional, sent to the student)"}
					value={note}
					onChange={(e) => setNote(e.target.value)}
				/>
			</div>
			<button
				type="button"
				className={secondaryButtonClass}
				disabled={!to || busy || (needNote && !note.trim())}
				onClick={save}
			>
				{busy ? "Saving…" : "Save"}
			</button>
			{err ? (
				<div className="w-full">
					<Flash kind="error" text={err} />
				</div>
			) : null}
		</div>
	)
}

function BulkBar({
	driveId,
	rows,
	selected,
	onFinished,
}: {
	driveId: number
	rows: Applicant[]
	selected: Set<number>
	onFinished: (msg: string) => void
}) {
	const [to, setTo] = useState<BulkTarget>("shortlisted")
	const [note, setNote] = useState("")
	const [preview, setPreview] = useState<BulkResponse | null>(null)
	const [busy, setBusy] = useState(false)
	const [err, setErr] = useState<string | null>(null)
	const { valid, skipped } = bulkCandidates(rows, selected, to)
	// Only rows visible on this page are ever sent (with the status shown).
	const picked = rows.filter((r) => selected.has(r.application_id))
	const items = picked.map((r) => ({ application_id: r.application_id, expected_status: r.status }))
	const names = new Map(rows.map((r) => [r.application_id, r.full_name]))

	async function run(dryRun: boolean) {
		setBusy(true)
		setErr(null)
		try {
			const res = await placementApi.bulk(driveId, to, items, note.trim() || null, dryRun)
			if (dryRun) setPreview(res)
			else {
				setPreview(null)
				onFinished(
					`${res.succeeded} application${res.succeeded === 1 ? "" : "s"} moved to ${STATUS_LABEL[res.to_status]}` +
						(res.failed ? `; ${res.failed} not changed (see the preview reasons).` : "."),
				)
			}
		} catch (e) {
			setErr(errorText(e))
		} finally {
			setBusy(false)
		}
	}

	if (picked.length === 0) return null
	return (
		<section
			aria-label="Bulk action"
			className="sticky bottom-2 z-10 mt-4 rounded-xl border border-indigo-300 bg-white p-4 shadow-lg dark:border-indigo-500/40 dark:bg-slate-900"
		>
			<div className="flex flex-wrap items-end gap-3">
				<p className="text-sm font-medium">{picked.length} selected on this page</p>
				<Field id="bulk-to" label="Move to">
					<select
						id="bulk-to"
						className={inputClass}
						value={to}
						onChange={(e) => {
							setTo(e.target.value as BulkTarget)
							setPreview(null)
						}}
					>
						<option value="shortlisted">Shortlisted</option>
						<option value="rejected">Not selected</option>
					</select>
				</Field>
				<div className="min-w-48 flex-1">
					<Field id="bulk-note" label="Note (optional; required to re-shortlist a rejected candidate)">
						<input
							id="bulk-note"
							className={inputClass}
							maxLength={500}
							value={note}
							onChange={(e) => {
								setNote(e.target.value)
								setPreview(null)
							}}
						/>
					</Field>
				</div>
				<button
					type="button"
					className={secondaryButtonClass}
					disabled={busy || picked.length > BULK_MAX}
					onClick={() => run(true)}
				>
					Preview changes
				</button>
			</div>
			<p className="mt-2 text-xs text-slate-600 dark:text-slate-400">
				{valid.length} can move; {skipped.length} cannot from their current status.
				{picked.length > BULK_MAX ? ` Select at most ${BULK_MAX}.` : ""}
			</p>
			{err ? (
				<div className="mt-2">
					<Flash kind="error" text={err} />
				</div>
			) : null}
			{preview ? (
				<div className="mt-3 rounded-lg border border-slate-200 p-3 dark:border-white/10" aria-live="polite">
					<p className="text-sm font-medium">
						Preview: {preview.succeeded} will move to {STATUS_LABEL[preview.to_status]}, {preview.failed} will not.
					</p>
					{preview.failed ? (
						<ul className="mt-2 max-h-40 list-inside list-disc overflow-y-auto text-xs">
							{preview.results
								.filter((r) => !r.ok)
								.map((r) => (
									<li key={r.application_id}>
										{names.get(r.application_id) ?? `#${r.application_id}`}: {r.detail}
									</li>
								))}
						</ul>
					) : null}
					<div className="mt-3 flex gap-2">
						<button
							type="button"
							className={to === "rejected" ? dangerButtonClass : buttonClass}
							disabled={busy || preview.succeeded === 0}
							onClick={() => run(false)}
						>
							{busy ? "Applying…" : `Confirm ${preview.succeeded} change${preview.succeeded === 1 ? "" : "s"}`}
						</button>
						<button type="button" className={secondaryButtonClass} onClick={() => setPreview(null)}>
							Cancel
						</button>
					</div>
				</div>
			) : null}
		</section>
	)
}

export default function ApplicantsPanel({ driveId, canWrite }: { driveId: number; canWrite: boolean }) {
	const [status, setStatus] = useState("")
	const [eligible, setEligible] = useState("")
	const [q, setQ] = useState("")
	const [query, setQuery] = useState("")
	const [offset, setOffset] = useState(0)
	const [selected, setSelected] = useState<Set<number>>(new Set())
	const [open, setOpen] = useState<number | null>(null)
	const [flash, setFlash] = useState<string | null>(null)
	const key = JSON.stringify({ driveId, status, eligible, query, offset })
	const res = useAsync(key, () =>
		placementApi.applicants(driveId, { status, eligible, q: query, limit: LIMIT, offset }),
	)
	const rows = res.data?.items ?? []
	const counts = parseStatusCounts(res.data?.headers.get("X-Status-Counts") ?? null)
	const allOnPage = rows.length > 0 && rows.every((r) => selected.has(r.application_id))

	function done(msg: string) {
		setFlash(msg)
		setSelected(new Set())
		res.reload()
	}

	return (
		<div>
			<p className="text-sm text-slate-600 dark:text-slate-300" aria-live="polite">
				{APP_STATUSES.map((s) => `${STATUS_LABEL[s]} ${counts[s] ?? 0}`).join(" · ")}
			</p>
			<form
				role="search"
				className="mt-3 grid gap-3 sm:grid-cols-4"
				onSubmit={(e) => {
					e.preventDefault()
					setOffset(0)
					setQuery(q.trim())
				}}
			>
				<div className="sm:col-span-2">
					<Field id="ap-q" label="Search name, email or roll number">
						<input id="ap-q" className={inputClass} maxLength={100} value={q} onChange={(e) => setQ(e.target.value)} />
					</Field>
				</div>
				<Field id="ap-status" label="Status">
					<select
						id="ap-status"
						className={inputClass}
						value={status}
						onChange={(e) => {
							setOffset(0)
							setStatus(e.target.value)
						}}
					>
						<option value="">All</option>
						{APP_STATUSES.map((s) => (
							<option key={s} value={s}>
								{STATUS_LABEL[s]}
							</option>
						))}
					</select>
				</Field>
				<Field id="ap-elig" label="Eligibility now">
					<select
						id="ap-elig"
						className={inputClass}
						value={eligible}
						onChange={(e) => {
							setOffset(0)
							setEligible(e.target.value)
						}}
					>
						<option value="">All</option>
						<option value="true">Eligible</option>
						<option value="false">Not eligible</option>
					</select>
				</Field>
				<button type="submit" className={`${secondaryButtonClass} sm:justify-self-start`}>
					Search
				</button>
			</form>
			{flash ? (
				<div className="mt-3">
					<Flash kind="ok" text={flash} />
				</div>
			) : null}
			<div className="mt-4">
				{res.loading && !res.data ? (
					<SkeletonList rows={3} />
				) : res.error ? (
					<ErrorState message={res.error} onRetry={res.reload} requestId={res.requestId} />
				) : rows.length === 0 ? (
					<EmptyState title="No applicants match" description="Try a different filter or wait for students to apply." />
				) : (
					<div className="overflow-x-auto relative [contain:inline-size] rounded-xl border border-slate-200 dark:border-white/10">
						<table className="min-w-full text-left text-sm">
							<caption className="sr-only">Applicants for this drive</caption>
							<thead className="bg-slate-50 text-xs uppercase text-slate-600 dark:bg-white/5 dark:text-slate-300">
								<tr>
									{canWrite ? (
										<th scope="col" className="px-3 py-2">
											<input
												type="checkbox"
												aria-label="Select all applicants on this page"
												className="h-4 w-4"
												checked={allOnPage}
												onChange={() =>
													setSelected((prev) => {
														const next = new Set(prev)
														for (const r of rows) {
															if (allOnPage) next.delete(r.application_id)
															else next.add(r.application_id)
														}
														return next
													})
												}
											/>
										</th>
									) : null}
									<th scope="col" className="px-3 py-2">Student</th>
									<th scope="col" className="px-3 py-2">Status</th>
									<th scope="col" className="px-3 py-2">Eligible now</th>
									<th scope="col" className="px-3 py-2">CGPA</th>
									<th scope="col" className="hidden px-3 py-2 md:table-cell">Attendance</th>
									<th scope="col" className="hidden px-3 py-2 lg:table-cell">Applied</th>
									<th scope="col" className="px-3 py-2">
										<span className="sr-only">Details</span>
									</th>
								</tr>
							</thead>
							<tbody className="divide-y divide-slate-200 dark:divide-white/10">
								{rows.map((a) => (
									<Fragment key={a.application_id}>
										<tr>
											{canWrite ? (
												<td className="px-3 py-2">
													<input
														type="checkbox"
														className="h-4 w-4"
														aria-label={`Select ${a.full_name}`}
														checked={selected.has(a.application_id)}
														onChange={() => setSelected((s) => toggleId(s, a.application_id))}
													/>
												</td>
											) : null}
											<td className="px-3 py-2">
												<div className="font-medium">{a.full_name}</div>
												<div className="text-xs text-slate-500 dark:text-slate-400">
													{[a.roll_number, a.department_name, a.graduation_year].filter(Boolean).join(" · ") || "—"}
													{a.placed ? " · placed" : ""}
												</div>
											</td>
											<td className="px-3 py-2">
												<StatusPill status={a.status} />
												{a.offer_status ? <div className="mt-1 text-xs">Offer: {a.offer_status}</div> : null}
											</td>
											<td className="px-3 py-2">
												<YesNo ok={a.eligible} yes="Yes" no="No" />
												{!a.eligible ? (
													<div className="text-xs text-slate-500 dark:text-slate-400">{a.failed_criteria.join(", ")}</div>
												) : null}
											</td>
											<td className="px-3 py-2">{a.cgpa}</td>
											<td className="hidden px-3 py-2 md:table-cell">{a.attendance}%</td>
											<td className="hidden px-3 py-2 lg:table-cell">{formatDateTime(a.applied_at)}</td>
											<td className="px-3 py-2">
												<button
													type="button"
													className={secondaryButtonClass}
													aria-expanded={open === a.application_id}
													aria-controls={`row-${a.application_id}`}
													onClick={() => setOpen(open === a.application_id ? null : a.application_id)}
												>
													{open === a.application_id ? "Hide" : "Details"}
												</button>
											</td>
										</tr>
										{open === a.application_id ? (
											<tr id={`row-${a.application_id}`}>
												<td colSpan={canWrite ? 8 : 7} className="bg-slate-50 px-3 py-3 dark:bg-white/5">
													{a.note ? <p className="text-sm">Last note: {a.note}</p> : null}
													<RowActions a={a} canWrite={canWrite} onDone={done} />
													<div className="mt-3">
														<History applicationId={a.application_id} />
													</div>
												</td>
											</tr>
										) : null}
									</Fragment>
								))}
							</tbody>
						</table>
					</div>
				)}
				{res.data ? <Pager offset={offset} limit={LIMIT} total={res.data.total} onChange={setOffset} /> : null}
			</div>
			{canWrite ? <BulkBar driveId={driveId} rows={rows} selected={selected} onFinished={done} /> : null}
		</div>
	)
}
