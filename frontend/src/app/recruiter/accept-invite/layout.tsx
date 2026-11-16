/**
 * Campus AI - layout for the recruiter invite-acceptance route.
 *
 * Server component so it can set page metadata for a page whose URL carries a
 * single-use invite token:
 *   - referrer "no-referrer": the token is never sent in a Referer header to
 *     any link or asset loaded from this page.
 *   - robots noindex/nofollow: invite links must never be indexed.
 */

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Accept recruiter invite | Campus AI",
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function RecruiterAcceptInviteLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
