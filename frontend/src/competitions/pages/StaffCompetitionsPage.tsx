import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import type { StaffCompetition } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { formatDate, formatPeriod } from "../../domain/format";
import { useUrlFilters } from "../../finance/filters";
import { PageHeader } from "../../layout/PageHeader";
import { COMPETITION_STATUS, CompetitionStatusBadge, LoadMore } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { Button } from "../../ui/Button";
import { DataTable } from "../../ui/DataTable";
import { SelectField, TextField } from "../../ui/Field";
import { Badge } from "../../ui/primitives";
import { ErrorState, LoadingState } from "../../ui/states";
import { COMP_ROOT, competitionKeys } from "../components";

/** Competitions, filtered by the backend (?status=, ?search=). Statuses are the backend's own. */
export function StaffCompetitionsPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const { params, set } = useUrlFilters();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const query = { status: params.get("status") || undefined, search: params.get("search") || undefined };
  const list = usePagedList<StaffCompetition>(competitionKeys.list(query),
    (page, signal) => endpoints.staffCompetitions({ ...query, page }, signal));
  const seesEntries = can(me, "competition.registrations.view_all");
  function onSearch(e: FormEvent) { e.preventDefault(); set("search", search.trim()); }
  return (
    <>
      <PageHeader title="Competitions"
                  description="Competitions, their events and registration forms, participants and results."
                  actions={can(me, "competition.manage")
                    ? <Link className="btn btn-primary" to={`${COMP_ROOT}/new`}>New competition</Link> : null} />
      <form className="filter-bar" role="search" aria-label="Find competitions" onSubmit={onSearch}>
        <TextField label="Search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Name, organiser or venue" />
        <SelectField label="Status" value={params.get("status") ?? ""} onChange={(e) => set("status", e.target.value)}>
          <option value="">All statuses</option>
          {Object.entries(COMPETITION_STATUS).map(([value, s]) => <option key={value} value={value}>{s.label}</option>)}
        </SelectField>
        <div className="filter-actions"><Button type="submit">Search</Button></div>
      </form>
      {list.isPending ? <LoadingState /> : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : (
        <>
          <DataTable<StaffCompetition>
            caption="Competitions"
            rows={list.rows}
            rowKey={(c) => c.id}
            emptyMessage="No competitions match."
            className="staff-table"
            columns={[
              { key: "name", header: "Competition", render: (c) => (
                <Link className="tap-link" to={`${COMP_ROOT}/${c.id}`}>{c.name}</Link>) },
              { key: "dates", header: "Dates", render: (c) => formatPeriod(c.start_date, c.end_date) },
              { key: "deadline", header: "Register by", priority: "secondary", render: (c) => formatDate(c.registration_deadline) },
              { key: "status", header: "Status", render: (c) => <CompetitionStatusBadge status={c.status} /> },
              { key: "form", header: "Form", priority: "secondary", render: (c) => (
                c.registration_form.status === "PUBLISHED"
                  ? <Badge tone="success">Published v{c.registration_form.version}</Badge>
                  : <Badge tone="neutral">Not published</Badge>) },
              ...(seesEntries ? [
                { key: "entries", header: "Entries", align: "end" as const, render: (c: StaffCompetition) => c.entry_count ?? "—" },
                { key: "pending", header: "Awaiting payment", align: "end" as const,
                  render: (c: StaffCompetition) => c.pending_count ?? "—" },
              ] : []),
            ]}
          />
          <LoadMore shown={list.rows.length} total={list.count} hasMore={!!list.hasNextPage}
                    loading={list.isFetchingNextPage} onMore={() => list.fetchNextPage()} />
        </>
      )}
    </>
  );
}
