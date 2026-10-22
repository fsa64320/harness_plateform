import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError, del, put, type Schemas } from "../api/client";

type Entry = Schemas["MemoryEntry"];
type Revision = Schemas["MemoryRevision"];

/** Mémoire d'un agent : consultation, correction (avec version), suppression et historique (FR-016). */
export default function AgentMemoryPage() {
  const { groupId = "", agentId = "" } = useParams();
  const queryClient = useQueryClient();
  const key = ["agent-memory", groupId, agentId];
  const [editing, setEditing] = useState<{ entry: Entry; content: string } | null>(null);
  const [history, setHistory] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: entries = [] } = useQuery({
    queryKey: key,
    queryFn: () => api<Entry[]>(`/groups/${groupId}/agents/${agentId}/memory`),
  });
  const { data: revisions = [] } = useQuery({
    queryKey: ["revisions", groupId, history],
    queryFn: () => api<Revision[]>(`/groups/${groupId}/memory/${history}/revisions`),
    enabled: history !== null,
  });
  const fail = (e: unknown) =>
    setError(e instanceof ApiError ? `${e.message}` : "Erreur inattendue");
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });

  const save = useMutation({
    mutationFn: ({ entry, content }: { entry: Entry; content: string }) =>
      put<Entry>(`/groups/${groupId}/memory/${entry.id}`, { content, version: entry.version }),
    onSuccess: () => {
      setEditing(null);
      setError(null);
      return refresh();
    },
    onError: (e) => {
      fail(e);
      return refresh(); // une version plus récente existe : on la recharge
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/groups/${groupId}/memory/${id}`),
    onSuccess: refresh,
    onError: fail,
  });

  return (
    <section>
      <h2>Mémoire de l'agent</h2>
      {error && <p role="alert">{error}</p>}
      {entries.length === 0 && <p>Cette mémoire est vide.</p>}
      <ul>
        {entries.map((entry) => {
          const draft = editing?.entry.id === entry.id ? editing : null;
          return (
            <li key={entry.id}>
              <strong>{entry.path}</strong>{" "}
              <small>
                version {entry.version} —{" "}
                {entry.author_type === "agent" ? "écrit par l'agent" : "modifié par une personne"}
                {entry.source_run_id ? ` (exécution ${entry.source_run_id})` : ""}
              </small>
              {draft ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    save.mutate(draft);
                  }}
                >
                  <textarea
                    aria-label={`Contenu de ${entry.path}`}
                    value={draft.content}
                    onChange={(e) => setEditing({ entry, content: e.target.value })}
                  />
                  <button type="submit">Enregistrer</button>{" "}
                  <button type="button" onClick={() => setEditing(null)}>
                    Annuler
                  </button>
                </form>
              ) : (
                <>
                  <pre>{entry.content}</pre>
                  <button
                    type="button"
                    onClick={() => setEditing({ entry, content: entry.content ?? "" })}
                  >
                    Modifier
                  </button>{" "}
                  <button type="button" onClick={() => remove.mutate(entry.id ?? "")}>
                    Supprimer
                  </button>{" "}
                  <button
                    type="button"
                    onClick={() => setHistory(history === entry.id ? null : (entry.id ?? null))}
                  >
                    Historique
                  </button>
                </>
              )}
              {history === entry.id && (
                <ol aria-label={`Historique de ${entry.path}`}>
                  {revisions.map((r, i) => (
                    <li key={`${r.version}-${i}`}>
                      v{r.version} — {r.change_type} par {r.changed_by} le {r.changed_at}
                      {r.change_type === "conflict" && " (écriture refusée, conservée ici)"}
                    </li>
                  ))}
                </ol>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
