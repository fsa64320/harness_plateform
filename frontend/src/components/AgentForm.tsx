import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, type Schemas } from "../api/client";

type AgentInput = Schemas["AgentInput"];

interface Props {
  groupId: string;
  initial?: Partial<AgentInput>;
  submitLabel?: string;
  onSubmit: (input: AgentInput) => void;
  error?: string | null;
}

/** Formulaire d'agent. Les bascules de mémoire sont ajoutées avec la mémoire (story 3). */
export default function AgentForm({
  groupId,
  initial,
  submitLabel = "Créer l'agent",
  onSubmit,
  error,
}: Props) {
  const { data: tools = [] } = useQuery({
    queryKey: ["tools", groupId],
    queryFn: () => api<Schemas["Tool"][]>(`/groups/${groupId}/tools`),
  });
  const [name, setName] = useState(initial?.name ?? "");
  const [instructions, setInstructions] = useState(initial?.instructions ?? "");
  const [modelId, setModelId] = useState(initial?.model_id ?? "qwen3");
  const [allowed, setAllowed] = useState<string[]>(initial?.allowed_tool_ids ?? []);
  const [subagents, setSubagents] = useState(initial?.subagents_enabled ?? false);
  const [agentMemory, setAgentMemory] = useState(initial?.agent_memory_enabled ?? true);
  const [groupMemory, setGroupMemory] = useState(initial?.group_memory_enabled ?? false);
  const [maxIterations, setMaxIterations] = useState(initial?.limits?.max_iterations ?? 10);
  const [maxDuration, setMaxDuration] = useState(initial?.limits?.max_duration_seconds ?? 300);
  const [maxTokens, setMaxTokens] = useState(initial?.limits?.max_tokens ?? 50000);

  const toggle = (id: string) =>
    setAllowed((current) =>
      current.includes(id) ? current.filter((i) => i !== id) : [...current, id],
    );

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({
      name,
      instructions,
      model_id: modelId,
      allowed_tool_ids: allowed,
      subagents_enabled: subagents,
      agent_memory_enabled: agentMemory,
      group_memory_enabled: groupMemory,
      limits: {
        max_iterations: maxIterations,
        max_duration_seconds: maxDuration,
        max_tokens: maxTokens,
      },
    });
  };

  return (
    <form onSubmit={submit}>
      <label>
        Nom <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <label>
        Instructions{" "}
        <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} required />
      </label>
      <label>
        Modèle (Ollama, avec appel d'outils){" "}
        <input value={modelId} onChange={(e) => setModelId(e.target.value)} required />
      </label>
      <fieldset>
        <legend>Outils autorisés</legend>
        {tools.length === 0 && (
          <p>Aucun outil au catalogue : l'agent n'aura que les outils internes.</p>
        )}
        {tools.map((tool) => (
          <div key={tool.id}>
            <label>
              <input
                type="checkbox"
                checked={allowed.includes(tool.id ?? "")}
                onChange={() => toggle(tool.id ?? "")}
              />{" "}
              {tool.name} (toutes les opérations)
            </label>
            {(tool.operations ?? []).map((op) => {
              const opId = (op as { id?: string }).id ?? "";
              return (
                <label key={opId} style={{ marginLeft: "1.5rem", display: "block" }}>
                  <input
                    type="checkbox"
                    checked={allowed.includes(opId)}
                    onChange={() => toggle(opId)}
                  />{" "}
                  {op.name} <small>({op.classification})</small>
                </label>
              );
            })}
          </div>
        ))}
      </fieldset>
      <label>
        <input
          type="checkbox"
          checked={subagents}
          onChange={(e) => setSubagents(e.target.checked)}
        />{" "}
        Autoriser les sous-agents
      </label>
      <fieldset>
        <legend>Mémoire</legend>
        <label>
          <input
            type="checkbox"
            checked={agentMemory}
            onChange={(e) => setAgentMemory(e.target.checked)}
          />{" "}
          Mémoire d'agent (retient d'une exécution à l'autre)
        </label>
        <label>
          <input
            type="checkbox"
            checked={groupMemory}
            onChange={(e) => setGroupMemory(e.target.checked)}
          />{" "}
          Mémoire de groupe (lecture, et propositions soumises à validation)
        </label>
      </fieldset>
      <fieldset>
        <legend>Limites d'exécution</legend>
        <label>
          Itérations max{" "}
          <input
            type="number"
            min={1}
            value={maxIterations}
            onChange={(e) => setMaxIterations(Number(e.target.value))}
          />
        </label>
        <label>
          Durée max (s){" "}
          <input
            type="number"
            min={1}
            value={maxDuration}
            onChange={(e) => setMaxDuration(Number(e.target.value))}
          />
        </label>
        <label>
          Tokens max{" "}
          <input
            type="number"
            min={1}
            value={maxTokens}
            onChange={(e) => setMaxTokens(Number(e.target.value))}
          />
        </label>
      </fieldset>
      {error && <p role="alert">{error}</p>}
      <button type="submit">{submitLabel}</button>
    </form>
  );
}
