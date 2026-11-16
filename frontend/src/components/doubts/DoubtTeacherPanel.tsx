"use client"

// Teacher/admin doubt-forum panel: pick a section, browse its doubts, and open
// one to answer / upvote / accept (resolve).

import { useState } from "react"
import { api, ApiError } from "@/lib/api"
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/states"
import SectionPicker from "./SectionPicker"
import DoubtDetail from "./DoubtDetail"
import { type DoubtOut, type MeUser } from "./doubtsApi"

export default function DoubtTeacherPanel({ me }: { me: MeUser }) {
	const [sectionId, setSectionId] = useState<number | null>(null)
	const [doubts, setDoubts] = useState<DoubtOut[]>([])
	const [loading, setLoading] = useState(false)
	const [error, setError] = useState<string | null>(null)
	const [requestId, setRequestId] = useState<string | undefined>(undefined)
	const [selected, setSelected] = useState<number | null>(null)

	function loadDoubts(id: number) {
		setLoading(true)
		setError(null)
		setRequestId(undefined)
		api
			.get(`/doubts?section_id=${id}`)
			.then((data) => setDoubts(data as DoubtOut[]))
			.catch((e) => {
				setError(e instanceof ApiError ? e.message : "Failed to load doubts")
				setRequestId(e instanceof ApiError ? e.requestId : undefined)
			})
			.finally(() => setLoading(false))
	}

	function handleSectionChange(id: number | null) {
		setSectionId(id)
		setDoubts([])
		setSelected(null)
		if (id !== null) loadDoubts(id)
	}

	const statusBadge = (resolved: boolean) =>
		resolved
			? "shrink-0 rounded-full bg-green-100 px-2 py-0.5 text-xs text-green-700"
			: "shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-700"

	if (selected !== null) {
		return (
			<DoubtDetail
				doubtId={selected}
				me={me}
				onBack={() => {
					setSelected(null)
					if (sectionId !== null) loadDoubts(sectionId)
				}}
				onChanged={() => {
					if (sectionId !== null) loadDoubts(sectionId)
				}}
			/>
		)
	}

	return (
		<div className="space-y-6">
			<section>
				<h2 className="mb-2 text-sm font-semibold text-gray-700 dark:text-slate-300">
					Pick a section
				</h2>
				<SectionPicker onSectionChange={handleSectionChange} />
			</section>

			{sectionId === null ? (
				<p className="text-sm text-gray-500 dark:text-slate-400">
					Select a department and section to view its doubts.
				</p>
			) : loading ? (
				<LoadingState message="Loading doubts..." />
			) : error ? (
				<ErrorState
					message={error}
					requestId={requestId}
					onRetry={() => sectionId !== null && loadDoubts(sectionId)}
				/>
			) : doubts.length === 0 ? (
				<EmptyState title="No doubts in this section yet" />
			) : (
				<ul className="space-y-2">
					{doubts.map((d) => (
						<li key={d.id}>
							<button
								onClick={() => setSelected(d.id)}
								className="w-full rounded-lg border border-gray-200 dark:border-white/10 p-3 text-left hover:bg-gray-50 dark:hover:bg-white/5"
							>
								<div className="flex items-center justify-between gap-3">
									<span className="font-medium text-gray-900 dark:text-slate-100">
										{d.title}
									</span>
									<span className={statusBadge(d.status === "resolved")}>
										{d.status === "resolved" ? "Resolved" : "Open"}
									</span>
								</div>
								<p className="mt-1 text-xs text-gray-500 dark:text-slate-400">
									{d.answer_count} answer
									{d.answer_count === 1 ? "" : "s"}
								</p>
							</button>
						</li>
					))}
				</ul>
			)}
		</div>
	)
}
