/**
 * Campus AI - Placement Hub home (Phase 9.2-9.4).
 *
 * One entry point, role-aware: TPO/admin get the placement overview dashboard
 * (9.3), students their placement profile, opportunities, applications and
 * preparation (9.2/9.4), faculty their interview-preparation campaigns (9.3).
 * Feature-flagged (build flag + institute allow-list; see placementApi.ts).
 */

"use client"

import { HubFrame, StaffNav } from "@/components/placement-hub/HubUi"
import StudentHub from "@/components/placement-hub/StudentHub"
import TpoHome from "@/components/placement-hub/TpoHome"
import CampaignsView from "@/components/placement-hub/CampaignsView"

export default function PlacementHubPage() {
	return (
		<HubFrame title="Placement Hub" roles={["student", "tpo", "admin", "teacher"]}>
			{(role) =>
				role === "student" ? (
					<StudentHub />
				) : role === "teacher" ? (
					<CampaignsView role={role} />
				) : (
					<>
						<StaffNav role={role} />
						<TpoHome role={role} />
					</>
				)
			}
		</HubFrame>
	)
}
