import { useQuery } from "@tanstack/react-query";

import type { StudentCompetitionEntry } from "../../api/types";
import { useServices } from "../../app/services";
import { formatPeriod } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { CompetitionStatusBadge, DefinitionList } from "../../parent/components";
import { Card } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { EntryStatusBadge, ResultText, studentKeys } from "../components";

function eventDetails(e: StudentCompetitionEntry["event"]) {
  const age = e.min_age !== null || e.max_age !== null
    ? `Age ${e.min_age ?? "any"}–${e.max_age ?? "any"}` : null;
  return [e.gender === "M" ? "Boys / men" : e.gender === "F" ? "Girls / women" : null, age, e.weight_class || null]
    .filter(Boolean).join(" · ");
}

/**
 * The student's own competition entries and results. Registration, answers
 * and payment are the family's (Parent Portal); none of that is sent here.
 */
export function StudentCompetitionsPage() {
  const { endpoints } = useServices();
  const entries = useQuery({ queryKey: studentKeys.competitions, queryFn: ({ signal }) => endpoints.myCompetitions(signal) });
  return (
    <>
      <PageHeader title="My competitions" crumbs={[{ label: "My dashboard", to: "/student/dashboard" }]}
                  description="Competitions you are entered in, and your results. Your parent or guardian registers you." />
      {entries.isPending ? <LoadingState /> : entries.isError ? (
        <ErrorState error={entries.error} onRetry={() => entries.refetch()} />
      ) : !entries.data.length ? <EmptyState message="You are not entered in any competitions." /> : (
        <ul className="plain-list entry-cards">
          {entries.data.map((e) => (
            <li key={e.id}>
              <Card as="article" title={e.competition.name}>
                <p className="badge-row"><EntryStatusBadge status={e.status} /><CompetitionStatusBadge status={e.competition.status} /></p>
                <DefinitionList items={[
                  ["Dates", formatPeriod(e.competition.start_date, e.competition.end_date)],
                  ["Venue", e.competition.venue],
                  ["Event", e.event.name],
                  ["Category", eventDetails(e.event)],
                  ["Result", <ResultText key="result" result={e.result} />],
                ]} />
                {e.competition.rules ? (
                  <details className="rules">
                    <summary>Competition rules</summary>
                    <p className="prewrap">{e.competition.rules}</p>
                  </details>
                ) : null}
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
