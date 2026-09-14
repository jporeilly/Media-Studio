import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

/** The adjustable password rules (api/passwords.py PasswordPolicy). */
export interface PasswordPolicy {
  min_length: number;
  require_upper: boolean;
  require_digit: boolean;
  require_symbol: boolean;
  forbid_username: boolean;
  forbid_common: boolean;
}

/** What GET/PUT /api/settings/password-policy return: the rules plus their one-sentence description. */
export interface PolicyPayload {
  policy: PasswordPolicy;
  description: string;
}

export const POLICY_QUERY_KEY = ["password-policy"];

/** The active policy. Any signed-in user may read it: it is the hint beside every password field. */
export function usePasswordPolicy() {
  return useQuery({
    queryKey: POLICY_QUERY_KEY,
    queryFn: () => api.get<PolicyPayload>("/api/settings/password-policy"),
    staleTime: 60_000,
  });
}
