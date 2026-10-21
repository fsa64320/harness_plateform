import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, ApiError, post, type Schemas } from "../api/client";

type Group = Schemas["Group"];

/** Administration de la plateforme : créer et désactiver des groupes, désigner leurs premiers
 *  administrateurs. Elle ne donne accès à aucun contenu de groupe (FR-022a). */
export default function PlatformAdminPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [emails, setEmails] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { data: groups = [] } = useQuery({
    queryKey: ["admin-groups"],
    queryFn: () => api<Group[]>("/admin/groups"),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["admin-groups"] });
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Erreur inattendue");

  const create = useMutation({
    mutationFn: () =>
      post<Group>("/admin/groups", {
        name,
        admin_emails: emails
          .split(/[\s,;]+/)
          .map((e) => e.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      setName("");
      setEmails("");
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      return refresh();
    },
    onError: fail,
  });
  const disable = useMutation({
    mutationFn: (id: string) => post<void>(`/admin/groups/${id}/disable`),
    onSuccess: refresh,
    onError: fail,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <section>
      <h2>Groupes de la plateforme</h2>
      {error && <p role="alert">{error}</p>}
      <ul>
        {groups.map((g) => (
          <li key={g.id}>
            {g.name} ({g.status}){" "}
            {g.status === "active" && (
              <button
                type="button"
                onClick={() => {
                  if (window.confirm(`Désactiver « ${g.name} » ? Ses exécutions seront arrêtées.`))
                    disable.mutate(g.id ?? "");
                }}
              >
                Désactiver
              </button>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={submit} aria-label="Créer un groupe">
        <label>
          Nom du groupe <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Premiers administrateurs (e-mails séparés par une virgule){" "}
          <input value={emails} onChange={(e) => setEmails(e.target.value)} required />
        </label>
        <button type="submit">Créer le groupe</button>
      </form>
    </section>
  );
}
