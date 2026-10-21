import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { api, ApiError, type Schemas } from "../api/client";

/** Journal d'administration du groupe : qui a fait quoi, et quand (FR-024). */
export default function AuditPage() {
  const { groupId = "" } = useParams();
  const { data = [], error } = useQuery({
    queryKey: ["audit", groupId],
    queryFn: () => api<Schemas["AdminAuditEntry"][]>(`/groups/${groupId}/audit`),
  });
  return (
    <section>
      <h2>Journal d'administration</h2>
      {error instanceof ApiError && <p role="alert">{error.message}</p>}
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Action</th>
            <th>Cible</th>
            <th>Auteur</th>
            <th>Détails</th>
          </tr>
        </thead>
        <tbody>
          {[...data].reverse().map((e, i) => (
            <tr key={`${e.occurred_at}-${i}`}>
              <td>{e.occurred_at}</td>
              <td>{e.action}</td>
              <td>
                {e.target_type} {e.target_id}
              </td>
              <td>{e.actor_id ?? "ligne de commande"}</td>
              <td>
                <code>{JSON.stringify(e.details)}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
