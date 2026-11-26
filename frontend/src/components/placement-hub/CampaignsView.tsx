"use client"

/**
 * Interview-preparation campaigns (Phase 9.3) for faculty, TPOs and admins.
 *
 * Faculty manage their own campaigns and assign students of sections they
 * teach; TPO/admin manage all and can pick a drive's applicants. Progress
 * shows COMPLETION STATUS ONLY (decision P9-D7): the API returns no score,
 * transcript, recording, feedback, report or answer, so none can appear here.
 */

import { useState } from "react"
import type { Role } from "@/lib/auth"
import { EmptyState, ErrorState, LoadingState, SkeletonList } from "@/components/ui/states"
import { errorText, insightsApi, placementApi, type Campaign, type CampaignInput } from "./placementApi"
import {
	buttonClass,
	cardClass,
	dangerButtonClass,
	Field,
	Flash,
	inputClass,
	Pager,
	secondaryButtonClass,
} from "./HubUi"
import {
	APP_STATUSES,
	CAMPAIGN_MOVE_LABEL,
	CAMPAIGN_MOVES,
	CAMPAIGN_STATUS_LABEL,
	COMPLETION_LABEL,
	formatDateTime,
	isoToLocalInput,
	localInputToIso,
	STATUS_LABEL,
	toggleId,
	type CampaignStatus,
	type Completion,
} from "./placementLogic"
import { useAsync } from "./useAsync"

const TYPES = [
	{ value: "", label: "Any interview type" },
	{ value: "hr", label: "HR" },
	{ value: "technical", label: "Technical" },
	{ value: "coding", label: "Coding" },
]

function CampaignForm({
	initial,
	staff,
	submitLabel,
	onSubmit,
}: {
	initial?: Campaign
	staff: boolean
	submitLabel: string
	onSubmit: (c: CampaignInput) => Promise<void>
}) {
	const [f, setF] = useState(() => ({
		title: initial?.title ?? "",
		description: initial?.description ?? "",
		interview_type: initial?.interview_type ?? "",
		target_role: initial?.target_role ?? "",
		drive_id: initial?.drive_id ? String(initial.drive_id) : "",
		starts: isoToLocalInput(initial?.starts_at ?? new Date().toISOString()),
		due: isoToLocalInput(initial?.due_at ?? new Date(Date.now() + 7 * 86400_000).toISOString()),
	}))
	const [busy, setBusy] = useState(false)
	const [err, setErr] = useState<string | null>(null)
	const drives = useAsync(staff ? "camp-drives" : null, () => placementApi.drives({ limit: 200 }))
	const set = (k: keyof typeof f, v: string) => setF((p) => ({ ...p, [k]: v }))

	async function submit(e: React.FormEvent) {
		e.preventDefault()
		const starts = localInputToIso(f.starts)
		const due = localInputToIso(f.due)
		if (f.title.trim().length < 3) return setErr("Title needs at least 3 characters.")
		if (!starts || !due || starts >= due) return setErr("The campaign must start before its due date.")
		setBusy(true)
		setErr(null)
		try {
			await onSubmit({
				title: f.title.trim(),
				description: f.description.trim() || null,
				interview_type: (f.interview_type || null) as CampaignInput["interview_type"],
				target_role: f.target_role.trim() || null,
				drive_id: f.drive_id ? Number(f.drive_id) : null,
				starts_at: starts,
				due_at: due,
			})
		} catch (e2) {
			setErr(errorText(e2))
		} finally {
			setBusy(false)
		}
	}

	return (
		<form onSubmit={submit} className="grid gap-3 sm:grid-cols-2" noValidate>
			<div className="sm:col-span-2">
				<Field id="cf-title" label="Title *">
					<input id="cf-title" className={inputClass} maxLength={150} value={f.title} onChange={(e) => set("title", e.target.value)} />
				</Field>
			</div>
			<div className="sm:col-span-2">
				<Field id="cf-desc" label="Instructions for students">
					<textarea id="cf-desc" rows={2} maxLength={2000} className={inputClass} value={f.description} onChange={(e) => set("description", e.target.value)} />
				</Field>
			</div>
			<Field id="cf-type" label="Interview type counted">
				<select id="cf-type" className={inputClass} value={f.interview_type} onChange={(e) => set("interview_type", e.target.value)}>
					{TYPES.map((t) => (
						<option key={t.value} value={t.value}>
							{t.label}
						</option>
					))}
				</select>
			</Field>
			<Field id="cf-role" label="Suggested job role">
				<input id="cf-role" className={inputClass} maxLength={150} value={f.target_role} onChange={(e) => set("target_role", e.target.value)} />
			</Field>
			<Field id="cf-start" label="Starts (your local time)">
				<input id="cf-start" type="datetime-local" className={inputClass} value={f.starts} onChange={(e) => set("starts", e.target.value)} />
			</Field>
			<Field id="cf-due" label="Due (your local time)">
				<input id="cf-due" type="datetime-local" className={inputClass} value={f.due} onChange={(e) => set("due", e.target.value)} />
			</Field>
			{staff ? (
				<div className="sm:col-span-2">
					<Field id="cf-drive" label="Linked placement drive (optional)">
						<select id="cf-drive" className={inputClass} value={f.drive_id} onChange={(e) => set("drive_id", e.target.value)}>
							<option value="">None</option>
							{(drives.data?.items ?? []).map((d) => (
								<option key={d.id} value={d.id}>
									{d.company_name} {"—"} {d.role_title}
								</option>
							))}
						</select>
					</Field>
				</div>
			) : null}
			<div className="sm:col-span-2">
				{err ? <Flash kind="error" text={err} /> : null}
				<button type="submit" className={`${buttonClass} mt-2`} disabled={busy}>
					{busy ? "Saving…" : submitLabel}
				</button>
			</div>
		</form>
	)
}

