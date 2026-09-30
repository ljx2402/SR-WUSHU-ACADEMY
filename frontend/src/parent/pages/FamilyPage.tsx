import { Link } from "react-router";

import type { Family } from "../../api/types";
import { useMe } from "../../auth/AuthProvider";
import { PageHeader } from "../../layout/PageHeader";
import { Card } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { DefinitionList, Section, StudentStatusBadge } from "../components";
import { useParent } from "../ParentContext";
import { useFamilies, useStudents } from "../queries";

/**
 * The family as the academy records it: its students. Invoices are issued to
 * the family as a whole; there is no billing contact or "bill to" person.
 */
export function FamilyPage() {
  const me = useMe();
  const { children } = useParent();
  const families = useFamilies();
  const students = useStudents(children.map((c) => c.id));
  const byId = new Map(children.map((child, i) => [child.id, students[i]]));

  return (
    <>
      <PageHeader title="My family" crumbs={[{ label: "Overview", to: "/parent/dashboard" }]}
                  description="Your family’s students. One family invoice can cover charges for several children." />
      {families.isPending ? <LoadingState /> : families.isError ? (
        <ErrorState error={families.error} onRetry={() => families.refetch()} />
      ) : !families.data.results.length && !children.length ? (
        <EmptyState message="No children found." />
      ) : (
        families.data.results.map((family) => (
          <FamilySection key={family.id} family={family} own={byId} />
        ))
      )}

      {me.parent ? (
        <Section title="Your contact details">
          <DefinitionList items={[
            ["Name", me.parent.full_name],
            ["Phone", me.parent.phone],
            ["Email", me.parent.email],
          ]} />
          <p className="muted">To change your details, please contact the academy office.</p>
        </Section>
      ) : null}
    </>
  );
}

function FamilySection({ family, own }: { family: Family; own: Map<number, ReturnType<typeof useStudents>[number]> }) {
  return (
    <Section title={family.name}>
      <p className="muted">{family.students.length} {family.students.length === 1 ? "student" : "students"}</p>
      {family.students.length ? (
        <div className="grid grid-cards">
          {family.students.map((member) => {
            const detail = own.get(member.id);
            return (
              <Card key={member.id} as="article" title={member.full_name}
                    actions={detail?.data ? <StudentStatusBadge status={detail.data.status} /> : null}>
                <p className="muted">Student no. {member.student_no}</p>
                {detail ? (
                  detail.isPending ? <LoadingState /> : detail.isError ? <ErrorState error={detail.error} /> : (
                    <>
                      <p>{detail.data.current_classes?.length
                        ? `${detail.data.current_classes.length} ${detail.data.current_classes.length === 1 ? "class" : "classes"}: ${detail.data.current_classes.map((c) => c.class_name).join(", ")}`
                        : "Not in a class at the moment"}</p>
                      <p><Link to={`/parent/students/${member.id}`}>View profile</Link></p>
                    </>
                  )
                ) : <p className="muted">Linked to another guardian in this family.</p>}
              </Card>
            );
          })}
        </div>
      ) : <EmptyState message="No children found." />}
    </Section>
  );
}
