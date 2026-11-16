/**
 * Student -> Offers page: view placement offers and accept/decline them.
 *
 * Place this file at: src/app/dashboard/offers/page.tsx
 *
 * Student-only (the backend also scopes /recruiters/offers/mine to the caller).
 */

"use client";

import { useCurrentUser } from "@/lib/auth";
import StudentOffersPanel from "@/components/offers/StudentOffersPanel";

export default function StudentOffersPage() {
  const { user } = useCurrentUser();
  if (!user) return null;
  if (user.role !== "student") {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400">
        This page is only available to student accounts.
      </p>
    );
  }
  return <StudentOffersPanel />;
}
