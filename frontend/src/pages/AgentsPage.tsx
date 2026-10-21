import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError, del, post, put, type Schemas } from "../api/client";
import AgentForm from "../components/AgentForm";

type Agent = Schemas["Agent"];

export default function AgentsPage() {
  const { groupId = "" } = useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [task, setTask] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const { data: agents = [], isLoading } = useQuery({
    queryKey: ["agents", groupId],
    queryFn: () => api<Agent[]>(`/groups/${groupId}/agents`),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["agents", groupId] });
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Erreur inattendue");

  const create = useMutation({
    mutationFn: (input: Schemas["AgentInput"]) => post<Agent>(`/groups/${groupId}/agents`, input),
    onSuccess: () => {
      setCreating(false);
      setError(null);
      return refresh();
    },
    onError: fail,
  });
  const update = useMutation({
    mutationFn: (input: Schemas["AgentInput"]) =>
      put<Agent>(`/groups/${groupId}/agents/${editing?.id}`, input),
    onSuccess: () => {
      setEditing(null);
      setError(null);
      return refresh();
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/groups/${groupId}/agents/${id}`),
    onSuccess: refresh,
    onError: fail,
  });
  const duplicate = useMutation({
    mutationFn: (id: string) => post<Agent>(`/groups/${groupId}/agents/${id}/duplicate`),
    onSuccess: refresh,
    onError: fail,
  });
  const launch = useMutation({
    mutationFn: (agent: Agent) =>
      post<Schemas["Run"]>(`/groups/${groupId}/runs`, {
        agent_id: agent.id,
        task: task[agent.id ?? ""] ?? "",
      }),
    onSuccess: (run) => navigate(`/g/${groupId}/runs/${run.id}`),
    onError: fail,
  });

  if (isLoading) return <p role="status">Chargement…</p>;

  return (
    <section>
      <h2>Agents</h2>
      {error && <p role="alert">{error}</p>}
      {agents.length === 0 && !creating && (
        <div className="empty">
          <h3>Créez votre premier agent</h3>
          <ol>
            <li>Donnez-lui un nom et des instructions.</li>
            <li>
              Choisissez les outils qu'il peut utiliser (aucun suffit pour commencer) et ses
              limites.
            </li>
            <li>Lancez-le sur une tâche et suivez-le étape par étape.</li>
          </ol>
        </div>
      )}
      {!creating && !editing && (
        <button type="button" onClick={() => setCreating(true)}>
          Nouvel agent
        </button>
      )}
      {creating && <AgentForm groupId={groupId} onSubmit={(i) => create.mutate(i)} error={error} />}
      {editing && (
        <AgentForm
          groupId={groupId}
          initial={editing}
          submitLabel="Enregistrer"
          onSubmit={(i) => update.mutate(i)}
          error={error}
        />
      )}
      <ul>
        {agents.map((agent) => (
          <li key={agent.id}>
            <strong>{agent.name}</strong> — {agent.model_id}
            {agent.config_changed_at && <em> (configuration modifiée : un outil a été retiré)</em>}
            <div>
              <input
                aria-label={`Tâche pour ${agent.name}`}
                placeholder="Décrivez la tâche"
                value={task[agent.id ?? ""] ?? ""}
                onChange={(e) => setTask({ ...task, [agent.id ?? ""]: e.target.value })}
              />
              <button
                type="button"
                onClick={() => launch.mutate(agent)}
                disabled={!task[agent.id ?? ""]}
              >
                Lancer
              </button>{" "}
              <button type="button" onClick={() => duplicate.mutate(agent.id ?? "")}>
                Dupliquer
              </button>{" "}
              <button type="button" onClick={() => setEditing(agent)}>
                Modifier
              </button>{" "}
              <Link to={`/g/${groupId}/agents/${agent.id}/memory`}>Mémoire</Link>{" "}
              <button type="button" onClick={() => remove.mutate(agent.id ?? "")}>
                Supprimer
              </button>
            </div>
          </li>
        ))}
      </ul>
      <p>
        <Link to={`/g/${groupId}/runs`}>Voir les exécutions</Link>
      </p>
    </section>
  );
}
