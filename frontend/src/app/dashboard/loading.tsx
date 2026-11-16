// Route-level loading UI for /dashboard/* segment navigations. Next.js shows
// this (inside the persistent AppShell) while the next route's payload loads.

import { SkeletonList } from "@/components/ui/states"

export default function DashboardLoading() {
	return (
		<div className="p-6">
			<SkeletonList rows={4} />
		</div>
	)
}