function Assign({ c, staff, onDone }: { c: Campaign; staff: boolean; onDone: (msg: string) => void }) {
	const [sectionId, setSectionId] = useState("")
	const [driveId, setDriveId] = useState("")
	const [appStatus, setAppStatus] = useState("")
	const [q, setQ] = useState("")
	const [offset, setOffset] = useState(0)
	const [picked, setPicked] = useState<Set<number>>(new Set())
	const [busy, setBusy] = useState(false)
	const [err, setErr] = useState<string | null>(null)
	const sections = useAsync("camp-sections", () => insightsApi.sections())
	const drives = useAsync(staff ? "camp-drives" : null, () => placementApi.drives({ limit: 200 }))
	const params = { section_id: sectionId || undefined, drive_id: driveId || undefined, application_status: driveId && appStatus ? appStatus : undefined, q: q || undefined, limit: 50, offset }
	const cands = useAsync(JSON.stringify({ id: c.id, params }), () => insightsApi.candidates(c.id, params))

	async function run(body: { student_ids?: number[]; section_ids?: number[] }) {
		setBusy(true)
		setErr(null)
		try {
			const r = await insightsApi.assign(c.id, body)
			setPicked(new Set())
			onDone(`${r.added} student${r.added === 1 ? "" : "s"} assigned (${r.already_assigned} already assigned).`)
			cands.reload()
		} catch (e) {
			setErr(errorText(e))
		} finally {
			setBusy(false)
		}
	}

	return (
		<section aria-labelledby={`as-${c.id}`} className="space-y-3">
			<h4 id={`as-${c.id}`} className="font-semibold">
				Assign students
			</h4>
			{sections.data && sections.data.length === 0 && !staff ? (
				<p className="text-sm text-slate-600 dark:text-slate-400">
					You can assign students of sections you teach. No timetable classes are linked to your account yet.
				</p>
			) : null}
			<div className="grid gap-3 sm:grid-cols-4">
				<Field id={`as-sec-${c.id}`} label="Section">
					<select id={`as-sec-${c.id}`} className={inputClass} value={sectionId} onChange={(e) => { setOffset(0); setSectionId(e.target.value) }}>
						<option value="">All {staff ? "" : "my "}sections</option>
						{(sections.data ?? []).map((s) => (
							<option key={s.id} value={s.id}>
								{s.name}
								{s.department_name ? ` (${s.department_name})` : ""}
							</option>
						))}
					</select>
				</Field>
				{staff ? (
					<>
						<Field id={`as-drv-${c.id}`} label="Applicants of drive">
							<select id={`as-drv-${c.id}`} className={inputClass} value={driveId} onChange={(e) => { setOffset(0); setDriveId(e.target.value) }}>
								<option value="">Any</option>
								{(drives.data?.items ?? []).map((d) => (
									<option key={d.id} value={d.id}>
										{d.company_name} {"—"} {d.role_title}
									</option>
								))}
							</select>
						</Field>
						<Field id={`as-st-${c.id}`} label="With status">
							<select id={`as-st-${c.id}`} className={inputClass} value={appStatus} disabled={!driveId} onChange={(e) => { setOffset(0); setAppStatus(e.target.value) }}>
								<option value="">Any</option>
								{APP_STATUSES.map((s) => (
									<option key={s} value={s}>
										{STATUS_LABEL[s]}
									</option>
								))}
							</select>
						</Field>
					</>
				) : null}
				<Field id={`as-q-${c.id}`} label="Search name or email">
					<input id={`as-q-${c.id}`} className={inputClass} maxLength={100} value={q} onChange={(e) => { setOffset(0); setQ(e.target.value) }} />
				</Field>
			</div>
			{sectionId ? (
				<button type="button" className={secondaryButtonClass} disabled={busy} onClick={() => run({ section_ids: [Number(sectionId)] })}>
					Assign the whole section
				</button>
			) : null}
			{cands.loading && !cands.data ? (
				<LoadingState message="Loading students…" />
			) : cands.error ? (
				<ErrorState message={cands.error} onRetry={cands.reload} />
			) : !cands.data?.items.length ? (
				<EmptyState title="No students match" />
			) : (
				<>
					<ul className="max-h-72 divide-y divide-slate-200 overflow-y-auto rounded-lg border border-slate-200 dark:divide-white/10 dark:border-white/10">
						{cands.data.items.map((s) => (
							<li key={s.student_id} className="flex items-center gap-3 px-3 py-2 text-sm">
								<input
									type="checkbox"
									className="h-4 w-4"
									id={`cand-${c.id}-${s.student_id}`}
									disabled={s.assigned}
									checked={s.assigned || picked.has(s.student_id)}
									onChange={() => setPicked((p) => toggleId(p, s.student_id))}
								/>
								<label htmlFor={`cand-${c.id}-${s.student_id}`} className="flex-1">
									{s.full_name}
									<span className="text-xs text-slate-500 dark:text-slate-400">
										{" "}
										{[s.section_name, s.department_name].filter(Boolean).join(" · ")}
										{s.assigned ? " · already assigned" : ""}
									</span>
								</label>
							</li>
						))}
					</ul>
					<Pager offset={offset} limit={50} total={cands.data.total} onChange={setOffset} />
				</>
			)}
			{err ? <Flash kind="error" text={err} /> : null}
			<button type="button" className={buttonClass} disabled={busy || picked.size === 0} onClick={() => run({ student_ids: [...picked] })}>
				{busy ? "Assigning…" : `Assign ${picked.size} selected`}
			</button>
		</section>
	)
}

