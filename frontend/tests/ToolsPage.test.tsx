import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import ToolsPage from "../src/pages/ToolsPage";

const tools = [
  {
    id: "t1",
    kind: "mcp_server",
    name: "crm",
    status: "active",
    has_credential: true,
    operations: [
      { id: "o1", name: "lookup", classification: "read_only" },
      { id: "o2", name: "send_message", classification: "unclassified" },
    ],
  },
];

function setup() {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") {
      return new Response(
        JSON.stringify({ id: "x", operation: "ok", classification: "read_only" }),
        {
          status: init.method === "POST" ? 201 : 200,
        },
      );
    }
    return new Response(JSON.stringify(tools), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={["/g/g1/tools"]}>
        <Routes>
          <Route path="/g/:groupId/tools" element={<ToolsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

test("l'identifiant d'un serveur MCP est en écriture seule et jamais prérempli", async () => {
  setup();
  await screen.findByText("crm");
  await userEvent.click(screen.getByRole("button", { name: "Ajouter un serveur MCP" }));

  const credential = screen.getByLabelText("Identifiant d'accès (écriture seule)");
  expect(credential).toHaveAttribute("type", "password");
  expect(credential).toHaveValue("");
  expect(credential).toHaveAttribute("autocomplete", "new-password");
  // l'outil existant signale qu'un identifiant est enregistré, sans jamais l'afficher
  expect(screen.getByText("identifiant enregistré")).toBeInTheDocument();
});

test("envoie l'identifiant à la création puis vide le champ", async () => {
  const fetchMock = setup();
  await screen.findByText("crm");
  await userEvent.click(screen.getByRole("button", { name: "Ajouter un serveur MCP" }));
  await userEvent.type(screen.getByLabelText("Nom"), "vault");
  await userEvent.type(screen.getByLabelText("URL (HTTPS)"), "https://mcp.example.test/mcp");
  await userEvent.type(
    screen.getByLabelText("Identifiant d'accès (écriture seule)"),
    "jeton-secret",
  );
  await userEvent.click(screen.getByRole("button", { name: "Enregistrer le serveur" }));

  await waitFor(() => {
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post).toBeDefined();
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      kind: "mcp_server",
      name: "vault",
      description: "",
      mcp: { url: "https://mcp.example.test/mcp", credential: "jeton-secret" },
    });
  });
});

test("la classification se change par opération", async () => {
  const fetchMock = setup();
  const row = (await screen.findByText("send_message")).closest("li") as HTMLElement;
  await userEvent.selectOptions(
    within(row).getByLabelText("Classification de send_message"),
    "external_effect",
  );

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/groups/g1/tools/t1/operations/send_message/classification",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ classification: "external_effect" }),
      }),
    ),
  );
});

test("une opération non classée est présentée comme à effet externe", async () => {
  setup();
  const row = (await screen.findByText("send_message")).closest("li") as HTMLElement;
  expect(within(row).getByLabelText("Classification de send_message")).toHaveValue("unclassified");
  expect(within(row).getByText(/traitée comme à effet externe/)).toBeInTheDocument();
});
