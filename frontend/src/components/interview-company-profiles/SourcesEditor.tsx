"use client"

/**
 * Phase 8.4: source records for a company profile (P8-D10). At least one is
 * needed to publish. A public page is a citation only: the address is stored
 * as text, never fetched, and students see only its label and domain.
 */

import type { OpenDrive } from "./companyProfilesApi"
import { LIMITS, SOURCE_KINDS, type SourceDraft } from "./profileDraft"

const field =
	"w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-white/10 dark:bg-slate-900"

export default function SourcesEditor({
	sources,
	drives,
	onChange,
	disabled,
}: {
	sources: SourceDraft[]
	drives: OpenDrive[]
	onChange: (next: SourceDraft[]) => void
	disabled?: boolean
}) {
	function set(i: number, patch: Partial<SourceDraft>) {
		onChange(sources.map((s, j) => (j === i ? { ...s, ...patch } : s)))
	}
	return (
		<fieldset className="space-y-2" disabled={disabled}>
			<legend className="text-sm font-medium">Sources</legend>
			<p className="text-xs text-slate-500 dark:text-slate-400">
				Record where this information comes from. Students see each source as plain text. Web addresses are never
				opened by Campus AI; students see only the label and the site name.
			</p>
			{sources.length === 0 ? (
				<p className="text-xs text-amber-700 dark:text-amber-300">Add at least one source before publishing.</p>
			) : null}
			<ol className="space-y-2">
				{sources.map((s, i) => (
					<li key={i} className="grid gap-2 rounded-lg border border-slate-200 p-2 sm:grid-cols-[10rem_1fr_auto] dark:border-white/10">
						<label className="text-xs">
							<span className="sr-only">Source {i + 1} type</span>
							<select
								value={s.kind}
								onChange={(e) => set(i, { kind: e.target.value as SourceDraft["kind"], url: "", driveId: "" })}
								className={field}
							>
								{SOURCE_KINDS.map((k) => (
									<option key={k.value} value={k.value}>
										{k.label}
									</option>
								))}
							</select>
						</label>
						<div className="space-y-2">
							<label className="block text-xs">
								<span className="sr-only">Source {i + 1} description</span>
								<input
									value={s.label}
									maxLength={200}
									placeholder="e.g. Job description shared with the TPO, Oct 2026"
									onChange={(e) => set(i, { label: e.target.value })}
									className={field}
								/>
							</label>
							{s.kind === "public_url" ? (
								<label className="block text-xs">
									<span className="sr-only">Source {i + 1} web address</span>
									<input
										value={s.url}
										maxLength={500}
										inputMode="url"
										placeholder="https://… (not opened by Campus AI)"
										onChange={(e) => set(i, { url: e.target.value })}
										className={field}
									/>
								</label>
							) : null}
							{s.kind === "drive" ? (
								<label className="block text-xs">
									<span className="sr-only">Source {i + 1} placement drive</span>
									<select value={s.driveId} onChange={(e) => set(i, { driveId: e.target.value })} className={field}>
										<option value="">Choose an open drive</option>
										{drives.map((d) => (
											<option key={d.id} value={d.id}>
												#{d.id} {d.company_name} - {d.role_title}
											</option>
										))}
									</select>
								</label>
							) : null}
						</div>
						<button
							type="button"
							onClick={() => onChange(sources.filter((_, j) => j !== i))}
							className="min-h-[44px] rounded-lg border border-slate-200 px-3 text-xs dark:border-white/10"
							aria-label={`Remove source ${i + 1}`}
						>
							Remove
						</button>
					</li>
				))}
			</ol>
			{sources.length < LIMITS.sources ? (
				<button
					type="button"
					onClick={() => onChange([...sources, { kind: "jd", label: "", url: "", driveId: "" }])}
					className="min-h-[44px] rounded-lg border border-dashed border-slate-300 px-3 text-sm dark:border-white/20"
				>
					Add a source
				</button>
			) : null}
		</fieldset>
	)
}
