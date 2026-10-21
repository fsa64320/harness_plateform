import { useEffect, useState } from "react";

import type { Schemas } from "../api/client";

type TraceEvent = Schemas["TraceEvent"];

// Types diffusés par le flux SSE (contracts/trace-events.md) : un écouteur par type nommé.
export const EVENT_TYPES = [
  "run_started",
  "model_step",
  "tool_call_started",
  "tool_call_finished",
  "tool_call_refused",
  "hook_evaluated",
  "approval_requested",
  "approval_decided",
  "memory_read",
  "memory_written",
  "memory_conflict",
  "group_memory_proposed",
  "subagent_started",
  "subagent_finished",
  "limit_reached",
  "config_warning",
  "run_ended",
] as const;

interface Props {
  groupId: string;
  runId: string;
  onStatus?: (status: string) => void;
}

function summary(event: TraceEvent): string {
  const p = event.payload as Record<string, unknown>;
  switch (event.type) {
    case "run_started":
      return String(p.task ?? "");
    case "model_step":
      return String(p.content || "(appel d'outil)");
    case "tool_call_started":
    case "tool_call_refused":
      return `${String(p.tool ?? "")}${p.reason ? ` — refusé (${String(p.reason)})` : ""}`;
    case "tool_call_finished":
      return `${String(p.status ?? "")} : ${String(p.result ?? p.error ?? "")}`;
    case "approval_requested":
      return `Approbation demandée pour ${String(p.operation ?? "")}`;
    case "approval_decided":
      return `${String(p.decision ?? "")}`;
    case "limit_reached":
      return `Limite atteinte : ${String(p.limit ?? "")} (${String(p.value ?? "")}/${String(p.max ?? "")})`;
    case "run_ended":
      return `${String(p.status ?? "")}${p.stop_reason ? ` (${String(p.stop_reason)})` : ""}`;
    default:
      return "";
  }
}

/** Suit une exécution en direct. Le navigateur reprend seul le flux avec `Last-Event-ID` après une
 *  coupure ; les doublons sont écartés par `seq`. */
export default function LiveTrace({ groupId, runId, onStatus }: Props) {
  const [events, setEvents] = useState<TraceEvent[]>([]);

  useEffect(() => {
    setEvents([]);
    const source = new EventSource(`/api/v1/groups/${groupId}/runs/${runId}/events`, {
      withCredentials: true,
    });
    const handler = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as TraceEvent;
      setEvents((current) =>
        current.some((e) => e.seq === event.seq)
          ? current
          : [...current, event].sort((a, b) => a.seq - b.seq),
      );
      if (event.type === "run_ended") {
        onStatus?.(String((event.payload as Record<string, unknown>).status));
        source.close();
      }
    };
    for (const type of EVENT_TYPES) source.addEventListener(type, handler as EventListener);
    return () => source.close();
    // onStatus est volontairement hors des dépendances : un nouveau rappel ne doit pas rouvrir le flux
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId, runId]);

  return (
    <ol aria-label="Trace de l'exécution" className="trace">
      {events.map((event) => (
        <li key={event.seq} data-seq={event.seq} className={`trace-${event.type}`}>
          <strong>{event.type}</strong> <span>{summary(event)}</span>
          <details>
            <summary>détail</summary>
            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
          </details>
        </li>
      ))}
    </ol>
  );
}
