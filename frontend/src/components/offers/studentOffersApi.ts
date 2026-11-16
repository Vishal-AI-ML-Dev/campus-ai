// Typed API helpers for the STUDENT side of placement offers.
// Mirrors the backend routes a student can call:
//   GET   /recruiters/offers/mine            - offers extended to me
//   PATCH /recruiters/offers/{id}/respond    - accept / decline (once)
// The offer shape and presentation helpers are shared with the recruiter
// portal so both sides render offers identically.
import { api } from "@/lib/api";
import type { Offer } from "@/components/recruiter/recruiterApi";

export type { Offer, OfferStatus } from "@/components/recruiter/recruiterApi";
export {
  OFFER_STYLES,
  fmtDate,
  fmtPackage,
  pretty,
} from "@/components/recruiter/recruiterApi";

export function listMyOffers(): Promise<Offer[]> {
  return api.get("/recruiters/offers/mine") as Promise<Offer[]>;
}

export function respondToOffer(
  offerId: number,
  accept: boolean,
  note?: string,
): Promise<Offer> {
  return api.patch(`/recruiters/offers/${offerId}/respond`, {
    accept,
    note: note?.trim() ? note.trim() : null,
  }) as Promise<Offer>;
}

/** True when an offer's expiry date (YYYY-MM-DD) is before today. */
export function isPastExpiry(expiresOn: string | null): boolean {
  if (!expiresOn) return false;
  const end = new Date(`${expiresOn}T23:59:59`);
  return !Number.isNaN(end.getTime()) && end.getTime() < Date.now();
}
