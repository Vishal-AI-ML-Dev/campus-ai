/**
 * Campus AI - client-side auth context + role helpers.
 *
 * Place this file at: src/lib/auth.tsx
 *
 * Provides:
 *   - <AuthProvider>: loads the current user once (via /auth/me) and guards the
 *     route (redirects to /login when there is no valid session).
 *   - useCurrentUser(): read the loaded user + loading state from any client
 *     component inside the provider.
 *   - NAV_BY_ROLE: the sidebar navigation items shown for each role (only
 *     features whose backend already exists are listed).
 */

"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { api, clearToken, ApiError } from "@/lib/api";

export type Role = "student" | "teacher" | "tpo" | "admin" | "recruiter";

export type CurrentUser = {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  /** A student's class/section (null for staff); part of the /auth/me response. */
  section_id?: number | null;
};

type AuthState = { user: CurrentUser | null; loading: boolean };

const AuthContext = createContext<AuthState>({ user: null, loading: true });

/** Loads the signed-in user once and shares it with the whole dashboard tree. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Cookie-based auth: no token is readable from JS. Just ask the backend who
    // we are; the api client transparently tries POST /auth/refresh once on a
    // 401, so a valid refresh cookie silently restores the session.
    let active = true;
    api
      .get<CurrentUser>("/auth/me")
      .then((u) => {
        if (active) setUser(u);
      })
      .catch((err) => {
        // Session truly gone -> purge any legacy token and bounce to login.
        if (err instanceof ApiError && err.status === 401) clearToken();
        if (active) router.replace("/login");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [router]);

  const value: AuthState = { user, loading };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Read the current user + loading flag from within <AuthProvider>. */
export function useCurrentUser(): AuthState {
  return useContext(AuthContext);
}

export type NavItem = { label: string; href: string; icon: string };

/**
 * Sidebar items per role. Only features with a working backend are listed;
 * more are added as their pages get built.
 */
