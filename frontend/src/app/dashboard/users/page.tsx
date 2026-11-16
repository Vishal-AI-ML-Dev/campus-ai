/**
 * Campus AI - Admin: Users & RBAC.
 *
 * Place this file at: src/app/dashboard/users/page.tsx
 *
 * Admin manages every account: filter by role, create users, change a user's
 * role, enable/disable accounts, and assign a student to a section (which is
 * what fills teacher rosters). The backend blocks changing your own role or
 * disabling yourself, so those controls are hidden for the signed-in admin.
 *
 * Backend endpoints:
 *   GET   /admin/users?role={role}                -> [{ id, email, full_name, role, is_active, section_id }]
 *   POST  /admin/users                             body { email, full_name, password, role }
 *   PATCH /admin/users/{id}/role                   body { role }
 *   PATCH /admin/users/{id}/status                 body { is_active }
 *   PATCH /admin/users/{id}/section                body { section_id }   (null clears)
 *   GET   /admin/departments                       -> [{ id, name, code }]
 *   GET   /admin/departments/{id}/sections          -> [{ id, name, year, department_id }]
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useCurrentUser, type Role } from "@/lib/auth";
import { BulkImportPanel } from "@/components/admin/BulkImportPanel";

type User = {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  section_id: number | null;
};
type Department = { id: number; name: string; code: string };
type Section = {
  id: number;
  name: string;
  year: number | null;
  department_id: number;
};
type SectionOption = { id: number; label: string };
type InviteStatus = "pending" | "accepted" | "revoked" | "expired";
type TenantInvite = {
  id: number;
  tenant_id: number;
  email: string;
  role: Role;
  status: InviteStatus;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
};
type TenantInviteCreated = {
  invite: TenantInvite;
  token: string;
  accept_path: string;
};

const ROLES: Role[] = ["student", "teacher", "tpo", "admin"];

const inputClass =
  "mt-1 w-full rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-400";

const smallSelect =
  "rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 px-2 py-1 text-xs outline-none focus:border-indigo-400";

export default function UsersPage() {
  const { user: me } = useCurrentUser();

  const [users, setUsers] = useState<User[]>([]);
  const [roleFilter, setRoleFilter] = useState<"" | Role>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const [sectionOptions, setSectionOptions] = useState<SectionOption[]>([]);

  // Add-user form
  const [aEmail, setAEmail] = useState("");
  const [aName, setAName] = useState("");
  const [aPassword, setAPassword] = useState("");
  const [aRole, setARole] = useState<Role>("student");
  const [creating, setCreating] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [addSuccess, setAddSuccess] = useState<string | null>(null);

  // Invite-user form
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("student");
  const [inviteDays, setInviteDays] = useState("14");
  const [invites, setInvites] = useState<TenantInvite[]>([]);
  const [inviteLoading, setInviteLoading] = useState(true);
  const [inviteCreating, setInviteCreating] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteSuccess, setInviteSuccess] = useState<string | null>(null);
  const [latestInviteLink, setLatestInviteLink] = useState<string | null>(null);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const path = roleFilter
        ? `/admin/users?role=${roleFilter}`
        : "/admin/users";
      setUsers(await api.get<User[]>(path));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load users.");
    } finally {
      setLoading(false);
    }
  }, [roleFilter]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const loadInvites = useCallback(async () => {
    setInviteLoading(true);
    setInviteError(null);
    try {
      setInvites(await api.get<TenantInvite[]>("/tenants/invites"));
    } catch (err) {
      setInviteError(
        err instanceof ApiError ? err.message : "Could not load invites.",
      );
    } finally {
      setInviteLoading(false);
    }
  }, []);

  useEffect(() => {
    loadInvites();
  }, [loadInvites]);

  // Load all sections once for the assign dropdown + label lookup.
  useEffect(() => {
    async function loadStructure() {
      try {
        const depts = await api.get<Department[]>("/admin/departments");
        const lists = await Promise.all(
          depts.map((d) =>
            api.get<Section[]>(`/admin/departments/${d.id}/sections`),
          ),
        );
        const opts: SectionOption[] = [];
        depts.forEach((d, i) => {
          lists[i].forEach((s) => {
            const label = `${d.code} - ${s.name}${
              s.year ? ` (Y${s.year})` : ""
            }`;
            opts.push({ id: s.id, label });
          });
        });
        setSectionOptions(opts);
      } catch {
        // non-fatal: assign dropdown just stays empty
      }
    }
    loadStructure();
  }, []);

  async function changeRole(u: User, role: Role) {
    if (role === u.role) return;
    setBusyKey(`r${u.id}`);
    setError(null);
    try {
      await api.patch(`/admin/users/${u.id}/role`, { role });
      await loadUsers();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not change role.",
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function toggleStatus(u: User) {
    setBusyKey(`s${u.id}`);
    setError(null);
    try {
      await api.patch(`/admin/users/${u.id}/status`, {
        is_active: !u.is_active,
      });
      await loadUsers();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not update status.",
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function assignSection(u: User, value: string) {
    setBusyKey(`sec${u.id}`);
    setError(null);
    try {
      const payload = { section_id: value ? Number(value) : null };
      await api.patch(`/admin/users/${u.id}/section`, payload);
      await loadUsers();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not assign section.",
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function createUser() {
    if (!aEmail.trim() || !aName.trim() || !aPassword) {
      setAddError("Email, name and password are required.");
      return;
    }
    setCreating(true);
    setAddError(null);
    setAddSuccess(null);
    try {
      const payload = {
        email: aEmail.trim(),
        full_name: aName.trim(),
        password: aPassword,
        role: aRole,
      };
      await api.post("/admin/users", payload);
      setAddSuccess(`User "${aName.trim()}" created.`);
      setAEmail("");
      setAName("");
      setAPassword("");
      setARole("student");
      await loadUsers();
    } catch (err) {
      setAddError(
        err instanceof ApiError ? err.message : "Could not create user.",
      );
    } finally {
      setCreating(false);
    }
  }

  async function createInvite() {
    if (!inviteEmail.trim()) {
      setInviteError("Email is required.");
      return;
    }
    setInviteCreating(true);
    setInviteError(null);
    setInviteSuccess(null);
    setLatestInviteLink(null);
    try {
      const res = await api.post<TenantInviteCreated>("/tenants/invites", {
        email: inviteEmail.trim(),
        role: inviteRole,
        expires_in_days: Number(inviteDays) || 14,
      });
      const link = `${window.location.origin}${res.accept_path}`;
      setLatestInviteLink(link);
      setInviteSuccess(`Invite created for ${res.invite.email}. Copy the link below and send it to the user.`);
      setInviteEmail("");
      setInviteRole("student");
      setInviteDays("14");
      await loadInvites();
    } catch (err) {
      setInviteError(
        err instanceof ApiError ? err.message : "Could not create invite.",
      );
    } finally {
      setInviteCreating(false);
    }
  }

  async function revokeInvite(invite: TenantInvite) {
    setBusyKey(`invite${invite.id}`);
    setInviteError(null);
    try {
      await api.post(`/tenants/invites/${invite.id}/revoke`);
      await loadInvites();
    } catch (err) {
      setInviteError(
        err instanceof ApiError ? err.message : "Could not revoke invite.",
      );
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div>
      <h2 className="text-2xl font-bold">Users & Roles</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        Manage accounts, roles and access. Assign students to a section to fill
        teacher rosters. You cannot change your own role or disable yourself.
      </p>

      {/* Add user */}
      <div className="mt-6 rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 p-6">
        <h3 className="text-lg font-semibold">Add a user</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="text-sm text-slate-500 dark:text-slate-400">Full name</label>
            <input
              value={aName}
              onChange={(e) => setAName(e.target.value)}
              className={inputClass}
              placeholder="Asha Verma"
            />
          </div>
          <div>
            <label className="text-sm text-slate-500 dark:text-slate-400">Email</label>
            <input
              value={aEmail}
              onChange={(e) => setAEmail(e.target.value)}
              className={inputClass}
              placeholder="asha@campus.ai"
            />
          </div>
          <div>
            <label className="text-sm text-slate-500 dark:text-slate-400">Temp password</label>
            <input
              type="text"
              value={aPassword}
              onChange={(e) => setAPassword(e.target.value)}
              className={inputClass}
              placeholder="test1234"
            />
          </div>
          <div>
            <label className="text-sm text-slate-500 dark:text-slate-400">Role</label>
            <select
              value={aRole}
              onChange={(e) => setARole(e.target.value as Role)}
              className={inputClass}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
        </div>
        {addError && (
          <p className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {addError}
          </p>
        )}
        {addSuccess && (
          <p className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-600 dark:text-emerald-300">
            {addSuccess}
          </p>
        )}
        <button
          onClick={createUser}
          disabled={creating}
          className="mt-3 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-40"
        >
          {creating ? "Creating..." : "Add user"}
        </button>
      </div>

      {/* Bulk CSV import */}
      <div className="mt-6">
        <BulkImportPanel onImported={loadUsers} />
      </div>

      {/* Invite links */}
      <div className="mt-6 rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 p-6">
        <h3 className="text-lg font-semibold">Invite user with set-password link</h3>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Send a single-use invite link so teachers, TPOs, admins, or students can set their own password.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="lg:col-span-2">
            <label className="text-sm text-slate-500 dark:text-slate-400">Email</label>
            <input
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className={inputClass}
              placeholder="new.user@college.edu"
            />
          </div>
          <div>
            <label className="text-sm text-slate-500 dark:text-slate-400">Role</label>
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as Role)}
              className={inputClass}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm text-slate-500 dark:text-slate-400">Expires in days</label>
            <input
              type="number"
              min={1}
              max={90}
              value={inviteDays}
              onChange={(e) => setInviteDays(e.target.value)}
              className={inputClass}
            />
          </div>
        </div>
        {inviteError && (
          <p className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {inviteError}
          </p>
        )}
        {inviteSuccess && (
          <p className="mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-600 dark:text-emerald-300">
            {inviteSuccess}
          </p>
        )}
        {latestInviteLink && (
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm dark:border-white/10 dark:bg-slate-950">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">Invite link</p>
            <code className="mt-1 block break-all text-indigo-600 dark:text-indigo-300">{latestInviteLink}</code>
          </div>
        )}
        <button
          onClick={createInvite}
          disabled={inviteCreating}
          className="mt-3 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-40"
        >
          {inviteCreating ? "Creating invite..." : "Create invite link"}
        </button>

        <div className="mt-5">
          <h4 className="text-sm font-semibold">Recent invites</h4>
          {inviteLoading ? (
            <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">Loading invites...</p>
          ) : invites.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">No invites yet.</p>
          ) : (
            <div className="mt-2 space-y-2">
              {invites.map((invite) => (
                <div
                  key={invite.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 px-3 py-2 text-sm dark:border-white/10"
                >
                  <div>
                    <p className="font-medium">{invite.email}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {invite.role} · {invite.status} · expires {new Date(invite.expires_at).toLocaleDateString()}
                    </p>
                  </div>
                  {invite.status === "pending" && (
                    <button
                      onClick={() => revokeInvite(invite)}
                      disabled={busyKey === `invite${invite.id}`}
                      className="rounded-lg border border-red-400/30 px-3 py-1 text-xs text-red-300 transition hover:bg-red-400/10 disabled:opacity-40"
                    >
                      Revoke
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Filter */}
      <div className="mt-6 flex items-center gap-3">
        <label className="text-sm text-slate-500 dark:text-slate-400">Filter by role</label>
        <select
          value={roleFilter}
          onChange={(e) => setRoleFilter(e.target.value as "" | Role)}
          className={smallSelect}
        >
          <option value="">All</option>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <p className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      )}

      {/* List */}
      <div className="mt-4">
        {loading ? (
          <p className="text-slate-500 dark:text-slate-400">Loading users...</p>
        ) : users.length === 0 ? (
          <p className="text-slate-500 dark:text-slate-400">No users match this filter.</p>
        ) : (
          <div className="space-y-2">
            {users.map((u) => {
              const isSelf = me?.id === u.id;
              return (
                <div
                  key={u.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 px-4 py-3"
                >
                  <div className="min-w-[180px]">
                    <p className="font-medium">
                      {u.full_name}
                      {isSelf && (
                        <span className="ml-2 text-xs text-indigo-600 dark:text-indigo-300">
                          (you)
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{u.email}</p>
                    {!u.is_active && (
                      <span className="text-xs text-red-300">Disabled</span>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    {/* Role */}
                    <select
                      value={u.role}
                      onChange={(e) => changeRole(u, e.target.value as Role)}
                      disabled={isSelf || busyKey === `r${u.id}`}
                      className={smallSelect}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>

                    {/* Section (mainly for students) */}
                    <select
                      value={u.section_id ?? ""}
                      onChange={(e) => assignSection(u, e.target.value)}
                      disabled={busyKey === `sec${u.id}`}
                      className={smallSelect}
                      title="Assign section"
                    >
                      <option value="">No section</option>
                      {sectionOptions.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.label}
                        </option>
                      ))}
                    </select>

                    {/* Enable / disable */}
                    <button
                      onClick={() => toggleStatus(u)}
                      disabled={isSelf || busyKey === `s${u.id}`}
                      className={`rounded-lg border px-3 py-1 text-xs transition disabled:opacity-40 ${
                        u.is_active
                          ? "border-red-400/30 text-red-300 hover:bg-red-400/10"
                          : "border-emerald-400/30 text-emerald-600 dark:text-emerald-300 hover:bg-emerald-400/10"
                      }`}
                    >
                      {u.is_active ? "Disable" : "Enable"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Hint about current section labels when no structure is loaded */}
      {sectionOptions.length === 0 && (
        <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
          Tip: create departments and sections first (Departments page) so they
          show up in the section dropdown here.
        </p>
      )}
    </div>
  );
}
