import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError, del, post, type Schemas } from "../api/client";

type Entry = Schemas["MemoryEntry"];
type Proposal = Schemas["GroupMemoryProposal"];

/** Mémoire de groupe : entrées, ajout direct, et propositions des agents à valider (FR-017a). */
export default function GroupMemoryPage() {
  const { groupId = "" } = useParams();
  const queryClient = useQueryClient();
  const [path, setPath] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);

  const entries = useQuery({
    queryKey: ["group-memory", groupId],
    queryFn: () => api<Entry[]>(`/groups/${groupId}/group-memory`),
  });
  const proposals = useQuery({
    queryKey: ["proposals", groupId],
    queryFn: () => api<Proposal[]>(`/groups/${groupId}/group-memory/proposals?status=pending`),
    refetchInterval: 10000,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["group-memory", groupId] });
    return queryClient.invalidateQueries({ queryKey: ["proposals", groupId] });
  };
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Erreur inattendue");

  const add = useMutation({
    mutationFn: () => post<Entry>(`/groups/${groupId}/group-memory`, { path, content }),
    onSuccess: () => {
      setPath("");
      setContent("");
      setError(null);
      return refresh();
    },
    onError: fail,
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "accept" | "reject" }) =>
      post<Proposal>(`/groups/${groupId}/group-memory/proposals/${id}/decision`, { decision }),
    onSuccess: refresh,
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/groups/${groupId}/memory/${id}`),
    onSuccess: refresh,
    onError: fail,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    add.mutate();
  };

  return (
    <section>
      <h2>Mémoire de groupe</h2>
      {error && <p role="alert">{error}</p>}

      <h3>Propositions des agents</h3>
      {(proposals.data ?? []).length === 0 && <p>Aucune proposition en attente.</p>}
      <ul>
        {(proposals.data ?? []).map((p) => (
          <li key={p.id}>
            <strong>{p.proposed_path}</strong>
            <pre>{p.proposed_content}</pre>
            <button
              type="button"
              onClick={() => decide.mutate({ id: p.id ?? "", decision: "accept" })}
            >
              Accepter
            </button>{" "}
            <button
              type="button"
              onClick={() => decide.mutate({ id: p.id ?? "", decision: "reject" })}
            >
              Refuser
            </button>
          </li>
        ))}
      </ul>

      <h3>Entrées</h3>
      {(entries.data ?? []).length === 0 && <p>La mémoire de groupe est vide.</p>}
      <ul>
        {(entries.data ?? []).map((e) => (
          <li key={e.id}>
            <strong>{e.path}</strong> <small>version {e.version}</small>
            <pre>{e.content}</pre>
            <button type="button" onClick={() => remove.mutate(e.id ?? "")}>
              Supprimer
            </button>
          </li>
        ))}
      </ul>

      <form onSubmit={submit} aria-label="Ajouter une entrée">
        <label>
          Chemin <input value={path} onChange={(e) => setPath(e.target.value)} required />
        </label>
        <label>
          Contenu <textarea value={content} onChange={(e) => setContent(e.target.value)} required />
        </label>
        <button type="submit">Ajouter l'entrée</button>
      </form>
    </section>
  );
}
