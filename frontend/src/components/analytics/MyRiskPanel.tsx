"use client";

// Student self-assessment: own risk score + explainable factor breakdown.
import { useApiResource } from "@/lib/useApiResource";
import { ErrorState, LoadingState } from "@/components/ui/states";
import { BAND_STYLES, StudentRisk, fmt, riskColor } from "./analyticsApi";

export default function MyRiskPanel() {
  const {
    data: risk,
    loading,
    error,
    requestId,
    reload,
  } = useApiResource<StudentRisk>("/analytics/me", {
    fallbackMessage: "Failed to load your assessment",
  });

  if (loading) return <LoadingState message={"Loading your assessment\u2026"} />;
  if (error)
    return (
      <ErrorState message={error} onRetry={reload} requestId={requestId} />
    );
  if (!risk) return null;

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-slate-900 p-5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-500 dark:text-slate-400">Your risk score</p>
            <p className="text-4xl font-bold text-gray-900 dark:text-slate-100">
              {risk.risk_score}
            </p>
          </div>
          <span
            className={`rounded-full px-3 py-1 text-sm font-medium ${BAND_STYLES[risk.band]}`}
          >
            {risk.band.toUpperCase()} risk
          </span>
        </div>
        {risk.reasons.length > 0 && (
          <ul className="mt-4 list-inside list-disc space-y-1 text-sm text-gray-700 dark:text-slate-300">
            {risk.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className="mb-2 text-sm font-medium text-gray-700 dark:text-slate-300">
          How it&apos;s calculated
        </p>
        <div className="space-y-3">
          {risk.factors.map((f) => (
            <div
              key={f.key}
              className="rounded-lg border border-gray-200 dark:border-white/10 bg-white dark:bg-slate-900 p-3"
            >
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium text-gray-900 dark:text-slate-100">{f.label}</span>
                <span className="text-gray-500 dark:text-slate-400">
                  weight {Math.round(f.weight * 100)}%
                </span>
              </div>
              {f.available ? (
                <>
                  <div className="mt-2 flex items-center gap-3 text-sm">
                    <span className="text-gray-700 dark:text-slate-300">
                      Value: {fmt(f.value, f.key === "academics" ? "" : "%")}
                    </span>
                    <span className="text-gray-500 dark:text-slate-400">Risk: {f.risk}</span>
                  </div>
                  <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-slate-800">
                    <div
                      className={riskColor(f.risk ?? 0)}
                      style={{ width: `${f.risk ?? 0}%` }}
                    />
                  </div>
                </>
              ) : (
                <p className="mt-2 text-sm text-gray-400 dark:text-slate-500">
                  No data yet — not counted in your score.
                </p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
