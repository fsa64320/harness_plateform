import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ChangeEvent, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError, del, post, put, type Schemas } from "../api/client";

type Tool = Schemas["Tool"];
type Operation = NonNullable<Tool["operations"]>[number] & { id?: string };
type Mode = null | "mcp" | "skill" | "hook";

const HOOK_EVENTS = ["run_start", "run_end", "before_tool_call", "after_tool_call"] as const;

function McpForm({ onSubmit }: { onSubmit: (body: unknown) => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  // L'identifiant est en écriture seule : on ne le relit jamais et le champ est vidé après envoi.
  const [credential, setCredential] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({
      kind: "mcp_server",
      name,
      description: "",
      mcp: { url, ...(credential ? { credential } : {}) },
    });
    setCredential("");
  };
  return (
    <form onSubmit={submit} aria-label="Serveur MCP">
      <label>
        Nom <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <label>
        URL (HTTPS){" "}
        <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} required />
      </label>
      <label>
        Identifiant d'accès (écriture seule)
        <input
          type="password"
          autoComplete="new-password"
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
        />
      </label>
      <button type="submit">Enregistrer le serveur</button>
    </form>
  );
}

function SkillForm({ onSubmit }: { onSubmit: (body: unknown) => void }) {
  const [name, setName] = useState("");
  const [files, setFiles] = useState<Record<string, string>>({});
  const read = async (e: ChangeEvent<HTMLInputElement>) => {
    const next: Record<string, string> = {};
    for (const file of Array.from(e.target.files ?? [])) {
      const path = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      // un dossier choisi porte son nom en préfixe : on le retire pour que SKILL.md soit à la racine
      next[path.includes("/") ? path.split("/").slice(1).join("/") : path] = await file.text();
    }
    setFiles(next);
  };
  return (
    <form
      aria-label="Skill"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ kind: "skill", name, description: "", skill: { files } });
      }}
    >
      <label>
        Nom <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <label>
        Fichiers de la skill (SKILL.md requis)
        <input type="file" multiple onChange={read} />
      </label>
      <p>Les scripts d'une skill ne sont jamais exécutés par la plateforme.</p>
      <button type="submit" disabled={!files["SKILL.md"]}>
        Enregistrer la skill
      </button>
    </form>
  );
}

function HookForm({ onSubmit }: { onSubmit: (body: unknown) => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"rule" | "webhook">("rule");
  const [events, setEvents] = useState<string[]>(["before_tool_call"]);
  const [classification, setClassification] = useState("");
  const [tool, setTool] = useState("");
  const [decision, setDecision] = useState("block");
  const [priority, setPriority] = useState(100);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const condition: Record<string, string> = {};
    if (classification) condition.classification = classification;
    if (tool) condition.tool = tool;
    onSubmit({
      kind: "hook",
      name,
      description: "",
      hook:
        type === "rule"
          ? { events, hook_type: "rule", priority, condition, decision }
          : { events, hook_type: "webhook", priority, url, signing_secret: secret },
    });
    setSecret("");
  };
  const toggle = (event: string) =>
    setEvents((cur) => (cur.includes(event) ? cur.filter((x) => x !== event) : [...cur, event]));

  return (
    <form onSubmit={submit} aria-label="Hook">
      <label>
        Nom <input value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <fieldset>
        <legend>Événements</legend>
        {HOOK_EVENTS.map((event) => (
          <label key={event}>
            <input
              type="checkbox"
              checked={events.includes(event)}
              onChange={() => toggle(event)}
            />{" "}
            {event}
          </label>
        ))}
      </fieldset>
      <label>
        Type{" "}
        <select value={type} onChange={(e) => setType(e.target.value as "rule" | "webhook")}>
          <option value="rule">Règle</option>
          <option value="webhook">Service externe (webhook)</option>
        </select>
      </label>
      <label>
        Priorité{" "}
        <input
          type="number"
          value={priority}
          onChange={(e) => setPriority(Number(e.target.value))}
        />
      </label>
      {type === "rule" ? (
        <>
          <label>
            Classification{" "}
            <select value={classification} onChange={(e) => setClassification(e.target.value)}>
              <option value="">Toutes</option>
              <option value="external_effect">À effet externe (ou non classé)</option>
              <option value="read_only">Lecture seule</option>
            </select>
          </label>
          <label>
            Outil (nom, opération ou serveur){" "}
            <input value={tool} onChange={(e) => setTool(e.target.value)} />
          </label>
          <label>
            Décision{" "}
            <select value={decision} onChange={(e) => setDecision(e.target.value)}>
              <option value="allow">Autoriser</option>
              <option value="block">Bloquer</option>
              <option value="log">Journaliser</option>
            </select>
          </label>
        </>
      ) : (
        <>
          <label>
            URL du service (HTTPS){" "}
            <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} required />
          </label>
          <label>
            Secret de signature (écriture seule)
            <input
              type="password"
              autoComplete="new-password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              required
            />
          </label>
          <p>Sans réponse en 10 secondes, l'action concernée est bloquée.</p>
        </>
      )}
      <button type="submit">Enregistrer le hook</button>
    </form>
  );
}

