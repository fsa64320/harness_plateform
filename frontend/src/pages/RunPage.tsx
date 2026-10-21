import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { api, post, type Schemas } from "../api/client";
import LiveTrace from "../components/LiveTrace";

const ACTIVE = new Set(["queued", "running", "awaiting_approval"]);

export default function RunPage() {
  const { groupId = "", runId = "" } = useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const key = ["run", groupId, runId];

  const { data: run } = useQuery({
    queryKey: key,
    queryFn: () => api<Schemas["Run"]>(`/groups/${groupId}/runs/${runId}`),
    refetchInterval: (query) => (ACTIVE.has(query.state.data?.status ?? "") ? 2000 : false),
  });

  const stop = useMutation({
    mutationFn: () => post<void>(`/groups/${groupId}/runs/${runId}/stop`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });
  const relaunch = useMutation({
    mutationFn: () =>
      post<Schemas["Run"]>(`/groups/${groupId}/runs`, {
        agent_id: run?.agent_id,
        task: run?.task,
        relaunch_of: runId,
      }),
    onSuccess: (created) => navigate(`/g/${groupId}/runs/${created.id}`),
  });

  if (!run) return <p role="status">Chargement…</p>;
  return (
    <section>
      <h2>Exécution</h2>
      <p role="status">Statut : {run.status}</p>
      <p>Tâche : {run.task}</p>
      {run.stop_reason && <p>Motif d'arrêt : {run.stop_reason}</p>}
      <p>
        {run.iterations ?? 0} itération(s) — {(run.input_tokens ?? 0) + (run.output_tokens ?? 0)}{" "}
        tokens
      </p>
      {ACTIVE.has(run.status ?? "") && (
        <button type="button" onClick={() => stop.mutate()} disabled={stop.isPending}>
          Arrêter
        </button>
      )}
      {run.status === "interrupted" && (
        <button type="button" onClick={() => relaunch.mutate()} disabled={relaunch.isPending}>
          Relancer
        </button>
      )}
      {run.result && <pre aria-label="Résultat">{run.result}</pre>}
      <LiveTrace groupId={groupId} runId={runId} />
    </section>
  );
}
