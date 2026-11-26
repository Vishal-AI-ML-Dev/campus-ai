"use client"

/**
 * Institute interview controls (Phase 7.8). Admins edit; TPOs see a read-only view
 * (decision P7-D3). The backend is authoritative (TPO PUT = 403, values above the
 * platform limit = 422); this form only offers what the server allows.
 */

import { useEffect, useState } from "react"
import { ApiError } from "@/lib/api"
import { ErrorState, LoadingState, Spinner } from "@/components/ui/states"
import type { Role } from "@/lib/auth"
import { opsApi, useLoaded, type Controls } from "./opsApi"
import { canEditControls, formatCount, LIMIT_FIELDS, parseLimitInput, validateLimit, type LimitKey } from "./opsLogic"

type Form = { paused: boolean; voice: boolean; video: boolean; limits: Record<LimitKey, string> }

function toForm(c: Controls): Form {
	const limits = {} as Record<LimitKey, string>
	for (const f of LIMIT_FIELDS) limits[f.key] = c.limits[f.key] == null ? "" : String(c.limits[f.key])
	return { paused: c.paused, voice: c.voice_allowed, video: c.video_allowed, limits }
}

function ReadOnly({ c }: { c: Controls }) {
	return (
		<dl className="mt-3 grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
			<div><dt className="text-slate-500 dark:text-slate-400">New interviews</dt><dd>{c.paused ? "Paused" : "Allowed"}</dd></div>
			<div><dt className="text-slate-500 dark:text-slate-400">Voice / video</dt><dd>{c.voice_allowed ? "Voice allowed" : "Voice off"} · {c.video_allowed ? "Video allowed" : "Video off"}</dd></div>
			{LIMIT_FIELDS.map((f) => (
				<div key={f.key}>
					<dt className="text-slate-500 dark:text-slate-400">{f.label}</dt>
					<dd>{c.effective_limits[f.key] == null ? "No limit" : formatCount(c.effective_limits[f.key])}</dd>
				</div>
			))}
		</dl>
	)
}

export default function ControlsPanel({ role }: { role: Role }) {
	const controls = useLoaded(() => opsApi.controls(), [])
	const [form, setForm] = useState<Form | null>(null)
	const [saving, setSaving] = useState(false)
	const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
	useEffect(() => {
		if (controls.data) setForm(toForm(controls.data))
	}, [controls.data])

	if (controls.loading) return <LoadingState message="Loading controls…" />
	if (controls.error || !controls.data)
		return <ErrorState message={controls.error ?? "Could not load controls."} onRetry={controls.reload} requestId={controls.requestId} />
	const c = controls.data
	const editable = canEditControls(role, c.can_edit)
	const errors = form
		? (Object.fromEntries(LIMIT_FIELDS.map((f) => [f.key, validateLimit(f.key, form.limits[f.key], c.platform_limits)])) as Record<LimitKey, string | null>)
		: null
	const invalid = errors ? Object.values(errors).some(Boolean) : true

	async function save(e: React.FormEvent) {
		e.preventDefault()
		if (!form || invalid || saving) return
		setSaving(true)
		setMessage(null)
		try {
			const limits: Record<string, number | null> = {}
			for (const f of LIMIT_FIELDS) {
				const v = parseLimitInput(form.limits[f.key])
				limits[f.key] = v === "invalid" ? null : v
			}
			await opsApi.updateControls({ paused: form.paused, voice_allowed: form.voice, video_allowed: form.video, limits })
			setMessage({ ok: true, text: "Saved. The change is recorded in the audit log." })
			controls.reload()
		} catch (err) {
			setMessage({ ok: false, text: err instanceof ApiError ? err.message : "Could not save the controls." })
		} finally {
			setSaving(false)
		}
	}

	return (
		<section aria-labelledby="ops-controls" className="rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-slate-900">
			<h2 id="ops-controls" className="text-lg font-semibold">Institute controls</h2>
			<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
				These settings can only narrow what the platform allows. Interviews already in progress always finish.
				{c.platform_voice_enabled ? "" : " Voice is off for the whole platform."}
				{c.platform_video_enabled ? "" : " Video is off for the whole platform."}
			</p>
			{!editable || !form ? (
				<>
					<ReadOnly c={c} />
					<p className="mt-3 text-xs text-slate-500 dark:text-slate-400">Only institute admins can change these settings.</p>
				</>
			) : (
				<form onSubmit={save} className="mt-3 space-y-4" noValidate>
					<fieldset className="space-y-2">
						<legend className="text-sm font-medium">Availability</legend>
						<label className="flex items-center gap-2 text-sm">
							<input type="checkbox" checked={form.paused} onChange={(e) => setForm({ ...form, paused: e.target.checked })} />
							Pause new practice interviews
						</label>
						<label className="flex items-center gap-2 text-sm">
							<input type="checkbox" checked={form.voice} disabled={!c.platform_voice_enabled} onChange={(e) => setForm({ ...form, voice: e.target.checked })} />
							Allow voice interviews
						</label>
						<label className="flex items-center gap-2 text-sm">
							<input type="checkbox" checked={form.video} disabled={!c.platform_video_enabled} onChange={(e) => setForm({ ...form, video: e.target.checked })} />
							Allow video interviews
						</label>
					</fieldset>
					<fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-3">
						<legend className="mb-1 text-sm font-medium">Limits (leave empty for the platform limit)</legend>
						{LIMIT_FIELDS.map((f) => {
							const ceiling = c.platform_limits[f.key]
							const id = `limit-${f.key}`
							const err = errors?.[f.key]
							return (
								<div key={f.key}>
									<label htmlFor={id} className="block text-xs font-medium">{f.label}</label>
									<input
										id={id}
										inputMode="numeric"
										value={form.limits[f.key]}
										onChange={(e) => setForm({ ...form, limits: { ...form.limits, [f.key]: e.target.value } })}
										aria-invalid={err ? true : undefined}
										aria-describedby={`${id}-hint`}
										className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-slate-900"
									/>
									<p id={`${id}-hint`} className={`mt-1 text-xs ${err ? "text-red-600 dark:text-red-300" : "text-slate-500 dark:text-slate-400"}`}>
										{err ?? (ceiling == null ? "Platform: no limit" : `Platform limit: ${formatCount(ceiling)}`)}
									</p>
								</div>
							)
						})}
					</fieldset>
					<div className="flex flex-wrap items-center gap-3">
						<button type="submit" disabled={invalid || saving} className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
							{saving ? <Spinner /> : null} Save controls
						</button>
						{message ? (
							<p role="status" className={`text-sm ${message.ok ? "text-emerald-600 dark:text-emerald-300" : "text-red-600 dark:text-red-300"}`}>{message.text}</p>
						) : null}
					</div>
				</form>
			)}
		</section>
	)
}
