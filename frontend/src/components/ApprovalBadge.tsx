import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, type Schemas } from "../api/client";

/** Notification dans l'application des demandes d'approbation en attente (FR-005b). */
export default function ApprovalBadge({ groupId }: { groupId: string }) {
  const { data = [] } = useQuery({
    queryKey: ["approvals", groupId, "pending"],
    queryFn: () => api<Schemas["ApprovalRequest"][]>(`/groups/${groupId}/approvals?status=pending`),
    refetchInterval: 10000,
  });
  if (data.length === 0) return <Link to={`/g/${groupId}/approvals`}>Approbations</Link>;
  return (
    <Link to={`/g/${groupId}/approvals`} aria-label={`${data.length} approbation(s) en attente`}>
      Approbations <span className="badge">{data.length}</span>
    </Link>
  );
}