function Progress({ c, editable, onChanged }: { c: Campaign; editable: boolean; onChanged: () => void }) {
	const [filter, setFilter] = useState<"" | Completion>("")
	const [offset, setOffset] = useState(0)
	const [err, setErr] = useState<string | null>(null)
	const res = useAsync(JSON.stringify({ id: c.id, filter, offset }), () =>
		insightsApi.progress(c.id, { completion: filter || undefined, limit: 50, offset }),
	)
	async function remove(studentId: number) {
		setErr(null)
		try {
			await insightsApi.unassign(c.id, studentId)
			res.reload()
			onChanged()
		} catch (e) {
			setErr(errorText(e))
		}
	}
	return (
		<section aria-labelledby={`pg-${c.id}`} className="space-y-3">
			<div className="flex flex-wrap items-end justify-between gap-3">
				<h4 id={`pg-${c.id}`} className="font-semibold">
					Completion
				</h4>
				<Field id={`pg-f-${c.id}`} label="Show">
					<select id={`pg-f-${c.id}`} className={inputClass} value={filter} onChange={(e) => { setOffset(0); setFilter(e.target.value as "" | Completion) }}>
						<option value="">Everyone</option>
						{(Object.keys(COMPLETION_LABEL) as Completion[]).map((k) => (
							<option key={k} value={k}>
								{COMPLETION_LABEL[k]}
							</option>
						))}
					</select>
				</Field>
			</div>
			<p className="text-xs text-slate-600 dark:text-slate-400">
				Only whether each student completed a mock interview in the campaign window is shown. Scores, answers and
				reports stay private to the student.
			</p>
			{err ? <Flash kind="error" text={err} /> : null}
			{res.loading && !res.data ? (
				<LoadingState message="Loading progress…" />
			) : res.error ? (
				<ErrorState message={res.error} onRetry={res.reload} />
			) : !res.data?.items.length ? (
				<EmptyState title="No students to show" />
			) : (
				<div className="overflow-x-auto relative [contain:inline-size] rounded-lg border border-slate-200 dark:border-white/10">
					<table className="min-w-full text-left text-sm">
						<caption className="sr-only">Completion status per assigned student</caption>
						<thead className="bg-slate-50 text-xs uppercase text-slate-600 dark:bg-white/5 dark:text-slate-300">
							<tr>
								<th scope="col" className="px-3 py-2">Student</th>
								<th scope="col" className="px-3 py-2">Status</th>
								<th scope="col" className="hidden px-3 py-2 sm:table-cell">Completed</th>
								{editable ? <th scope="col" className="px-3 py-2"><span className="sr-only">Remove</span></th> : null}
							</tr>
						</thead>
						<tbody className="divide-y divide-slate-200 dark:divide-white/10">
							{res.data.items.map((r) => (
								<tr key={r.student_id}>
									<td className="px-3 py-2">
										{r.full_name}
										<div className="text-xs text-slate-500 dark:text-slate-400">{r.section_name ?? ""}</div>
									</td>
									<td className="px-3 py-2">
										<span aria-hidden="true">{r.completion === "pending" ? "○ " : "✓ "}</span>
										{COMPLETION_LABEL[r.completion]}
									</td>
									<td className="hidden px-3 py-2 sm:table-cell">{formatDateTime(r.completed_at)}</td>
									{editable ? (
										<td className="px-3 py-2">
											<button type="button" className={secondaryButtonClass} onClick={() => remove(r.student_id)} aria-label={`Remove ${r.full_name}`}>
												Remove
											</button>
										</td>
									) : null}
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
			{res.data ? <Pager offset={offset} limit={50} total={res.data.total} onChange={setOffset} /> : null}
		</section>
	)
}

function CampaignCard({ c, staff, onChanged }: { c: Campaign; staff: boolean; onChanged: () => void }) {
	const [open, setOpen] = useState(false)
	const [editing, setEditing] = useState(false)
	const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null)
	const [confirm, setConfirm] = useState<CampaignStatus | null>(null)
	const editable = c.status === "draft" || c.status === "active"
	async function move(to: CampaignStatus) {
		setMsg(null)
		try {
			await insightsApi.campaignStatus(c.id, to)
			setConfirm(null)
			setMsg({ kind: "ok", text: `Campaign ${CAMPAIGN_STATUS_LABEL[to].toLowerCase()}.` })
			onChanged()
		} catch (e) {
			setMsg({ kind: "error", text: errorText(e) })
		}
	}
	const done = c.completed + c.completed_late
	return (
		<li className={cardClass}>
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div className="min-w-0">
					<h3 className="font-semibold">{c.title}</h3>
					<p className="text-xs text-slate-600 dark:text-slate-400">
						{CAMPAIGN_STATUS_LABEL[c.status]} {"·"} due {formatDateTime(c.due_at)}
						{c.interview_type ? ` · ${c.interview_type}` : ""}
						{c.drive_label ? ` · ${c.drive_label}` : ""}
						{c.created_by_name && !c.is_mine ? ` · by ${c.created_by_name}` : ""}
					</p>
					<p className="mt-1 text-sm">
						{done} of {c.assigned} completed{c.completed_late ? ` (${c.completed_late} late)` : ""}
					</p>
				</div>
				<div className="flex flex-wrap gap-2">
					{CAMPAIGN_MOVES[c.status].map((to) => (
						<button
							key={to}
							type="button"
							className={to === "cancelled" ? dangerButtonClass : secondaryButtonClass}
							onClick={() => (to === "cancelled" || to === "archived" ? setConfirm(to) : move(to))}
						>
							{CAMPAIGN_MOVE_LABEL[to]}
						</button>
					))}
					<button type="button" className={secondaryButtonClass} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
						{open ? "Hide" : "Manage"}
					</button>
				</div>
			</div>
			{confirm ? (
				<div role="alertdialog" aria-labelledby={`cc-${c.id}`} className="mt-3 rounded-lg border border-red-300 p-3 dark:border-red-500/40">
					<p id={`cc-${c.id}`} className="text-sm">
						{confirm === "cancelled" ? "Cancel this campaign? Students keep their practice history." : "Archive this campaign? It will be hidden from the list."}
					</p>
					<div className="mt-2 flex gap-2">
						<button type="button" className={dangerButtonClass} onClick={() => move(confirm)}>
							Yes, {CAMPAIGN_MOVE_LABEL[confirm].toLowerCase()}
						</button>
						<button type="button" className={secondaryButtonClass} onClick={() => setConfirm(null)}>
							Keep
						</button>
					</div>
				</div>
			) : null}
			{msg ? (
				<div className="mt-2">
					<Flash kind={msg.kind} text={msg.text} />
				</div>
			) : null}
			{open ? (
				<div className="mt-4 space-y-6 border-t border-slate-200 pt-4 dark:border-white/10">
					{c.description ? <p className="whitespace-pre-line text-sm">{c.description}</p> : null}
					{editable ? (
						<div>
							<button type="button" className={secondaryButtonClass} aria-expanded={editing} onClick={() => setEditing((v) => !v)}>
								{editing ? "Close editor" : "Edit details"}
							</button>
							{editing ? (
								<div className="mt-3">
									<CampaignForm
										initial={c}
										staff={staff}
										submitLabel="Save campaign"
										onSubmit={async (input) => {
											await insightsApi.editCampaign(c.id, input)
											setEditing(false)
											setMsg({ kind: "ok", text: "Campaign saved." })
											onChanged()
										}}
									/>
								</div>
							) : null}
						</div>
					) : null}
					{editable ? <Assign c={c} staff={staff} onDone={(text) => { setMsg({ kind: "ok", text }); onChanged() }} /> : null}
					<Progress c={c} editable={editable} onChanged={onChanged} />
				</div>
			) : null}
		</li>
	)
}

export default function CampaignsView({ role }: { role: Role }) {
	const staff = role === "tpo" || role === "admin"
	const [creating, setCreating] = useState(false)
	const [archived, setArchived] = useState(false)
	const [offset, setOffset] = useState(0)
	const [flash, setFlash] = useState<string | null>(null)
	const list = useAsync(JSON.stringify({ archived, offset }), () =>
		insightsApi.campaigns({ include_archived: archived, limit: 20, offset }),
	)
	return (
		<div className="space-y-6">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<h2 className="text-2xl font-bold">Interview preparation campaigns</h2>
					<p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
						Ask students to practise with the AI mock interview before a deadline and track who has completed it.
					</p>
				</div>
				<button type="button" className={buttonClass} aria-expanded={creating} onClick={() => setCreating((v) => !v)}>
					{creating ? "Close" : "+ New campaign"}
				</button>
			</div>
			{flash ? <Flash kind="ok" text={flash} /> : null}
			{creating ? (
				<section className={cardClass} aria-label="New campaign">
					<CampaignForm
						staff={staff}
						submitLabel="Create as draft"
						onSubmit={async (input) => {
							await insightsApi.createCampaign(input)
							setCreating(false)
							setFlash("Campaign created as a draft. Assign students, then activate it.")
							list.reload()
						}}
					/>
				</section>
			) : null}
			<label className="flex items-center gap-2 text-sm">
				<input type="checkbox" className="h-4 w-4" checked={archived} onChange={(e) => { setOffset(0); setArchived(e.target.checked) }} />
				Include archived campaigns
			</label>
			{list.loading && !list.data ? (
				<SkeletonList rows={2} />
			) : list.error ? (
				<ErrorState message={list.error} onRetry={list.reload} requestId={list.requestId} />
			) : !list.data?.items.length ? (
				<EmptyState title="No campaigns yet" description="Create one to assign interview practice to students." />
			) : (
				<>
					<ul className="space-y-3">
						{list.data.items.map((c) => (
							<CampaignCard key={c.id} c={c} staff={staff} onChanged={list.reload} />
						))}
					</ul>
					<Pager offset={offset} limit={20} total={list.data.total} onChange={setOffset} />
				</>
			)}
		</div>
	)
}
