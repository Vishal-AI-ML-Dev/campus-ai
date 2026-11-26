"use client"

/** Past interviews with their report scores; opens a report or transcript. */

import { useApiResource } from "@/lib/useApiResource"
import { EmptyState, ErrorState, SkeletonList } from "@/components/ui/states"
import { TYPE_LABELS, type HistoryItem } from "./mockInterviewApi"

const STATUS_TEXT: Record<string, string> = {
	setup: "Not started",
	in_progress: "In progress",
	evaluating: "Evaluating",
	completed: "Completed",
	abandoned: "Cancelled",
	failed: "Failed",
}

function formatDate(iso: string): string {
	const d = new Date(iso)
	return Number.isNaN(d.getTime())
		? ""
		: d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

export default function InterviewHistory({ onOpen }: { onOpen: (item: HistoryItem) => void }) {
	const history = useApiResource<HistoryItem[]>("/mock-interview/history", {
		fallbackMessage: "Could not load your past interviews.",
	})

	let body
	if (history.loading) body = <SkeletonList rows={2} />
	else if (history.error)
		body = <ErrorState message={history.error} onRetry={history.reload} requestId={history.requestId} />
	else {
		const items = (history.data ?? []).filter(
			(i) => i.status === "completed" || i.status === "abandoned",
		)
		body = items.length ? (
			<ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 dark:divide-white/5 dark:border-white/10">
				{items.map((item) => (
					<li key={item.id} className="flex flex-wrap items-center gap-3 p-3 text-sm">
						<div className="min-w-0 flex-1">
							<p className="truncate font-medium">
								{TYPE_LABELS[item.interview_type] ?? item.interview_type}
								{item.mode === "voice" ? " · Voice" : item.mode === "video" ? " · Video" : ""}
								{item.interview_type === "coding" ? " · Feedback only" : ""}
								{item.target_role ? ` · ${item.target_role}` : ""}
							</p>
							<p className="text-xs text-slate-500 dark:text-slate-400">
								{formatDate(item.completed_at ?? item.created_at)} · {STATUS_TEXT[item.status] ?? item.status}
								{item.interview_type === "coding" ? null : (
									<>
										{" · "}
										{item.answered} answered
									</>
								)}
							</p>
						</div>
						<span className="tabular-nums text-sm font-semibold text-indigo-600 dark:text-indigo-400">
							{item.report ? `${Math.round(item.report.overall_score)}/100` : ""}
						</span>
						<button
							type="button"
							onClick={() => onOpen(item)}
							className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-50 dark:border-white/20 dark:hover:bg-white/5"
						>
							{item.interview_type === "coding"
								? "View summary"
								: item.status === "completed"
									? item.report
										? "View report"
										: "Get report"
									: "View transcript"}
						</button>
					</li>
				))}
			</ul>
		) : (
			<EmptyState
				title="No past interviews yet"
				description="Your finished interviews and feedback reports will appear here."
			/>
		)
	}

	return (
		<section aria-labelledby="mi-history-title" className="space-y-2">
			<h2 id="mi-history-title" className="text-base font-semibold">
				Your past interviews
			</h2>
			{body}
		</section>
	)
}
