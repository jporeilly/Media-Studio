import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { api, errorMessage } from "../../api/client";
import { POLICY_QUERY_KEY, usePasswordPolicy, type PasswordPolicy, type PolicyPayload } from "../../lib/passwordPolicy";
import { Button, Card, ErrorBox, Field, Input, Spinner } from "../ui";

/** The policy in one sentence, for a Field hint. Renders nothing until it is known. */
export function PolicyHint() {
  const policy = usePasswordPolicy();
  return <>{policy.data?.description ?? ""}</>;
}

const MIN = 4;
const MAX = 64;

const TOGGLES: { key: keyof Omit<PasswordPolicy, "min_length">; label: string }[] = [
  { key: "require_upper", label: "Require an upper-case letter" },
  { key: "require_digit", label: "Require a digit" },
  { key: "require_symbol", label: "Require a symbol" },
  { key: "forbid_username", label: "Must not contain the username" },
  { key: "forbid_common", label: "Reject common passwords (a built-in list of the usual suspects)" },
];

/** "Password policy" (admins only): the rules applied to new accounts, admin resets and password changes. */
export function PasswordPolicyCard() {
  const qc = useQueryClient();
  const policy = usePasswordPolicy();
  const [draft, setDraft] = useState<PasswordPolicy | null>(null);
  const [saved, setSaved] = useState(false);
  const form = draft ?? policy.data?.policy ?? null;
  const dirty = !!draft && JSON.stringify(draft) !== JSON.stringify(policy.data?.policy);
  const lengthOk = !!form && Number.isInteger(form.min_length) && form.min_length >= MIN && form.min_length <= MAX;

  const save = useMutation({
    mutationFn: (p: PasswordPolicy) => api.put<PolicyPayload>("/api/settings/password-policy", p),
    onSuccess: (r) => { qc.setQueryData(POLICY_QUERY_KEY, r); setDraft(null); setSaved(true); },
    onError: () => setSaved(false),
  });
  const update = (patch: Partial<PasswordPolicy>) => {
    if (!form) return;
    setSaved(false);
    setDraft({ ...form, ...patch });
  };

  return (
    <Card
      title="Password policy"
      subtitle="Applies to new accounts, admin resets and password changes. Blank or space-padded passwords, and passwords over 72 characters, are always rejected."
    >
      {policy.isLoading && <Spinner label="Loading the policy…" />}
      {policy.isError && <ErrorBox message={errorMessage(policy.error)} />}
      {form && (
        <div style={{ display: "grid", gap: 14 }}>
          <div className="os-form-grid">
            <Field label="Minimum length" hint={`${MIN} to ${MAX} characters. Length protects more than complexity does.`}>
              <Input
                type="number"
                min={MIN}
                max={MAX}
                value={form.min_length}
                onChange={(e) => update({ min_length: Number(e.target.value) })}
                aria-invalid={!lengthOk}
              />
            </Field>
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {TOGGLES.map((t) => (
              <label key={t.key} className="os-checkbox">
                <input
                  type="checkbox"
                  checked={form[t.key]}
                  onChange={(e) => update({ [t.key]: e.target.checked } as Partial<PasswordPolicy>)}
                />
                {t.label}
              </label>
            ))}
          </div>
          {policy.data && <div className="os-muted os-small">Users currently see: {policy.data.description}</div>}
          {!lengthOk && <ErrorBox message={`The minimum length must be between ${MIN} and ${MAX}.`} />}
          {save.isError && <ErrorBox message={errorMessage(save.error)} />}
          {saved && <div style={{ color: "var(--good)", fontWeight: 500 }}>Policy saved — it applies from the next password set.</div>}
          <div>
            <Button
              variant="primary"
              icon={<ShieldCheck size={16} />}
              disabled={!dirty || !lengthOk || save.isPending}
              onClick={() => form && save.mutate(form)}
            >
              {save.isPending ? "Saving…" : "Save policy"}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
