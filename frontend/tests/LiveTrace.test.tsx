import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import LiveTrace from "../src/components/LiveTrace";
import RunPage from "../src/pages/RunPage";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  listeners = new Map<string, ((e: MessageEvent) => void)[]>();
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  removeEventListener() {}
  close() {
    this.closed = true;
  }
  emit(seq: number, type: string, payload: Record<string, unknown> = {}) {
    const data = JSON.stringify({
      run_id: "r1",
      seq,
      type,
      occurred_at: "2026-10-21T12:00:00Z",
      payload,
    });
    for (const fn of this.listeners.get(type) ?? []) {
      fn(new MessageEvent(type, { data, lastEventId: String(seq) }));
    }
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

const source = () => FakeEventSource.instances[0]!;

test("ouvre le flux de l'exécution et affiche les événements dans l'ordre de seq", () => {
  render(<LiveTrace groupId="g1" runId="r1" />);
  expect(source().url).toBe("/api/v1/groups/g1/runs/r1/events");

  act(() => {
    source().emit(2, "tool_call_started", { tool: "crm_lookup" });
    source().emit(1, "run_started", { task: "cherche" });
    source().emit(3, "tool_call_finished", { status: "ok" });
  });

  const items = within(screen.getByRole("list", { name: "Trace de l'exécution" })).getAllByRole(
    "listitem",
  );
  expect(items.map((i) => i.getAttribute("data-seq"))).toEqual(["1", "2", "3"]);
  expect(items[1]).toHaveTextContent("crm_lookup");
});

test("ignore un événement déjà reçu (reconnexion)", () => {
  render(<LiveTrace groupId="g1" runId="r1" />);
  act(() => {
    source().emit(1, "run_started");
    source().emit(1, "run_started");
  });
  expect(screen.getAllByRole("listitem")).toHaveLength(1);
});

test("ferme le flux et signale le statut final à la fin de l'exécution", () => {
  const onStatus = vi.fn();
  render(<LiveTrace groupId="g1" runId="r1" onStatus={onStatus} />);
  act(() => {
    source().emit(1, "run_started");
    source().emit(2, "run_ended", { status: "limit_reached", stop_reason: "iterations" });
  });
  expect(source().closed).toBe(true);
  expect(onStatus).toHaveBeenLastCalledWith("limit_reached");
});

function renderRunPage(run: Record<string, unknown>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response(null, { status: 202 });
    return new Response(JSON.stringify(run), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter initialEntries={["/g/g1/runs/r1"]}>
        <Routes>
          <Route path="/g/:groupId/runs/:runId" element={<RunPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

test("le bouton Arrêter appelle POST /runs/{runId}/stop", async () => {
  const fetchMock = renderRunPage({ id: "r1", agent_id: "a1", task: "t", status: "running" });
  const stop = await screen.findByRole("button", { name: "Arrêter" });
  await userEvent.click(stop);
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/groups/g1/runs/r1/stop",
      expect.objectContaining({ method: "POST" }),
    ),
  );
});

test("une exécution terminée n'a pas de bouton Arrêter ; une interrompue propose Relancer", async () => {
  renderRunPage({ id: "r1", agent_id: "a1", task: "t", status: "interrupted" });
  expect(await screen.findByRole("button", { name: "Relancer" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Arrêter" })).not.toBeInTheDocument();
});