export const NAV_BY_ROLE: Record<Role, NavItem[]> = {
  student: [
    { label: "Overview", href: "/dashboard", icon: "\u{1F3E0}" },
    {
      label: "Attendance",
      href: "/dashboard/attendance",
      icon: "\u{1F5D3}\uFE0F",
    },
    { label: "Academics", href: "/dashboard/academics", icon: "\u{1F4CA}" },
    { label: "Timetable", href: "/dashboard/timetable", icon: "\u{1F4C5}" },
    { label: "Assignments", href: "/dashboard/assignments", icon: "\u{1F4DD}" },
    { label: "Study Hub", href: "/dashboard/study-hub", icon: "\u{1F4DA}" },
    { label: "Doubts", href: "/dashboard/doubts", icon: "\u{1F4AC}" },
    { label: "Resume", href: "/dashboard/resume", icon: "\u{1F4C4}" },
    { label: "Skills", href: "/dashboard/skills", icon: "\u{1F6E1}\uFE0F" },
    { label: "Projects", href: "/dashboard/projects", icon: "\u{1F9E9}" },
    { label: "Activities", href: "/dashboard/eca", icon: "\u{1F3C6}" },
    { label: "Internships", href: "/dashboard/internships", icon: "\u{1F4BC}" },
    { label: "Placements", href: "/dashboard/placements", icon: "\u{1F3E2}" },
    { label: "Offers", href: "/dashboard/offers", icon: "\u{1F4DC}" },
    { label: "Leave & OD", href: "/dashboard/leave", icon: "\u{1F3D6}\uFE0F" },
    { label: "My Risk", href: "/dashboard/analytics", icon: "\u{1F4C8}" },
    { label: "AI Mentor", href: "/dashboard/mentor", icon: "\u{1F916}" },
  ],
  teacher: [
    { label: "Overview", href: "/dashboard", icon: "\u{1F3E0}" },
    { label: "Verify Queue", href: "/dashboard/verify", icon: "\u2705" },
    {
      label: "Attendance",
      href: "/dashboard/attendance",
      icon: "\u{1F5D3}\uFE0F",
    },
    { label: "Face Enroll", href: "/dashboard/face-enroll", icon: "\u{1F194}" },
    {
      label: "Face Attendance",
      href: "/dashboard/face-attendance",
      icon: "\u{1F4F8}",
    },
    { label: "Assignments", href: "/dashboard/assignments", icon: "\u{1F4DD}" },
    { label: "Study Hub", href: "/dashboard/study-hub", icon: "\u{1F4DA}" },
    { label: "Doubts", href: "/dashboard/doubts", icon: "\u{1F4AC}" },
    { label: "Timetable", href: "/dashboard/timetable", icon: "\u{1F4C5}" },
    { label: "Gradebook", href: "/dashboard/gradebook", icon: "\u{1F4D2}" },
    { label: "Analytics", href: "/dashboard/analytics", icon: "\u{1F4C8}" },
    { label: "Leave & OD", href: "/dashboard/leave", icon: "\u{1F3D6}\uFE0F" },
  ],
  tpo: [
    { label: "Overview", href: "/dashboard", icon: "\u{1F3E0}" },
    { label: "Drives", href: "/dashboard/drives", icon: "\u{1F3E2}" },
    {
      label: "Placement Analytics",
      href: "/dashboard/placement-analytics",
      icon: "\u{1F4C8}",
    },
    {
      label: "Applications",
      href: "/dashboard/applications",
      icon: "\u{1F4E8}",
    },
    { label: "Recruiters", href: "/dashboard/recruiters", icon: "\u{1F91D}" },
    // Class analytics + at-risk students (backend analytics staff_only includes tpo).
    { label: "Student Analytics", href: "/dashboard/analytics", icon: "\u{1F4CA}" },
  ],
  recruiter: [
    { label: "Overview", href: "/dashboard", icon: "\u{1F3E0}" },
    {
      label: "Candidates",
      href: "/dashboard/recruiter-candidates",
      icon: "\u{1F465}",
    },
    {
      label: "Offers",
      href: "/dashboard/recruiter-offers",
      icon: "\u{1F4DC}",
    },
  ],
  admin: [
    { label: "Overview", href: "/dashboard", icon: "\u{1F3E0}" },
    {
      label: "Institute KPIs",
      href: "/dashboard/institute",
      icon: "\u{1F3DB}\uFE0F",
    },
    { label: "Users", href: "/dashboard/users", icon: "\u{1F465}" },
    { label: "Departments", href: "/dashboard/departments", icon: "\u{1F3EB}" },
    {
      label: "Announcements",
      href: "/dashboard/announcements",
      icon: "\u{1F4E3}",
    },
    { label: "Calendar", href: "/dashboard/calendar", icon: "\u{1F5D3}\uFE0F" },
    { label: "Assignments", href: "/dashboard/assignments", icon: "\u{1F4DD}" },
    { label: "Study Hub", href: "/dashboard/study-hub", icon: "\u{1F4DA}" },
    { label: "Doubts", href: "/dashboard/doubts", icon: "\u{1F4AC}" },
    { label: "Timetable", href: "/dashboard/timetable", icon: "\u{1F4C5}" },
    { label: "Leave & OD", href: "/dashboard/leave", icon: "\u{1F3D6}\uFE0F" },
    { label: "Analytics", href: "/dashboard/analytics", icon: "\u{1F4C8}" },
    {
      label: "Placement Analytics",
      href: "/dashboard/placement-analytics",
      icon: "\u{1F3E2}",
    },
    // Recruiter onboarding, invites and drive links (backend recruiters staff_only = tpo + admin).
    { label: "Recruiters", href: "/dashboard/recruiters", icon: "\u{1F91D}" },
    { label: "Audit Log", href: "/dashboard/audit", icon: "\u{1F6E1}\uFE0F" },
  ],
};
