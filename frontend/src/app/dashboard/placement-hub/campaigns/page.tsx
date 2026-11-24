/**
 * Campus AI - Placement Hub: interview-preparation campaigns (Phase 9.3).
 * Faculty, TPO and admin. Progress is completion status only (P9-D7).
 * Backend: /placement/campaigns/* (backend/app/routers/placement_campaigns.py).
 */

"use client"

import { HubFrame, StaffNav } from "@/components/placement-hub/HubUi"
import CampaignsView from "@/components/placement-hub/CampaignsView"

export default function PlacementCampaignsPage() {
	return (
		<HubFrame title="Interview preparation campaigns" roles={["teacher", "tpo", "admin"]}>
			{(role) => (
				<>
					<StaffNav role={role} />
					<CampaignsView role={role} />
				</>
			)}
		</HubFrame>
	)
}
