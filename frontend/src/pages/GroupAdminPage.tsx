import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError, del, patch, post, type Schemas } from "../api/client";

type Membership = Schemas["Membership"];
type ServiceAccount = Schemas["ServiceAccount"];
type Role = NonNullable<Membership["roles"]>[number];

// Du moins au plus privilégié ; `member` est toujours accordé à un membre actif.
const ROLES: Role[] = ["member", "approver", "tool_manager", "group_memory_manager", "admin"];
const ROLE_LABELS: Record<Role, string> = {
  member: "membre",
  approver: "approbateur",
  tool_manager: "gestionnaire d'outils",
  group_memory_manager: "gestionnaire de la mémoire de groupe",
  admin: "administrateur",
};

function MemberRow({
  member,
  groupId,
  onError,
}: {
  member: Membership;
  groupId: string;
  onError: (e: unknown) => void;
}) {
  const queryClient = useQueryClient();
  const [roles, setRoles] = useState<Role[]>((member.roles ?? []) as Role[]);
  const [concurrent, setConcurrent] = useState(member.max_concurrent_runs ?? 5);
  const [tokens, setTokens] = useState(member.max_tokens_per_day ?? 1000000);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["members", groupId] });
  const save = useMutation({
    mutationFn: () =>
      patch<Membership>(`/groups/${groupId}/members/${member.id}`, {
        roles,
        max_concurrent_runs: concurrent,
        max_tokens_per_day: tokens,
      }),
    onSuccess: refresh,
    onError,
  });
  const remove = useMutation({
    mutationFn: () => del(`/groups/${groupId}/members/${member.id}`),
    onSuccess: refresh,
    onError,
  });
  const email = member.person_email ?? "";
  return (
    <li>
      <strong>{email}</strong>
      <label>
        Rôles{" "}
        <select
          multiple
          aria-label={`Rôles de ${email}`}
          value={roles}
          onChange={(e) => setRoles(Array.from(e.target.selectedOptions, (o) => o.value as Role))}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </select>
      </label>
      <label>
        Exécutions simultanées max{" "}
        <input
          type="number"
          min={1}
          value={concurrent}
          onChange={(e) => setConcurrent(Number(e.target.value))}
        />
      </label>
      <label>
        Tokens par jour max{" "}
        <input
          type="number"
          min={1}
          value={tokens}
          onChange={(e) => setTokens(Number(e.target.value))}
        />
      </label>
      <button type="button" onClick={() => save.mutate()}>
        Enregistrer les rôles
      </button>{" "}
      <button type="button" onClick={() => remove.mutate()}>
        Retirer du groupe
      </button>
    </li>
  );
}

export default function GroupAdminPage() {
  const { groupId = "" } = useParams();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRoles, setInviteRoles] = useState<Role[]>(["member"]);
  const [accountName, setAccountName] = useState("");
  const [allowed, setAllowed] = useState<string[]>([]);
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Erreur inattendue");

  const members = useQuery({
    queryKey: ["members", groupId],
    queryFn: () => api<Membership[]>(`/groups/${groupId}/members`),
  });
  const agents = useQuery({
    queryKey: ["agents", groupId],
    queryFn: () => api<Schemas["Agent"][]>(`/groups/${groupId}/agents`),
  });
  const accounts = useQuery({
    queryKey: ["service-accounts", groupId],
    queryFn: () => api<ServiceAccount[]>(`/groups/${groupId}/service-accounts`),
  });

  const invite = useMutation({
    mutationFn: () =>
      post<Membership>(`/groups/${groupId}/members`, { email: inviteEmail, roles: inviteRoles }),
    onSuccess: () => {
      setInviteEmail("");
      setError(null);
      return queryClient.invalidateQueries({ queryKey: ["members", groupId] });
    },
    onError: fail,
  });
  const createAccount = useMutation({
    mutationFn: () =>
      post<ServiceAccount & { token: string }>(`/groups/${groupId}/service-accounts`, {
        name: accountName,
        allowed_agent_ids: allowed,
      }),
    onSuccess: (created) => {
      setNewToken(created.token); // montré une seule fois : jamais relu ensuite
      setAccountName("");
      setAllowed([]);
      setError(null);
      return queryClient.invalidateQueries({ queryKey: ["service-accounts", groupId] });
    },
    onError: fail,
  });
  const revoke = useMutation({
    mutationFn: (id: string) => post<void>(`/groups/${groupId}/service-accounts/${id}/revoke`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["service-accounts", groupId] }),
    onError: fail,
  });

  const toggle = (id: string) =>
    setAllowed((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  const submitAccount = (e: FormEvent) => {
    e.preventDefault();
    createAccount.mutate();
  };

  return (
    <section>
      <h2>Administration du groupe</h2>
      {error && <p role="alert">{error}</p>}

      <h3>Membres</h3>
      <ul>
        {(members.data ?? [])
          .filter((m) => m.status === "active")
          .map((m) => (
            <MemberRow key={m.id} member={m} groupId={groupId} onError={fail} />
          ))}
      </ul>
      <form
        aria-label="Inviter un membre"
        onSubmit={(e) => {
          e.preventDefault();
          invite.mutate();
        }}
      >
        <label>
          E-mail{" "}
          <input
            type="email"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            required
          />
        </label>
        <label>
          Rôles{" "}
          <select
            multiple
            aria-label="Rôles de la personne invitée"
            value={inviteRoles}
            onChange={(e) =>
              setInviteRoles(Array.from(e.target.selectedOptions, (o) => o.value as Role))
            }
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Inviter</button>
      </form>

      <h3>Comptes de service</h3>
      {newToken && (
        <div role="alert">
          <p>
            Jeton du compte de service : <code>{newToken}</code>
          </p>
          <p>Copiez-le maintenant : il ne sera plus affiché.</p>
          <button type="button" onClick={() => setNewToken(null)}>
            J'ai copié le jeton
          </button>
        </div>
      )}
      <ul>
        {(accounts.data ?? []).map((a) => (
          <li key={a.id}>
            {a.name} — <code>{a.token_prefix}…</code> ({a.status}){" "}
            {a.status === "active" && (
              <button type="button" onClick={() => revoke.mutate(a.id ?? "")}>
                Révoquer
              </button>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={submitAccount} aria-label="Créer un compte de service">
        <label>
          Nom du compte de service{" "}
          <input value={accountName} onChange={(e) => setAccountName(e.target.value)} required />
        </label>
        <fieldset>
          <legend>Agents autorisés</legend>
          {(agents.data ?? []).map((agent) => (
            <label key={agent.id}>
              <input
                type="checkbox"
                checked={allowed.includes(agent.id ?? "")}
                onChange={() => toggle(agent.id ?? "")}
              />{" "}
              {agent.name}
            </label>
          ))}
        </fieldset>
        <button type="submit">Créer le compte de service</button>
      </form>
    </section>
  );
}
