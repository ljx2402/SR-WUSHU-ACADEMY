import { useQuery } from "@tanstack/react-query";

import { allPages } from "../../api/endpoints";
import type { CompetitionRegistration } from "../../api/types";
import { useServices } from "../../app/services";
import { formatDate, formatPeriod } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Section } from "../../parent/components";
import { DataTable } from "../../ui/DataTable";
import { Alert, Badge } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { COACH_ENTRY_STATUS, coachKeys } from "../components";

const MEDAL: Record<string, string> = { GOLD: "Gold", SILVER: "Silver", BRONZE: "Bronze", NONE: "No medal" };

/**
 * Competition entries of the students the coach coaches (the backend's
 * registration scope). Coaches see who is entered in which event, the entry
 * status and results; never the family's form answers, notes, fees, invoices
 * or payments (the API does not send them to coaches).
 */
export function CoachCompetitionsPage() {
  const { endpoints } = useServices();
  const entries = useQuery({
    queryKey: coachKeys.registrations,
    queryFn: ({ signal }) => allPages((page) => endpoints.registrations({ page }, signal), 10),
  });
  const competitions = useQuery({
    queryKey: coachKeys.competitions,
    queryFn: ({ signal }) => allPages((page) => endpoints.competitions({ page }, signal), 5),
  });

  const byCompetition = new Map<number, CompetitionRegistration[]>();
  for (const entry of entries.data?.rows ?? []) {
    byCompetition.set(entry.competition, [...(byCompetition.get(entry.competition) ?? []), entry]);
  }
  const info = new Map((competitions.data?.rows ?? []).map((c) => [c.id, c]));

  return (
    <>
      <PageHeader title="Competitions" crumbs={[{ label: "Coach dashboard", to: "/coach/dashboard" }]}
                  description="Competition entries of the students you coach." />
      {entries.isPending || competitions.isPending ? <LoadingState /> : entries.isError ? (
        <ErrorState error={entries.error} onRetry={() => entries.refetch()} />
      ) : !byCompetition.size ? <EmptyState message="None of your students are entered in a competition." /> : (
        <>
          {!entries.data.complete ? <Alert tone="warning">Only the first {entries.data.rows.length} entries are shown.</Alert> : null}
          {[...byCompetition.entries()].map(([id, list]) => {
            const c = info.get(id);
            const active = list.filter((e) => e.status === "PENDING" || e.status === "CONFIRMED");
            return (
              <Section key={id} title={list[0].competition_name}>
                {c ? (
                  <p className="muted">{formatPeriod(c.start_date, c.end_date)}{c.venue ? ` · ${c.venue}` : ""} · Registration
                    {" "}deadline {formatDate(c.registration_deadline)}</p>
                ) : null}
                <p className="muted">{active.length} active {active.length === 1 ? "entry" : "entries"}</p>
                <DataTable<CompetitionRegistration>
                  caption={`Entries in ${list[0].competition_name}`}
                  rows={[...list].sort((a, b) => a.event_name.localeCompare(b.event_name) || a.student_name.localeCompare(b.student_name))}
                  rowKey={(e) => e.id}
                  columns={[
                    { key: "student", header: "Student", render: (e) => e.student_name },
                    { key: "event", header: "Event", render: (e) => e.event_name },
                    { key: "status", header: "Entry", render: (e) => {
                      const st = COACH_ENTRY_STATUS[e.status] ?? { label: e.status, tone: "neutral" as const };
                      return <Badge tone={st.tone}>{st.label}</Badge>;
                    } },
                    { key: "result", header: "Result", render: (e) => (e.result
                      ? [e.result.placing ? `Placing ${e.result.placing}` : null, MEDAL[e.result.medal] ?? null, e.result.score ? `Score ${e.result.score}` : null]
                        .filter(Boolean).join(" · ") || "—"
                      : "—") },
                  ]}
                />
              </Section>
            );
          })}
        </>
      )}
    </>
  );
}
