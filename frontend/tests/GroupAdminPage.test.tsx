import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import GroupAdminPage from "../src/pages/GroupAdminPage";

const members = [
  {
    id: "m1",
    group_id: "g1",
    person_email: "bob@example.test",
    roles: ["member"],
    max_concurrent_runs: 5,
    max_tokens_per_day: 1000000,
    status: "active",
  },
];
const agents = [{ id: "a1", name: "veille" }];
let accounts: unknown[] = [];

function setup() {
  accounts = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/service-accounts") && method === "POST") {
      const created = {
        id: "s1",
        name: "ci",
        token_prefix: "hsa_0123abcd",
        token: "hsa_SECRET-TOKEN-VALUE",
        allowed_agent_ids: ["a1"],
        status: "active",
      };
      accounts = [{ ...created, token: undefined }];
      return new Response(JSON.stringify(created), { status: 201 });
    }
    if (url.endsWith("/service-accounts"))
      return new Response(JSON.stringify(accounts), { status: 200 });
    if (url.endsWith("/members")) return new Response(JSON.stringify(members), { status: 200 });
    if (url.endsWith("/agents")) return new Response(JSON.stringify(agents), { status: 200 });
    if (url.includes("/members/") && method === "PATCH")
      return new Response(JSON.stringify({ ...members[0], ...JSON.parse(String(init?.body)) }), {
        status: 200,
      });
    return new Response("{}", { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={["/g/g1/admin"]}>
        <Routes>
          <Route path="/g/:groupId/admin" element={<GroupAdminPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

test("le jeton d'un compte de service s'affiche une seule fois puis disparaît", async () => {
  setup();
  await screen.findByText("bob@example.test");
  await userEvent.type(screen.getByLabelText("Nom du compte de service"), "ci");
  await userEvent.click(screen.getByLabelText("veille"));
  await userEvent.click(screen.getByRole("button", { name: "Créer le compte de service" }));

  const banner = await screen.findByRole("alert");
  expect(banner).toHaveTextContent("hsa_SECRET-TOKEN-VALUE");
  expect(banner).toHaveTextContent("ne sera plus affiché");

  await userEvent.click(screen.getByRole("button", { name: "J'ai copié le jeton" }));
  expect(screen.queryByText(/hsa_SECRET-TOKEN-VALUE/)).not.toBeInTheDocument();
  // la liste ne montre que le préfixe
  expect(await screen.findByText(/hsa_0123abcd/)).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("SECRET-TOKEN-VALUE");
});

test("les rôles d'un membre se modifient dans une liste à choix multiple", async () => {
  const fetchMock = setup();
  const row = (await screen.findByText("bob@example.test")).closest("li") as HTMLElement;
  const select = within(row).getByLabelText("Rôles de bob@example.test") as HTMLSelectElement;
  expect(select.multiple).toBe(true);
  expect(Array.from(select.selectedOptions).map((o) => o.value)).toEqual(["member"]);

  await userEvent.selectOptions(select, ["member", "approver"]);
  await userEvent.click(within(row).getByRole("button", { name: "Enregistrer les rôles" }));

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith("/members/m1") && init?.method === "PATCH",
    );
    expect(JSON.parse(String(call?.[1]?.body)).roles).toEqual(["member", "approver"]);
  });
});