const CLASSIFICATIONS = [
  ["read_only", "Lecture seule"],
  ["external_effect", "À effet externe"],
  ["unclassified", "Non classée"],
] as const;

export default function ToolsPage() {
  const { groupId = "" } = useParams();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>(null);
  const [error, setError] = useState<string | null>(null);
  const key = ["tools", groupId];

  const { data: tools = [], isLoading } = useQuery({
    queryKey: key,
    queryFn: () => api<Tool[]>(`/groups/${groupId}/tools`),
  });
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : "Erreur inattendue");
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });

  const create = useMutation({
    mutationFn: (body: unknown) => post<Tool>(`/groups/${groupId}/tools`, body),
    onSuccess: () => {
      setMode(null);
      setError(null);
      return refresh();
    },
    onError: fail,
  });
  const retire = useMutation({
    mutationFn: (id: string) => del(`/groups/${groupId}/tools/${id}`),
    onSuccess: refresh,
    onError: fail,
  });
  const classify = useMutation({
    mutationFn: ({
      tool,
      op,
      classification,
    }: {
      tool: string;
      op: string;
      classification: string;
    }) =>
      put(`/groups/${groupId}/tools/${tool}/operations/${op}/classification`, { classification }),
    onSuccess: refresh,
    onError: fail,
  });

  if (isLoading) return <p role="status">Chargement…</p>;
  return (
    <section>
      <h2>Catalogue d'outils</h2>
      {error && <p role="alert">{error}</p>}
      <p>
        <button type="button" onClick={() => setMode("mcp")}>
          Ajouter un serveur MCP
        </button>{" "}
        <button type="button" onClick={() => setMode("skill")}>
          Ajouter une skill
        </button>{" "}
        <button type="button" onClick={() => setMode("hook")}>
          Ajouter un hook
        </button>
      </p>
      {mode === "mcp" && <McpForm onSubmit={(b) => create.mutate(b)} />}
      {mode === "skill" && <SkillForm onSubmit={(b) => create.mutate(b)} />}
      {mode === "hook" && <HookForm onSubmit={(b) => create.mutate(b)} />}
      <ul>
        {tools.map((tool) => (
          <li key={tool.id}>
            <strong>{tool.name}</strong> <small>({tool.kind})</small>
            {tool.has_credential && <em> identifiant enregistré</em>}{" "}
            <button type="button" onClick={() => retire.mutate(tool.id ?? "")}>
              Retirer
            </button>
            {(tool.operations ?? []).length > 0 && (
              <ul>
                {(tool.operations as Operation[]).map((op) => (
                  <li key={op.name}>
                    {op.name}{" "}
                    <select
                      aria-label={`Classification de ${op.name}`}
                      value={op.classification}
                      onChange={(e) =>
                        classify.mutate({
                          tool: tool.id ?? "",
                          op: op.name ?? "",
                          classification: e.target.value,
                        })
                      }
                    >
                      {CLASSIFICATIONS.map(([value, label]) => (
                        <option key={value} value={value} disabled={value === "unclassified"}>
                          {label}
                        </option>
                      ))}
                    </select>
                    {op.classification === "unclassified" && (
                      <small>
                        {" "}
                        — traitée comme à effet externe : chaque appel demande une approbation
                      </small>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {tool.hook && (
              <p>
                <small>
                  {tool.hook.hook_type === "rule"
                    ? `règle → ${tool.hook.decision}`
                    : "service externe"}{" "}
                  — {tool.hook.events.join(", ")}
                </small>
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
