import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router";

import type { CompetitionInput, StaffCompetition } from "../../api/types";
import { useServices } from "../../app/services";
import { PageHeader } from "../../layout/PageHeader";
import { COMPETITION_STATUS } from "../../parent/components";
import { errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { SelectField, TextAreaField, TextField } from "../../ui/Field";
import { Alert } from "../../ui/primitives";
import { COMP_CRUMBS, COMP_ROOT, competitionKeys, fieldErrorsOf, intOrNull } from "../components";
import { WithCompetition } from "./StaffCompetitionDetailPage";

/*
 * Create or edit a competition through the existing endpoint
 * (/api/competitions/): the same fields and the same statuses as the admin.
 * The registration form and events are edited on their own pages.
 */

export function NewCompetitionPage() {
  return (
    <>
      <PageHeader title="New competition" crumbs={COMP_CRUMBS}
                  description="Starts as a draft: only staff see it until its status is set to open." />
      <CompetitionForm competition={null} />
    </>
  );
}

export function EditCompetitionPage() {
  const { competitionId = "" } = useParams();
  return (
    <WithCompetition title="Edit competition">
      {(c) => (
        <>
          <PageHeader title={`Edit ${c.name}`}
                      crumbs={[...COMP_CRUMBS, { label: c.name, to: `${COMP_ROOT}/${competitionId}` }]} />
          <CompetitionForm competition={c} />
        </>
      )}
    </WithCompetition>
  );
}

function initial(c: StaffCompetition | null) {
  return {
    name: c?.name ?? "", organiser: c?.organiser ?? "", venue: c?.venue ?? "", start_date: c?.start_date ?? "",
    end_date: c?.end_date ?? "", registration_deadline: c?.registration_deadline ?? "", status: c?.status ?? "DRAFT",
    allow_parent_registration: c?.allow_parent_registration ?? true,
    allow_parent_withdrawal: c?.allow_parent_withdrawal ?? true,
    max_events_per_student: c?.max_events_per_student?.toString() ?? "", age_reference_date: c?.age_reference_date ?? "",
    description: c?.description ?? "", rules: c?.rules ?? "",
  };
}

function CompetitionForm({ competition }: { competition: StaffCompetition | null }) {
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState(() => initial(competition));
  const save = useMutation({
    mutationFn: () => {
      const body: CompetitionInput = {
        ...form, name: form.name.trim(), organiser: form.organiser.trim(), venue: form.venue.trim(),
        status: form.status as CompetitionInput["status"],
        max_events_per_student: intOrNull(form.max_events_per_student),
        age_reference_date: form.age_reference_date || null,
      };
      return competition ? endpoints.updateCompetition(competition.id, body) : endpoints.createCompetition(body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: competitionKeys.all });
      navigate(`${COMP_ROOT}/${saved.id}`);
    },
  });
  const errors = fieldErrorsOf(save.error);
  const set = (name: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value });
  const check = (name: "allow_parent_registration" | "allow_parent_withdrawal") =>
    (e: { target: { checked: boolean } }) => setForm({ ...form, [name]: e.target.checked });
  function submit(e: FormEvent) { e.preventDefault(); save.mutate(); }
  const cancelTo = competition ? `${COMP_ROOT}/${competition.id}` : COMP_ROOT;
  return (
    <form className="competition-form" onSubmit={submit} noValidate>
      <TextField label="Name" required value={form.name} onChange={set("name")} errors={errors.name} maxLength={200} />
      <TextField label="Organiser" value={form.organiser} onChange={set("organiser")} errors={errors.organiser} />
      <TextField label="Venue" value={form.venue} onChange={set("venue")} errors={errors.venue} />
      <div className="form-grid">
        <TextField label="Start date" type="date" required value={form.start_date} onChange={set("start_date")} errors={errors.start_date} />
        <TextField label="End date" type="date" required value={form.end_date} onChange={set("end_date")} errors={errors.end_date} />
      </div>
      <div className="form-grid">
        <TextField label="Registration deadline" type="date" required value={form.registration_deadline}
                   onChange={set("registration_deadline")} errors={errors.registration_deadline} />
        <TextField label="Ages counted on" type="date" value={form.age_reference_date} onChange={set("age_reference_date")}
                   errors={errors.age_reference_date} hint="Empty: the start date." />
      </div>
      <SelectField label="Status" value={form.status} onChange={set("status")} errors={errors.status}
                   hint="Parents register only while the status is open, the deadline has not passed and the form is published.">
        {Object.entries(COMPETITION_STATUS).map(([value, s]) => <option key={value} value={value}>{s.label}</option>)}
      </SelectField>
      <TextField label="Events per student" inputMode="numeric" value={form.max_events_per_student}
                 onChange={set("max_events_per_student")} errors={errors.max_events_per_student} hint="Empty for no limit." />
      <label className="check-field"><input type="checkbox" checked={form.allow_parent_registration}
        onChange={check("allow_parent_registration")} /> Parents may register their children</label>
      <label className="check-field"><input type="checkbox" checked={form.allow_parent_withdrawal}
        onChange={check("allow_parent_withdrawal")} /> Parents may withdraw before the deadline</label>
      <TextAreaField label="Description" rows={3} value={form.description} onChange={set("description")} errors={errors.description} />
      <TextAreaField label="Rules" rows={4} value={form.rules} onChange={set("rules")} errors={errors.rules} />
      {save.error ? <Alert tone="danger" role="alert" title={errorText(save.error)} /> : null}
      <div className="button-row">
        <Button type="submit" busy={save.isPending}>{competition ? "Save changes" : "Create competition"}</Button>
        <Button variant="secondary" onClick={() => navigate(cancelTo)}>Cancel</Button>
      </div>
    </form>
  );
}
