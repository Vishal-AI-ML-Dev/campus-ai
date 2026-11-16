"use client"

// Student Study Hub panel: read-only list of materials shared with the
// logged-in student's own section (backend scopes this via /materials/me).

import { useApiResource } from "@/lib/useApiResource"
import { EmptyState, ErrorState, SkeletonList } from "@/components/ui/states"
import { CATEGORY_LABEL, formatDateTime, type MaterialOut } from "./studyHubApi"

export default function MaterialStudentPanel() {
	const {
		data: materials,
		loading,
		error,
		requestId,
		reload,
	} = useApiResource<MaterialOut[]>("/materials/me", {
		fallbackMessage: "Failed to load materials",
	})

	if (loading) return <SkeletonList rows={3} />
	if (error)
		return <ErrorState message={error} requestId={requestId} onRetry={reload} />
	if (!materials || materials.length === 0)
		return (
			<EmptyState
				title="No study materials yet"
				description="Nothing has been shared with your section yet. Check back later."
			/>
		)

	return (
		<ul className="space-y-3">
			{materials.map((m) => (
				<li key={m.id} className="rounded-lg border border-gray-200 dark:border-white/10 p-4">
					<p className="font-medium text-gray-900 dark:text-slate-100">
						{m.title}{" "}
						<span className="text-xs text-gray-500 dark:text-slate-400">
							{CATEGORY_LABEL[m.category]}
						</span>
					</p>
					{m.description ? (
						<p className="text-sm text-gray-600 dark:text-slate-300">{m.description}</p>
					) : null}
					{m.content ? (
						<p className="mt-1 whitespace-pre-wrap text-sm text-gray-700 dark:text-slate-300">
							{m.content}
						</p>
					) : null}
					{m.link ? (
						<a
							href={m.link}
							target="_blank"
							rel="noopener noreferrer"
							className="mt-1 inline-block text-sm text-blue-600 underline"
						>
							Open resource
						</a>
					) : null}
					<p className="mt-1 text-xs text-gray-400 dark:text-slate-500">
						{formatDateTime(m.created_at)}
					</p>
				</li>
			))}
		</ul>
	)
}
