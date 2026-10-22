import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { Schemas } from "../src/api/client";
import GroupSwitcher from "../src/components/GroupSwitcher";

const memberships: Schemas["Membership"][] = [
  { id: "m1", group_id: "g1", group_name: "demo", roles: ["admin", "member"] },
  { id: "m2", group_id: "g2", group_name: "autre", roles: ["member"] },
];

test("affiche le groupe courant avec ses rôles", () => {
  render(
    <MemoryRouter>
      <GroupSwitcher memberships={memberships} currentGroupId="g2" />
    </MemoryRouter>,
  );
  expect(screen.getByLabelText("Groupe courant")).toHaveValue("g2");
  expect(screen.getByRole("option", { name: "demo (admin, member)" })).toBeInTheDocument();
});

test("signale l'absence de groupe", () => {
  render(
    <MemoryRouter>
      <GroupSwitcher memberships={[]} currentGroupId={undefined} />
    </MemoryRouter>,
  );
  expect(screen.getByRole("status")).toHaveTextContent("Aucun groupe");
});
