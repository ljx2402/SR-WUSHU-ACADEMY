import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type {
  FormFieldDefinition, FormFieldType, RegistrationFormFieldInput, RegistrationFormFieldRow, StaffCompetition,
  StaffRegistrationForm,
} from "../../api/types";
import { useServices } from "../../app/services";
import { formatDateTime } from "../../domain/format";
import { Section } from "../../parent/components";
import { DynamicForm, type Answers } from "../../parent/DynamicForm";
import { ActionDialog, errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/Dialog";
import { SelectField, TextAreaField, TextField } from "../../ui/Field";
import { Alert, Badge, Card } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { FIELD_TYPE, competitionKeys, fieldErrorsOf, intOrNull } from "../components";
import { CompetitionFrame, WithCompetition } from "./StaffCompetitionDetailPage";

/*
 * The registration form builder over the existing endpoints: the working copy
 * (/api/competition-form-fields/), its preview, and publish / unpublish
 * (/api/competitions/:id/publish-form/). Publishing freezes the working copy
 * as a new version; every registration keeps the version and answers it was
 * submitted with. Validation (keys, types, options, limits, no secrets) is
 * the backend's.
 */

const MAX_FIELDS = 30; // registration_forms.MAX_FIELDS (the backend refuses more)
const CHOICE: FormFieldType[] = ["SINGLE_SELECT", "MULTI_SELECT"];
const TEXT: FormFieldType[] = ["TEXT", "LONG_TEXT"];

export function RegistrationFormBuilderPage() {
  return <WithCompetition title="Registration form">{(c) => <Builder competition={c} />}</WithCompetition>;
}

type Pending = { kind: "publish" | "unpublish" } | { kind: "delete"; field: RegistrationFormFieldRow };

function Builder({ competition: c }: { competition: StaffCompetition }) {
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const id = String(c.id);
  const form = useQuery({ queryKey: competitionKeys.form(id), queryFn: ({ signal }) => endpoints.registrationForm(id, signal) });
  const [editing, setEditing] = useState<RegistrationFormFieldRow | "new" | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  async function refresh(data?: StaffRegistrationForm) {
    if (data) queryClient.setQueryData(competitionKeys.form(id), data);
    await queryClient.invalidateQueries({ queryKey: competitionKeys.all });
  }
  const act = useMutation({
    mutationFn: async (p: Pending) => {
      if (p.kind === "delete") {
        await endpoints.deleteFormField(p.field.id);
        return undefined;
      }
      return p.kind === "publish" ? endpoints.publishForm(c.id) : endpoints.unpublishForm(c.id);
    },
    onSuccess: async (data, p) => {
      setPending(null);
      setNotice(p.kind === "delete" ? `“${p.field.label}” removed from the working copy.`
        : p.kind === "publish" ? `Registration form published as version ${data?.version}.`
          : "Registration form unpublished: parents cannot register until it is published again.");
      await refresh(data);
    },
  });
  const quick = useMutation({
    mutationFn: async (change: { kind: "move"; keys: string[] } | { kind: "active"; field: RegistrationFormFieldRow }) => {
      if (change.kind === "move") return endpoints.reorderForm(c.id, change.keys);
      await endpoints.updateFormField(change.field.id, { is_active: !change.field.is_active });
      return undefined;
    },
    onMutate: () => setRowError(null),
    onSuccess: (data) => refresh(data),
    onError: (e) => setRowError(errorText(e)),
  });

  if (form.isPending) return <CompetitionFrame competition={c} title="Registration form"><LoadingState /></CompetitionFrame>;
  if (form.isError) {
    return <CompetitionFrame competition={c} title="Registration form"><ErrorState error={form.error} onRetry={() => form.refetch()} /></CompetitionFrame>;
  }
  const f = form.data;
  const fields = f.fields;
  function move(index: number, by: -1 | 1) {
    const keys = fields.map((x) => x.key);
    [keys[index], keys[index + by]] = [keys[index + by], keys[index]];
    quick.mutate({ kind: "move", keys });
  }
  return (
    <CompetitionFrame competition={c} title="Registration form">
      <Card title="Publication" actions={<FormStatus form={f} />}>
        <p>Parents answer the <strong>published</strong> version. Changes below are a working copy until you publish
          them; publishing freezes them as a new version. Each registration keeps the version and answers it was
          submitted with.</p>
        {f.has_unpublished_changes ? <Alert tone="warning" title="The working copy has unpublished changes." /> : null}
        <div className="button-row">
          <Button onClick={() => { act.reset(); setPending({ kind: "publish" }); }}>
            {f.has_unpublished_changes || f.status !== "PUBLISHED" ? "Publish form" : "Re-publish (no changes)"}
          </Button>
          {f.status === "PUBLISHED"
            ? <Button variant="secondary" onClick={() => { act.reset(); setPending({ kind: "unpublish" }); }}>Unpublish</Button> : null}
        </div>
      </Card>
      {notice ? <Alert tone="success" role="status" title={notice} /> : null}

      <div className="builder-layout">
        <Section title={`Working copy (${fields.length} of ${MAX_FIELDS} fields)`}
                 actions={<Button variant="secondary" disabled={fields.length >= MAX_FIELDS}
                                  onClick={() => setEditing("new")}>Add field</Button>}>
          <p className="muted">Student, event, fee and payment are fixed system fields; these are the competition’s own questions.</p>
          {rowError ? <Alert tone="danger" role="alert" title={rowError} /> : null}
          {fields.length ? (
            <ol className="builder-fields">
              {fields.map((field, index) => (
                <li key={field.id} className={`builder-field${field.is_active ? "" : " builder-field-inactive"}`}>
                  <div className="builder-field-head">
                    <strong>{field.label}</strong>
                    <span className="badge-row">
                      <Badge tone="info">{FIELD_TYPE[field.field_type]}</Badge>
                      {field.required ? <Badge tone="warning">Required</Badge> : <Badge>Optional</Badge>}
                      {field.is_active ? null : <Badge tone="neutral">Inactive</Badge>}
                    </span>
                  </div>
                  <p className="muted">Key: <code>{field.key}</code>{field.options.length ? ` · Options: ${field.options.join(", ")}` : ""}</p>
                  {field.help_text ? <p className="muted">{field.help_text}</p> : null}
                  <div className="button-row">
                    <Button variant="ghost" disabled={index === 0 || quick.isPending} onClick={() => move(index, -1)}
                            aria-label={`Move ${field.label} up`}>Up</Button>
                    <Button variant="ghost" disabled={index === fields.length - 1 || quick.isPending}
                            onClick={() => move(index, 1)} aria-label={`Move ${field.label} down`}>Down</Button>
                    <Button variant="ghost" onClick={() => setEditing(field)} aria-label={`Edit ${field.label}`}>Edit</Button>
                    <Button variant="ghost" disabled={quick.isPending} onClick={() => quick.mutate({ kind: "active", field })}
                            aria-label={`${field.is_active ? "Deactivate" : "Activate"} ${field.label}`}>
                      {field.is_active ? "Deactivate" : "Activate"}</Button>
                    <Button variant="ghost" onClick={() => { act.reset(); setPending({ kind: "delete", field }); }}
                            aria-label={`Remove ${field.label}`}>Remove</Button>
                  </div>
                </li>
              ))}
            </ol>
          ) : <EmptyState message="No custom fields: parents choose the child and the event only." />}
        </Section>

        <div>
          <Section title="Preview (working copy)">
            <Preview fields={f.preview} />
          </Section>
          <Section title={`Published version ${f.version}`}>
            {f.published_fields.length ? (
              <ol className="plain-list published-fields">
                {f.published_fields.map((d) => (
                  <li key={d.key}><strong>{d.label}</strong> <span className="muted">({FIELD_TYPE[d.type]}{d.required ? ", required" : ""})</span></li>
                ))}
              </ol>
            ) : <p className="muted">No custom fields in the published version.</p>}
          </Section>
        </div>
      </div>

      {editing ? <FieldDialog competitionId={c.id} field={editing === "new" ? null : editing}
                              onClose={() => setEditing(null)} onSaved={(label) => { setNotice(`“${label}” saved to the working copy.`); return refresh(); }} /> : null}
      <ConfirmDialog
        open={!!pending}
        title={pending?.kind === "publish" ? "Publish the registration form?" : pending?.kind === "unpublish"
          ? "Unpublish the registration form?" : "Remove this field?"}
        confirmLabel={pending?.kind === "publish" ? "Publish" : pending?.kind === "unpublish" ? "Unpublish" : "Remove field"}
        tone={pending?.kind === "publish" ? "primary" : "danger"}
        busy={act.isPending}
        error={act.error ? errorText(act.error) : null}
        message={pending?.kind === "publish" ? (
          <p>{f.has_unpublished_changes ? `The working copy becomes version ${f.version + 1}.` : "Nothing changed: the version stays the same."}
            {" "}Parents see it at once. Existing registrations keep their own version.</p>
        ) : pending?.kind === "unpublish" ? (
          <p>New parent registrations stop until the form is published again. Existing registrations are not affected.</p>
        ) : pending?.kind === "delete" ? (
          <p>“{pending.field.label}” leaves the working copy. Published versions and answers already submitted keep it.</p>
        ) : null}
        onCancel={() => setPending(null)}
        onConfirm={() => pending && act.mutate(pending)}
      />
    </CompetitionFrame>
  );
}

function FormStatus({ form }: { form: StaffRegistrationForm }) {
  return form.status === "PUBLISHED" ? (
    <span className="badge-row"><Badge tone="success">Published v{form.version}</Badge>
      {form.published_at ? <span className="muted">{formatDateTime(form.published_at)}</span> : null}</span>
  ) : <Badge tone="neutral">Not published{form.version ? ` (last version ${form.version})` : ""}</Badge>;
}

/** The working copy as parents would see it (answers stay in this page; nothing is submitted). */
function Preview({ fields }: { fields: FormFieldDefinition[] }) {
  const [answers, setAnswers] = useState<Answers>({});
  if (!fields.length) return <p className="muted">No active custom fields.</p>;
  return (
    <div className="builder-preview" aria-label="Form preview">
      <DynamicForm fields={fields} answers={answers} errors={{}} onChange={(key, value) => setAnswers({ ...answers, [key]: value })} />
    </div>
  );
}

function FieldDialog({ competitionId, field, onClose, onSaved }: {
  competitionId: number; field: RegistrationFormFieldRow | null; onClose: () => void; onSaved: (label: string) => Promise<void>;
}) {
  const { endpoints } = useServices();
  const [form, setForm] = useState({
    label: field?.label ?? "", key: field?.key ?? "", field_type: (field?.field_type ?? "TEXT") as FormFieldType,
    required: field?.required ?? false, help_text: field?.help_text ?? "", placeholder: field?.placeholder ?? "",
    options: (field?.options ?? []).join("\n"), max_length: field?.max_length?.toString() ?? "",
    min_value: field?.min_value ?? "", max_value: field?.max_value ?? "", is_active: field?.is_active ?? true,
  });
  const choice = CHOICE.includes(form.field_type);
  const save = useMutation({
    mutationFn: () => {
      const body: RegistrationFormFieldInput = {
        competition: competitionId, key: form.key.trim(), label: form.label.trim(), field_type: form.field_type,
        required: form.required, help_text: form.help_text.trim(), placeholder: form.placeholder.trim(),
        options: choice ? form.options.split("\n").map((o) => o.trim()).filter(Boolean) : [],
        max_length: TEXT.includes(form.field_type) ? intOrNull(form.max_length) : null,
        min_value: form.field_type === "NUMBER" && form.min_value.trim() ? form.min_value.trim() : null,
        max_value: form.field_type === "NUMBER" && form.max_value.trim() ? form.max_value.trim() : null,
        is_active: form.is_active,
      };
      if (field) {
        const { key: _key, competition: _competition, ...changes } = body;
        return endpoints.updateFormField(field.id, changes);
      }
      return endpoints.createFormField(body);
    },
    onSuccess: async (saved) => { onClose(); await onSaved(saved.label); },
  });
  const errors = fieldErrorsOf(save.error);
  const set = (name: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value });
  return (
    <ActionDialog open title={field ? `Edit “${field.label}”` : "Add field"} submitLabel={field ? "Save field" : "Add field"}
                  onClose={onClose} onSubmit={() => save.mutate()} busy={save.isPending} error={save.error ? errorText(save.error) : null}>
      <TextField label="Question (label)" required value={form.label} onChange={set("label")} errors={errors.label} maxLength={200} />
      <TextField label="Key" required value={form.key} onChange={set("key")} errors={errors.key} disabled={!!field} maxLength={40}
                 hint={field ? "Answers are stored under the key, so it cannot change. Deactivate this field and add a new one instead."
                   : "Lowercase letters, digits and _ (for example shirt_size). It cannot change later."} />
      <SelectField label="Type" value={form.field_type} errors={errors.field_type}
                   onChange={(e) => setForm({ ...form, field_type: e.target.value as FormFieldType })}>
        {Object.entries(FIELD_TYPE).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </SelectField>
      {choice ? (
        <TextAreaField label="Options" rows={4} required value={form.options} onChange={set("options")} errors={errors.options}
                       hint="One option per line." />
      ) : null}
      {TEXT.includes(form.field_type) ? (
        <TextField label="Maximum length" inputMode="numeric" value={form.max_length} onChange={set("max_length")}
                   errors={errors.max_length} hint="Empty for the default limit." />
      ) : null}
      {form.field_type === "NUMBER" ? (
        <div className="form-grid">
          <TextField label="Minimum" inputMode="decimal" value={form.min_value} onChange={set("min_value")} errors={errors.min_value} />
          <TextField label="Maximum" inputMode="decimal" value={form.max_value} onChange={set("max_value")} errors={errors.max_value} />
        </div>
      ) : null}
      <TextField label="Help text" value={form.help_text} onChange={set("help_text")} errors={errors.help_text} />
      <TextField label="Placeholder" value={form.placeholder} onChange={set("placeholder")} errors={errors.placeholder} />
      <label className="check-field"><input type="checkbox" checked={form.required}
        onChange={(e) => setForm({ ...form, required: e.target.checked })} /> Parents must answer</label>
      <label className="check-field"><input type="checkbox" checked={form.is_active}
        onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Active (included when published)</label>
      <p className="muted">Never ask for passwords, PINs, card details, tokens or keys: the backend refuses them.</p>
    </ActionDialog>
  );
}
