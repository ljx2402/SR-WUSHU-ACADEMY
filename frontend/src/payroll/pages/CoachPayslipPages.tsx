import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { Payslip } from "../../api/types";
import { useServices } from "../../app/services";
import { MoneyAmount } from "../../finance/components";
import { PageHeader } from "../../layout/PageHeader";
import { LoadMore } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { DataTable } from "../../ui/DataTable";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { PayslipView, payrollKeys, periodName } from "../components";

/*
 * Coach "My payslips" (Phase 6H): the coach's own FINALIZED payslips only
 * (payroll.view_own; the API returns nothing else, and another coach's
 * payslip or an unfinalized one is 404). No bank details, no other coaches.
 */

const CRUMBS = [{ label: "Coach dashboard", to: "/coach/dashboard" }];

export function CoachPayslipsPage() {
  const { endpoints } = useServices();
  const list = usePagedList<Payslip>(payrollKeys.payslips({ mine: true }), (page, signal) => endpoints.payslips({ page }, signal));
  return (
    <>
      <PageHeader title="My payslips" crumbs={CRUMBS}
                  description="Your monthly payslips, once the academy has finalized the payroll. You are paid per session." />
      {list.isPending ? <LoadingState /> : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : (
        <>
          <DataTable<Payslip>
            caption="My payslips"
            rows={list.rows}
            rowKey={(p) => p.id}
            emptyMessage="No finalized payslips yet."
            className="staff-table"
            columns={[
              { key: "period", header: "Month", render: (p) => (
                <Link className="tap-link" to={`/coach/payslips/${p.id}`}>{periodName(p.year, p.month)}</Link>) },
              { key: "regular", header: "Regular sessions", align: "end", render: (p) => p.regular_sessions },
              { key: "substitute", header: "Substitute sessions", align: "end", render: (p) => p.substitute_sessions },
              { key: "net", header: "Net pay", align: "end", render: (p) => <MoneyAmount value={p.net_pay} /> },
            ]}
          />
          <LoadMore shown={list.rows.length} total={list.count} hasMore={!!list.hasNextPage}
                    loading={list.isFetchingNextPage} onMore={() => list.fetchNextPage()} />
        </>
      )}
    </>
  );
}

export function CoachPayslipDetailPage() {
  const { payslipId = "" } = useParams();
  const { endpoints } = useServices();
  const payslip = useQuery({ queryKey: payrollKeys.payslip(payslipId), queryFn: ({ signal }) => endpoints.payslip(payslipId, signal) });
  const crumbs = [...CRUMBS, { label: "My payslips", to: "/coach/payslips" }];
  if (payslip.isPending) return (<><PageHeader title="Payslip" crumbs={crumbs} /><LoadingState /></>);
  if (payslip.isError) {
    return (<><PageHeader title="Payslip" crumbs={crumbs} />
      {isApiError(payslip.error) && payslip.error.kind === "not_found" ? <NotFoundState message="Payslip not found." />
        : <ErrorState error={payslip.error} onRetry={() => payslip.refetch()} />}</>);
  }
  const p = payslip.data;
  return (
    <>
      <PageHeader title={`Payslip ${periodName(p.year, p.month)}`} crumbs={crumbs} />
      <PayslipView payslip={p} />
    </>
  );
}
