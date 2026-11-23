"use client"

/**
 * Phase 8.2: the coding part of the setup form (languages, duration, topics,
 * optional practice problem). Choices come from GET /mock-interview/coding/catalog;
 * the backend validates everything again.
 */

import { useApiResource } from "@/lib/useApiResource"
import { ErrorState, LoadingState } from "@/components/ui/states"
import { codingApi, type CodingCatalog } from "./codingApi"

export type CodingChoices = { languages: string[]; topics: string[]; problemKey: string; duration: string }

const labelClass = "block text-sm font-medium text-slate-700 dark:text-slate-200"
const MAX_TOPICS = 5

function Checks({
	legend,
	name,
	options,
	selected,
	onToggle,
	hint,
}: {
	legend: string
	name: string
	options: { value: string; label: string }[]
	selected: string[]
	onToggle: (value: string) => void
	hint?: string
}) {
	return (
		<fieldset>
			<legend className={labelClass}>{legend}</legend>
			{hint ? <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p> : null}
			<div className="mt-2 flex flex-wrap gap-2">
				{options.map((o) => {
					const id = `${name}-${o.value}`
					const on = selected.includes(o.value)
					return (
						<label
							key={o.value}
							htmlFor={id}
							className={`flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-sm focus-within:ring-2 focus-within:ring-indigo-400/40 ${
								on
									? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
									: "border-slate-200 text-slate-600 dark:border-white/10 dark:text-slate-300"
							}`}
						>
							<input id={id} type="checkbox" checked={on} onChange={() => onToggle(o.value)} />
							{o.label}
						</label>
					)
				})}
			</div>
		</fieldset>
	)
}

export default function CodingSetupFields({
	value,
	onChange,
	difficulty,
}: {
	value: CodingChoices
	onChange: (v: CodingChoices) => void
	difficulty: string
}) {
	const catalog = useApiResource<CodingCatalog>(codingApi.catalogPath, {
		fallbackMessage: "Could not load coding round options.",
	})
	if (catalog.loading) return <LoadingState message="Loading coding options…" />
	if (catalog.error || !catalog.data)
		return (
			<ErrorState
				message={catalog.error ?? "Could not load coding round options."}
				onRetry={catalog.reload}
				requestId={catalog.requestId}
			/>
		)
	const c = catalog.data
	const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
	const practice = c.problems.filter((p) => p.difficulty === difficulty)

	return (
		<div className="space-y-5">
			<Checks
				legend="Programming languages"
				name="coding-language"
				options={c.languages}
				selected={value.languages}
				onToggle={(v) => onChange({ ...value, languages: toggle(value.languages, v) })}
				hint="Choose at least one. You can switch between them during the round."
			/>
			<fieldset>
				<legend className={labelClass}>Duration</legend>
				<div className="mt-2 flex flex-wrap gap-2">
					{c.durations_minutes.map((d) => (
						<label
							key={d}
							className={`flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-sm focus-within:ring-2 focus-within:ring-indigo-400/40 ${
								value.duration === String(d)
									? "border-indigo-500 bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
									: "border-slate-200 text-slate-600 dark:border-white/10 dark:text-slate-300"
							}`}
						>
							<input
								type="radio"
								name="coding-duration"
								className="sr-only"
								checked={value.duration === String(d)}
								onChange={() => onChange({ ...value, duration: String(d) })}
							/>
							{d} min
						</label>
					))}
				</div>
			</fieldset>
			<Checks
				legend="Topics (optional)"
				name="coding-topic"
				options={c.topics}
				selected={value.topics}
				onToggle={(v) => {
					const next = toggle(value.topics, v)
					if (next.length <= MAX_TOPICS) onChange({ ...value, topics: next })
				}}
				hint={`Pick up to ${MAX_TOPICS} to prefer problems on them.`}
			/>
			<div>
				<label htmlFor="coding-problem" className={labelClass}>
					Practise one specific problem <span className="text-xs font-normal text-slate-400">(optional)</span>
				</label>
				<select
					id="coding-problem"
					value={value.problemKey}
					onChange={(e) => onChange({ ...value, problemKey: e.target.value })}
					className="mt-1 min-h-[44px] w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-slate-900"
				>
					<option value="">Choose for me (recommended)</option>
					{practice.map((p) => (
						<option key={p.key} value={p.key}>
							{p.title}
						</option>
					))}
				</select>
			</div>
		</div>
	)
}
