"use client";

/**
 * Student offers panel.
 *
 * Lists every placement offer extended to the signed-in student and lets them
 * accept or decline an offer that is still `extended`. Responses are final on
 * the backend (a second response returns 409), so each action asks for an
 * explicit confirmation first. Already-responded and withdrawn offers are shown
 * read-only with their outcome.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  EmptyState,
  ErrorState,
  SkeletonList,
  Spinner,
} from "@/components/ui/states";
import {
  OFFER_STYLES,
  fmtDate,
  fmtPackage,
  isPastExpiry,
  listMyOffers,
  pretty,
  respondToOffer,
  type Offer,
} from "./studentOffersApi";

type Pending = { offerId: number; accept: boolean } | null;

export default function StudentOffersPanel() {
  const [offers, setOffers] = useState<Offer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | undefined>();
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [rowMsg, setRowMsg] = useState<Record<number, string>>({});
  const [announce, setAnnounce] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setRequestId(undefined);
    try {
      setOffers(await listMyOffers());
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not load your offers.",
      );
      setRequestId(err instanceof ApiError ? err.requestId : undefined);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCount = useMemo(
    () => offers.filter((o) => o.status === "extended").length,
    [offers],
  );

  async function confirmResponse(offer: Offer, accept: boolean) {
    setBusy(offer.id);
    setRowMsg((p) => ({ ...p, [offer.id]: "" }));
    try {
      const updated = await respondToOffer(offer.id, accept, notes[offer.id]);
      setOffers((prev) => prev.map((o) => (o.id === updated.id ? updated : o)));
      setAnnounce(
        `You ${accept ? "accepted" : "declined"} the ${offer.role_title} offer from ${offer.company_name}.`,
      );
      setPending(null);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.status === 409
            ? `${err.message} The list has been refreshed.`
            : err.message
          : "Could not send your response. Please try again.";
      setRowMsg((p) => ({ ...p, [offer.id]: msg }));
      setPending(null);
      // The offer changed under us (withdrawn / already answered): refresh.
      if (err instanceof ApiError && (err.status === 409 || err.status === 404)) {
        load();
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold">My offers</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Offers recruiters have extended to you. Your response is final once
            sent.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-50 dark:border-white/15 dark:hover:bg-white/10"
        >
          Refresh
        </button>
      </div>

      {/* Screen-reader confirmation of a completed response. */}
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>

      <div className="mt-6">
        {loading ? (
          <SkeletonList rows={2} />
        ) : error ? (
          <ErrorState message={error} requestId={requestId} onRetry={load} />
        ) : offers.length === 0 ? (
          <EmptyState
            title="No offers yet"
            description="When a recruiter extends an offer for a drive you were shortlisted on, it will appear here."
          />
        ) : (
          <>
            <p className="mb-4 text-sm text-slate-500 dark:text-slate-400">
              {openCount === 0
                ? "No offers are waiting for your response."
                : `${openCount} offer${openCount === 1 ? "" : "s"} awaiting your response.`}
            </p>
            <ul className="grid gap-4 md:grid-cols-2" aria-label="Your offers">
              {offers.map((o) => (
                <OfferCard
                  key={o.id}
                  offer={o}
                  note={notes[o.id] ?? ""}
                  onNote={(v) => setNotes((p) => ({ ...p, [o.id]: v }))}
                  pending={pending?.offerId === o.id ? pending.accept : null}
                  onAsk={(accept) => {
                    setRowMsg((p) => ({ ...p, [o.id]: "" }));
                    setPending({ offerId: o.id, accept });
                  }}
                  onCancel={() => setPending(null)}
                  onConfirm={(accept) => confirmResponse(o, accept)}
                  busy={busy === o.id}
                  message={rowMsg[o.id] ?? ""}
                />
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

function OfferCard({
  offer,
  note,
  onNote,
  pending,
  onAsk,
  onCancel,
  onConfirm,
  busy,
  message,
}: {
  offer: Offer;
  note: string;
  onNote: (v: string) => void;
  pending: boolean | null;
  onAsk: (accept: boolean) => void;
  onCancel: () => void;
  onConfirm: (accept: boolean) => void;
  busy: boolean;
  message: string;
}) {
  const open = offer.status === "extended";
  const expired = open && isPastExpiry(offer.expires_on);
  const headingId = `offer-${offer.id}-title`;
  const noteId = `offer-${offer.id}-note`;

  return (
    <li
      aria-labelledby={headingId}
      className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-slate-900"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id={headingId} className="break-words text-lg font-semibold">
            {offer.role_title}
          </h3>
          <p className="break-words text-sm text-slate-500 dark:text-slate-400">
            {offer.company_name}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${OFFER_STYLES[offer.status]}`}
        >
          {pretty(offer.status)}
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <dt className="text-slate-500 dark:text-slate-400">Package</dt>
        <dd>{fmtPackage(offer.package_lpa)}</dd>
        <dt className="text-slate-500 dark:text-slate-400">Location</dt>
        <dd className="break-words">{offer.location ?? "—"}</dd>
        <dt className="text-slate-500 dark:text-slate-400">Joining date</dt>
        <dd>{fmtDate(offer.joining_date)}</dd>
        <dt className="text-slate-500 dark:text-slate-400">Respond by</dt>
        <dd>{fmtDate(offer.expires_on)}</dd>
        <dt className="text-slate-500 dark:text-slate-400">Offered on</dt>
        <dd>{fmtDate(offer.created_at)}</dd>
      </dl>

      {offer.note && (
        <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600 dark:bg-white/5 dark:text-slate-300">
          <span className="font-medium">Note from the recruiter: </span>
          {offer.note}
        </p>
      )}

      {open ? (
        <div className="mt-4 border-t border-slate-200 pt-4 dark:border-white/10">
          {expired && (
            <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
              The respond-by date has passed. You can still respond, but check
              with the placement office that the offer still stands.
            </p>
          )}
          <label
            htmlFor={noteId}
            className="block text-sm font-medium text-slate-600 dark:text-slate-300"
          >
            Message to the recruiter (optional)
          </label>
          <textarea
            id={noteId}
            rows={2}
            maxLength={500}
            value={note}
            onChange={(e) => onNote(e.target.value)}
            disabled={busy}
            className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 disabled:opacity-60 dark:border-white/10 dark:bg-slate-900"
          />

          {pending === null ? (
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <button
                type="button"
                onClick={() => onAsk(true)}
                disabled={busy}
                aria-label={`Accept the ${offer.role_title} offer from ${offer.company_name}`}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
              >
                Accept
              </button>
              <button
                type="button"
                onClick={() => onAsk(false)}
                disabled={busy}
                aria-label={`Decline the ${offer.role_title} offer from ${offer.company_name}`}
                className="rounded-lg border border-red-500/40 px-4 py-2 text-sm font-medium text-red-600 transition hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300"
              >
                Decline
              </button>
            </div>
          ) : (
            <div
              role="group"
              aria-label="Confirm your response"
              className="mt-3 rounded-lg border border-slate-200 p-3 dark:border-white/10"
            >
              <p className="text-sm">
                {pending
                  ? "Accept this offer? This cannot be undone."
                  : "Decline this offer? This cannot be undone."}
              </p>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <button
                  type="button"
                  onClick={() => onConfirm(pending)}
                  disabled={busy}
                  className={`flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium text-white transition disabled:opacity-50 ${
                    pending
                      ? "bg-emerald-600 hover:bg-emerald-500"
                      : "bg-red-600 hover:bg-red-500"
                  }`}
                >
                  {busy && <Spinner />}
                  {busy
                    ? "Sending..."
                    : pending
                      ? "Yes, accept offer"
                      : "Yes, decline offer"}
                </button>
                <button
                  type="button"
                  onClick={onCancel}
                  disabled={busy}
                  className="rounded-lg border border-slate-300 px-4 py-2 text-sm transition hover:bg-slate-100 disabled:opacity-50 dark:border-white/15 dark:hover:bg-white/10"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      ) : (
        <p className="mt-4 border-t border-slate-200 pt-4 text-sm text-slate-600 dark:border-white/10 dark:text-slate-300">
          {offer.status === "withdrawn"
            ? "The company withdrew this offer."
            : `You ${offer.status} this offer${
                offer.responded_at ? ` on ${fmtDate(offer.responded_at)}` : ""
              }.`}
          {offer.student_response_note && offer.status !== "withdrawn" && (
            <span className="block text-slate-500 dark:text-slate-400">
              Your message: {offer.student_response_note}
            </span>
          )}
        </p>
      )}

      {message && (
        <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-300">
          {message}
        </p>
      )}
    </li>
  );
}
