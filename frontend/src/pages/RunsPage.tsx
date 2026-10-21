import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, type Schemas } from "../api/client";

const STATUSES = [
  "queued",
  "running",
  "awaiting_approval",
  "succeeded",
  "failed",
  "stopped",
  "limit_reached",
  "interrupted",
  "expired",
  "rejected_quota",
];

/** Historique des exécutions, filtrable par agent, déclencheur, statut et période (FR-008). */
export default function RunsPage() {
  const { groupId = "" } = useParams();
  const [filters, setFilters] = useState({
    agent_id: "",
    triggered_by: "",
    status: "",
    from: "",
    to: "",
  });

  const { data: agents = [] } = useQuery({
    queryKey: ["agents", groupId],
    queryFn: () => api<Schemas["Agent"][]>(`/groups/${groupId}/agents`),
  });
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api<Schemas["Me"]>("/me") });
  const { data: members = [] } = useQuery({
    queryKey: ["members", groupId],
    queryFn: () => api<Schemas["Membership"][]>(`/groups/${groupId}/members`),
    retry: false,
  });

  const query = new URLSearchParams(
    Object.entries(filters)
      .filter(([, v]) => v !== "")
      .map(([k, v]) => [k, k === "from" || k === "to" ? new Date(v).toISOString() : v]),
  ).toString();
  const { data } = useQuery({
    queryKey: ["runs", groupId, query],
    queryFn: () =>
      api<{ items: Schemas["Run"][] }>(`/groups/${groupId}/runs${query ? `?${query}` : ""}`),
    refetchInterval: 5000,
  });
  const set = (key: keyof typeof filters) => (e: { target: { value: string } }) =>
    setFilters({ ...filters, [key]: e.target.value });
  const agentName = (id?: string | null) =>
    agents.find((a) => a.id === id)?.name ?? "(agent supprimé)";

  return (
    <section>
      <h2>Exécutions</h2>
      <form aria-label="Filtres" onSubmit={(e) => e.preventDefault()}>
        <label>
          Agent{" "}
          <select value={filters.agent_id} onChange={set("agent_id")}>
            <option value="">Tous</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Déclencheur{" "}
          <select value={filters.triggered_by} onChange={set("triggered_by")}>
            <option value="">Tous</option>
            {me?.id && <option value={me.id}>Moi</option>}
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.person_email}
              </option>
            ))}
          </select>
        </label>
        <label>
          Statut{" "}
          <select value={filters.status} onChange={set("status")}>
            <option value="">Tous</option>
            {STATUSES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          Du <input type="datetime-local" value={filters.from} onChange={set("from")} />
        </label>
        <label>
          Au <input type="datetime-local" value={filters.to} onChange={set("to")} />
        </label>
      </form>
      <table>
        <thead>
          <tr>
            <th>Agent</th>
            <th>Tâche</th>
            <th>Statut</th>
            <th>Mode</th>
            <th>Début</th>
          </tr>
        </thead>
        <tbody>
          {(data?.items ?? []).map((run) => (
            <tr key={run.id}>
              <td>{agentName(run.agent_id)}</td>
              <td>
                <Link to={`/g/${groupId}/runs/${run.id}`}>{run.task}</Link>
              </td>
              <td>{run.status}</td>
              <td>{run.launch_mode}</td>
              <td>{run.started_at ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
