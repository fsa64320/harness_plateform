import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api, ApiError, post, type Schemas } from "../api/client";

type Approval = Schemas["ApprovalRequest"];

export default function ApprovalsPage() {
  const { groupId = "" } = useParams();
  const queryClient = useQueryClient();
  const { data = [], error } = useQuery({
    queryKey: ["approvals", groupId, "pending"],
    queryFn: () => api<Approval[]>(`/groups/${groupId}/approvals?status=pending`),
    refetchInterval: 5000,
  });
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      post<Approval>(`/groups/${groupId}/approvals/${id}/decision`, { decision }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["approvals", groupId] }),
  });

  return (
    <section>
      <h2>Demandes d'approbation</h2>
      {error instanceof ApiError && <p role="alert">{error.message}</p>}
      {decide.error instanceof ApiError && <p role="alert">{decide.error.message}</p>}
      {data.length === 0 && <p>Aucune demande en attente.</p>}
      <ul>
        {data.map((a) => (
          <li key={a.id}>
            {a.no_eligible_approver && (
              <p role="alert">
                Aucun approbateur n'est désigné dans ce groupe : cette demande expirera sans
                décision.
              </p>
            )}
            <strong>{a.tool_operation}</strong> <code>{JSON.stringify(a.arguments)}</code>{" "}
            <small>expire le {a.expires_at}</small>{" "}
            <Link to={`/g/${groupId}/runs/${a.run_id}`}>Voir l'exécution</Link>
            {!a.no_eligible_approver && (
              <>
                {" "}
                <button
                  type="button"
                  onClick={() => decide.mutate({ id: a.id ?? "", decision: "approve" })}
                >
                  Approuver
                </button>{" "}
                <button
                  type="button"
                  onClick={() => decide.mutate({ id: a.id ?? "", decision: "reject" })}
                >
                  Refuser
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
