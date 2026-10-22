import { useNavigate } from "react-router-dom";

import type { Schemas } from "../api/client";

type Membership = Schemas["Membership"];

interface Props {
  memberships: Membership[];
  currentGroupId: string | undefined;
}

/** Affiche en permanence le groupe courant et permet d'en changer (FR-022b). */
export default function GroupSwitcher({ memberships, currentGroupId }: Props) {
  const navigate = useNavigate();
  const current = memberships.find((m) => m.group_id === currentGroupId);

  if (memberships.length === 0) {
    return <span role="status">Aucun groupe : demandez une invitation à un administrateur.</span>;
  }
  return (
    <label>
      Groupe courant{" "}
      <select
        aria-label="Groupe courant"
        value={current?.group_id ?? ""}
        onChange={(e) => navigate(`/g/${e.target.value}/agents`)}
      >
        {current === undefined && (
          <option value="" disabled>
            Choisir un groupe
          </option>
        )}
        {memberships.map((m) => (
          <option key={m.id} value={m.group_id}>
            {m.group_name} ({(m.roles ?? []).join(", ")})
          </option>
        ))}
      </select>
    </label>
  );
}
