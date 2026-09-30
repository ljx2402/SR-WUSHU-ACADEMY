import { useQuery } from "@tanstack/react-query";

import type { StudentSelfProfile } from "../../api/types";
import { useServices } from "../../app/services";
import { formatDate } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, Section, StudentStatusBadge } from "../../parent/components";
import { DataTable } from "../../ui/DataTable";
import { ErrorState, LoadingState } from "../../ui/states";
import { studentKeys } from "../components";

/**
 * The student's basic profile, read only. Personal and family details (IC,
 * contact details, guardians, medical notes) are not sent to student logins;
 * changes go through the academy or a parent.
 */
export function MyProfilePage() {
  const { endpoints } = useServices();
  const profile = useQuery({ queryKey: studentKeys.profile, queryFn: ({ signal }) => endpoints.myProfile(signal) });
  return (
    <>
      <PageHeader title="My profile" crumbs={[{ label: "My dashboard", to: "/student/dashboard" }]}
                  description="Your details can only be changed by the academy." />
      {profile.isPending ? <LoadingState /> : profile.isError ? (
        <ErrorState error={profile.error} onRetry={() => profile.refetch()} />
      ) : (
        <>
          <Section title="Student">
            <DefinitionList items={[
              ["Name", profile.data.full_name],
              ["Chinese name", profile.data.chinese_name],
              ["Student no.", profile.data.student_no],
              ["Gender", profile.data.gender === "M" ? "Male" : "Female"],
              ["Age", profile.data.age],
              ["Status", <StudentStatusBadge key="status" status={profile.data.status} />],
              ["Joined", profile.data.join_date ? formatDate(profile.data.join_date) : ""],
            ]} />
          </Section>
          <Section title="My classes">
            <DataTable<StudentSelfProfile["current_classes"][number]>
              caption="My current classes"
              rows={profile.data.current_classes}
              rowKey={(c) => `${c.class_name}-${c.start_date}`}
              emptyMessage="You are not in a class at the moment."
              columns={[
                { key: "class", header: "Class", render: (c) => c.class_name },
                { key: "team", header: "Team", render: (c) => c.team_name ?? "—" },
                { key: "since", header: "Since", render: (c) => formatDate(c.start_date) },
              ]}
            />
          </Section>
        </>
      )}
    </>
  );
}
