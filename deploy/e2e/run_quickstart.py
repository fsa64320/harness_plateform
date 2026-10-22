"""Valide les scénarios S1 à S6 de quickstart.md contre la pile docker compose, avec un vrai modèle Ollama.

Prérequis (pile neuve : `docker compose down -v` avant, les noms d'outils étant fixes) : `docker compose -f deploy/docker-compose.yml up -d postgres dex backend frontend`, groupes
« demo » (alice) et « autre » (dan) créés avec `harness-admin`, Ollama joignable depuis le conteneur
(OLLAMA_BASE_URL=http://host.docker.internal:11434) et un modèle compatible appel d'outils.

    cd backend && uv run python ../deploy/e2e/run_quickstart.py --model qwen3.5:latest --out <rapport.md>

Le script pilote l'API comme le ferait l'interface : connexion SSO réelle (Dex), SSE, approbations,
redémarrage du backend. Un serveur MCP et un service de webhook de test tournent sur l'hôte en HTTPS.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from tests.fixtures.mcp_test_server import McpTestServer  # noqa: E402
from tests.fixtures.silent_https_server import WebhookTestServer  # noqa: E402

COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "docker-compose.yml"), "-f", str(ROOT / "deploy" / "e2e" / "docker-compose.e2e.yml")]
API = "http://localhost:8000/api/v1"
HOST_ALIAS = "host.docker.internal"
TERMINAL = {"succeeded", "failed", "stopped", "limit_reached", "interrupted", "expired", "rejected_quota"}


@dataclass
class Check:
    scenario: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    model: str
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(self, scenario: str, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(scenario, name, bool(ok), detail))
        print(f"  {'✅' if ok else '❌'} {name} {detail}")
        return bool(ok)

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  ℹ️  {text}")


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*COMPOSE, *args], capture_output=True, text=True, check=check)  # noqa: S603


async def login(email: str) -> httpx.AsyncClient:
    """Connexion SSO réelle : formulaire de Dex (utilisateur de test), puis cookie de session du backend."""

    async def rewrite(request: httpx.Request) -> None:
        if request.url.host == "dex":  # l'émetteur annoncé est dex:5556 ; l'hôte l'atteint par localhost
            request.url = request.url.copy_with(host="localhost")

    client = httpx.AsyncClient(base_url=API, timeout=60, follow_redirects=True, event_hooks={"request": [rewrite]})
    page = await client.get("/auth/login")
    form = str(page.url)
    done = await client.post(form, data={"login": email, "password": "password"})
    assert done.status_code in (200, 302), f"connexion de {email} : {done.status_code}"
    me = await client.get("/me")
    assert me.status_code == 200, f"pas de session pour {email} ({me.status_code})"
    client.event_hooks = {"request": []}
    return client


class Stack:
    def __init__(self, report: Report, model: str) -> None:
        self.r, self.model = report, model
        self.clients: dict[str, httpx.AsyncClient] = {}
        self.group = ""
        self.other_group = ""
        self.mcp = McpTestServer(host="0.0.0.0", extra_dns=(HOST_ALIAS,))  # noqa: S104
        self.webhook = WebhookTestServer(host="0.0.0.0", extra_dns=(HOST_ALIAS,), delay=60)  # noqa: S104

    # --- outils ------------------------------------------------------------------------------

    def url(self, path: str = "") -> str:
        return f"/groups/{self.group}{path}"

    async def call(self, who: str, method: str, path: str, **kw: Any) -> httpx.Response:
        return await self.clients[who].request(method, path, **kw)

    async def j(self, who: str, method: str, path: str, expect: int | tuple[int, ...] = (200, 201, 202, 204), **kw: Any) -> Any:
        r = await self.call(who, method, path, **kw)
        ok = r.status_code in (expect if isinstance(expect, tuple) else (expect,))
        assert ok, f"{method} {path} -> {r.status_code} {r.text[:300]}"
        return r.json() if r.content else None

    async def events(self, run_id: str, who: str = "alice") -> list[dict[str, Any]]:
        return await self.j(who, "GET", self.url(f"/runs/{run_id}/events"), headers={"Accept": "application/json"})

    async def run(self, run_id: str, who: str = "alice") -> dict[str, Any]:
        return await self.j(who, "GET", self.url(f"/runs/{run_id}"))

    async def wait_status(self, run_id: str, statuses: set[str], timeout: float = 240) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = await self.run(run_id)
            if run["status"] in statuses:
                return run
            await asyncio.sleep(0.5)
        raise TimeoutError(f"{run_id} : statut {run['status']} après {timeout}s")

    async def wait_event(self, run_id: str, type_: str, timeout: float = 240) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for e in await self.events(run_id):
                if e["type"] == type_:
                    return e
            await asyncio.sleep(0.4)
        raise TimeoutError(f"événement {type_} jamais reçu ({run_id})")

    async def launch(self, who: str, agent_id: str, task: str, **extra: Any) -> dict[str, Any]:
        return await self.j(who, "POST", self.url("/runs"), json={"agent_id": agent_id, "task": task, **extra})

    async def agent(self, who: str, name: str, ops: list[str], **overrides: Any) -> dict[str, Any]:
        body = {
            "name": name, "instructions": overrides.pop("instructions", "Tu es un assistant. Utilise les outils quand on te le demande."),
            "model_id": self.model, "allowed_tool_ids": ops,
            "limits": overrides.pop("limits", {"max_iterations": 12, "max_duration_seconds": 300, "max_tokens": 100000}),
            **overrides,
        }
        return await self.j(who, "POST", self.url("/agents"), json=body)

    async def stream_lags(self, who: str, run_id: str) -> list[float]:
        """Écarts (s) entre l'horodatage de chaque événement et sa réception sur le flux SSE (SC-002)."""
        lags: list[float] = []
        async with self.clients[who].stream("GET", self.url(f"/runs/{run_id}/events"), headers={"Accept": "text/event-stream"}) as response:
            data: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and data:
                    event = json.loads("".join(data))
                    data = []
                    lags.append((dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(event["occurred_at"])).total_seconds())
                    if event["type"] == "run_ended":
                        break
        return lags

    # --- préparation ---------------------------------------------------------------------------

    async def setup(self) -> None:
        ca = ROOT / "deploy" / "e2e" / "ca"
        ca.mkdir(exist_ok=True)
        self.mcp.start()
        self.webhook.start()
        (ca / "ca.pem").write_text(self.mcp.cert_path.read_text() + self.webhook.cert_path.read_text())
        compose("up", "-d", "--force-recreate", "backend")
        await self.wait_backend()
        for email in ("alice", "bob", "carol", "dan"):
            self.clients[email if email != "alice" else "alice"] = await login(f"{email}@example.test")
        me = await self.j("alice", "GET", "/me")
        self.group = next(m["group_id"] for m in me["memberships"] if m["group_name"] == "demo")
        self.other_group = next(m["group_id"] for m in (await self.j("dan", "GET", "/me"))["memberships"] if m["group_name"] == "autre")
        await self.j("alice", "POST", self.url("/members"), json={"email": "bob@example.test", "roles": []}, expect=(201, 409))
        await self.j("alice", "POST", self.url("/members"), json={"email": "carol@example.test", "roles": ["group_memory_manager"]}, expect=(201, 409))
        members = await self.j("alice", "GET", self.url("/members"))
        alice_member = next(m for m in members if m["person_email"] == "alice@example.test")
        await self.j("alice", "PATCH", self.url(f"/members/{alice_member['id']}"), json={
            "roles": ["admin", "tool_manager", "approver", "group_memory_manager"], "max_concurrent_runs": 20})

        tool = await self.j("alice", "POST", self.url("/tools"), json={
            "kind": "mcp_server", "name": "crm", "description": "CRM de test",
            "mcp": {"url": f"https://{HOST_ALIAS}:{self.mcp.port}/mcp"}})
        self.tool = tool
        self.ops = {o["name"]: o["id"] for o in tool["operations"]}
        for name in ("lookup", "wait"):
            await self.j("alice", "PUT", self.url(f"/tools/{tool['id']}/operations/{name}/classification"), json={"classification": "read_only"})

    async def wait_backend(self) -> None:
        for _ in range(60):
            try:
                if (await httpx.AsyncClient().get(f"{API}/me")).status_code == 401:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        raise TimeoutError("le backend ne répond pas")

    # --- scénarios -----------------------------------------------------------------------------

    async def s1(self) -> None:
        sc = "S1"
        print("S1 : lancer, suivre et arrêter un agent")
        agent = await self.agent("alice", "s1-lecture", [self.ops["lookup"], self.ops["wait"]], limits={"max_iterations": 5, "max_duration_seconds": 300, "max_tokens": 100000})
        run = await self.launch("alice", agent["id"], "Appelle l'outil crm_lookup avec query='acme', puis résume le résultat en une phrase.")
        lags = await self.stream_lags("alice", run["id"])
        done = await self.wait_status(run["id"], TERMINAL)
        types = [e["type"] for e in await self.events(run["id"])]
        self.r.check(sc, "run_started, model_step, tool_call_*, run_ended présents", {"run_started", "model_step", "tool_call_started", "tool_call_finished", "run_ended"} <= set(types), str(types[:12]))
        self.r.check(sc, "exécution « succeeded »", done["status"] == "succeeded", done["status"])
        self.r.check(sc, "chaque événement arrive sur le flux SSE en moins de 2 s (SC-002)", bool(lags) and max(lags) < 2.0, f"max {max(lags):.2f}s sur {len(lags)} événements" if lags else "aucun événement")
        self.r.check(sc, "l'outil MCP a été appelé", any(c[0] == "lookup" for c in self.mcp.calls))

        run = await self.launch("alice", agent["id"], "Appelle l'outil crm_wait avec seconds=30, puis réponds « fini ».")
        await self.wait_event(run["id"], "tool_call_started")
        t0 = time.monotonic()
        await self.j("alice", "POST", self.url(f"/runs/{run['id']}/stop"))
        done = await self.wait_status(run["id"], TERMINAL, timeout=30)
        took = time.monotonic() - t0
        ended = (await self.events(run["id"]))[-1]
        self.r.check(sc, "arrêt manuel en moins de 5 s (SC-003)", done["status"] == "stopped" and took < 5, f"{took:.2f}s, statut {done['status']}")
        self.r.check(sc, "run_ended porte stop_reason = manual", ended["type"] == "run_ended" and ended["payload"]["stop_reason"] == "manual")

        limited = await self.agent("alice", "s1-limite", [self.ops["lookup"]], limits={"max_iterations": 1, "max_duration_seconds": 300, "max_tokens": 100000})
        status = ""
        for _ in range(3):  # un vrai modèle peut répondre sans outil : on réessaie
            run = await self.launch("alice", limited["id"], "Appelle crm_lookup avec query='acme' puis résume le résultat.")
            done = await self.wait_status(run["id"], TERMINAL)
            status = done["status"]
            if status == "limit_reached":
                break
        self.r.check(sc, "limite d'itérations : limit_reached", status == "limit_reached" and "limit_reached" in [e["type"] for e in await self.events(run["id"])], status)

    async def s2(self) -> None:
        sc = "S2"
        print("S2 : outil non autorisé et approbation")
        lookup_only = await self.agent("alice", "s2-lecture", [self.ops["lookup"]])
        run = await self.launch("alice", lookup_only["id"], "Envoie le message « bonjour » avec l'outil crm_send_message.")
        await self.wait_status(run["id"], TERMINAL)
        calls = [c for c in self.mcp.calls if c[0] == "send_message"]
        self.r.check(sc, "send_message jamais exécuté pour un agent qui n'y a pas droit", not calls)
        refused = [e for e in await self.events(run["id"]) if e["type"] == "tool_call_refused"]
        self.r.note("Avec un vrai modèle, l'outil non autorisé n'est même pas proposé au modèle (il ne peut donc pas l'appeler) : "
                    f"{len(refused)} refus consigné ici. Le refus consigné `not_allowed` / `unknown_tool` est vérifié par les tests automatisés (faux modèle).")

        both = await self.agent("alice", "s2-envoi", [self.ops["lookup"], self.ops["send_message"]])
        run = await self.launch("alice", both["id"], "Envoie le message « bonjour » avec l'outil crm_send_message, puis dis « envoyé ».")
        await self.wait_event(run["id"], "approval_requested")
        run_row = await self.run(run["id"])
        self.r.check(sc, "l'exécution passe en awaiting_approval", run_row["status"] == "awaiting_approval", run_row["status"])
        approvals = await self.j("alice", "GET", self.url("/approvals?status=pending"))
        approval = next(a for a in approvals if a["run_id"] == run["id"])
        bob = await self.call("bob", "POST", self.url(f"/approvals/{approval['id']}/decision"), json={"decision": "approve"})
        self.r.check(sc, "Bob (simple membre) ne peut pas décider : 403", bob.status_code == 403, str(bob.status_code))
        before = len([c for c in self.mcp.calls if c[0] == "send_message"])
        await self.j("alice", "POST", self.url(f"/approvals/{approval['id']}/decision"), json={"decision": "approve"})
        done = await self.wait_status(run["id"], TERMINAL)
        after = len([c for c in self.mcp.calls if c[0] == "send_message"])
        decided = next(e for e in await self.events(run["id"]) if e["type"] == "approval_decided")
        me = await self.j("alice", "GET", "/me")
        self.r.check(sc, "après l'approbation d'Alice, l'appel s'exécute", after == before + 1 and done["status"] == "succeeded", f"statut {done['status']}")
        self.r.check(sc, "approval_decided porte l'identité d'Alice", decided["payload"]["decided_by"] == me["id"] and decided["payload"]["decision"] == "approve")

    async def s3(self) -> None:
        sc = "S3"
        print("S3 : catalogue, hooks et identifiants")
        hook = await self.j("alice", "POST", self.url("/tools"), json={"kind": "hook", "name": "bloque-effets-externes", "hook": {
            "events": ["before_tool_call"], "hook_type": "rule", "condition": {"classification": "external_effect"}, "decision": "block"}})
        agent = await self.agent("alice", "s3-hook", [self.ops["lookup"], self.ops["send_message"], hook["id"]])
        sent = len([c for c in self.mcp.calls if c[0] == "send_message"])
        run = await self.launch("alice", agent["id"], "Envoie le message « bonjour » avec l'outil crm_send_message.")
        done = await self.wait_status(run["id"], TERMINAL)  # un appel bloqué par un hook ne sollicite personne
        events = await self.events(run["id"])
        evaluated = [e["payload"] for e in events if e["type"] == "hook_evaluated"]
        self.r.check(sc, "le hook règle bloque l'appel à effet externe (aucune approbation demandée)", done["status"] != "awaiting_approval" and not [a for a in await self.j("alice", "GET", self.url("/approvals?status=pending")) if a["run_id"] == run["id"]] and len([c for c in self.mcp.calls if c[0] == "send_message"]) == sent, done["status"])
        self.r.check(sc, "hook_evaluated désigne le hook et la décision block", any(p["decision"] == "block" and p["hook_id"] == hook["id"] for p in evaluated), str(evaluated[:1]))

        webhook = await self.j("alice", "POST", self.url("/tools"), json={"kind": "hook", "name": "validation-externe", "hook": {
            "events": ["before_tool_call"], "hook_type": "webhook", "url": f"https://{HOST_ALIAS}:{self.webhook.port}/decide",
            "signing_secret": "whsec-e2e-test", "timeout_seconds": 3}})
        agent2 = await self.agent("alice", "s3-webhook", [self.ops["lookup"], webhook["id"]])
        run = await self.launch("alice", agent2["id"], "Appelle crm_lookup avec query='acme'.")
        t0 = time.monotonic()
        await self.wait_status(run["id"], TERMINAL)
        evaluated = [e["payload"] for e in await self.events(run["id"]) if e["type"] == "hook_evaluated"]
        blocked = [p for p in evaluated if p["hook_type"] == "webhook" and p["decision"] == "block" and "timeout" in p["reason"]]
        self.r.check(sc, "webhook muet : blocage après le délai, cause « timeout » tracée", bool(blocked), (blocked or evaluated or [{}])[0].get("reason", ""))

        secret = "vault-token-e2e-7f3a9c1d5b"
        vault = await self.j("alice", "POST", self.url("/tools"), json={"kind": "mcp_server", "name": "vault", "mcp": {"url": f"https://{HOST_ALIAS}:{self.mcp.port}/mcp", "credential": secret}})
        for op in ("whoami", "lookup"):
            await self.j("alice", "PUT", self.url(f"/tools/{vault['id']}/operations/{op}/classification"), json={"classification": "read_only"})
        vops = {o["name"]: o["id"] for o in vault["operations"]}
        agent3 = await self.agent("alice", "s3-secret", [vops["whoami"]])
        run = await self.launch("alice", agent3["id"], "Appelle l'outil vault_whoami et recopie exactement sa réponse dans ta réponse finale.")
        await self.wait_status(run["id"], TERMINAL)
        surfaces = [
            json.dumps(await self.j("alice", "GET", self.url("/tools"))),
            json.dumps(await self.events(run["id"])),
            json.dumps(await self.run(run["id"])),
            json.dumps(await self.j("alice", "GET", self.url("/agents"))),
        ]
        logs = compose("logs", "--no-log-prefix", "backend").stdout
        self.r.check(sc, "l'identifiant MCP a bien atteint le serveur MCP", any(h.get("authorization") == f"Bearer {secret}" for h in self.mcp.received_headers))
        self.r.check(sc, "valeur absente de toutes les réponses d'API et événements de trace", not any(secret in s for s in surfaces))
        self.r.check(sc, "valeur absente des logs du backend", secret not in logs)
        self.r.check(sc, "valeur chiffrée au repos (absente de la base)", subprocess.run([*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "harness", "-d", "harness", "-Atc", f"SELECT count(*) FROM credentials WHERE convert_from(ciphertext,'LATIN1') LIKE '%{secret}%'"], capture_output=True, text=True, check=False).stdout.strip() == "0")

    async def s4(self) -> None:
        sc = "S4"
        print("S4 : mémoire d'agent et de groupe")
        agent = await self.agent("alice", "s4-memoire", [], group_memory_enabled=True)
        run = await self.launch("alice", agent["id"], "Retiens pour les prochaines fois : le client Acme préfère être contacté par e-mail, jamais par téléphone.")
        await self.wait_status(run["id"], TERMINAL)
        written = [e for e in await self.events(run["id"]) if e["type"] == "memory_written"]
        self.r.check(sc, "memory_written à la première exécution", bool(written), str([w["payload"] for w in written][:1]))
        run2 = await self.launch("alice", agent["id"], "Par quel canal dois-je contacter le client Acme ? Réponds en une phrase.")
        done = await self.wait_status(run2["id"], TERMINAL)
        read = [e["payload"] for e in await self.events(run2["id"]) if e["type"] == "memory_read"]
        self.r.check(sc, "memory_read (scope = agent) à la seconde exécution", any(r["scope"] == "agent" for r in read), str(read[:1]))
        self.r.check(sc, "la réponse réutilise la préférence", "mail" in (done["result"] or "").lower() or "courriel" in (done["result"] or "").lower(), (done["result"] or "")[:100])

        proposer = await self.agent("alice", "s4-proposition", [], group_memory_enabled=True)
        run3 = await self.launch("alice", proposer["id"], "Propose à la mémoire de groupe, avec propose_group_memory, le fait suivant dans le fichier faits/tarifs.md : « Tarif 2026 : 12 euros ».")
        await self.wait_status(run3["id"], TERMINAL)
        proposals = await self.j("carol", "GET", self.url("/group-memory/proposals?status=pending"))
        group_before = await self.j("carol", "GET", self.url("/group-memory"))
        self.r.check(sc, "group_memory_proposed ; rien dans la mémoire de groupe avant acceptation", bool(proposals) and not group_before, f"{len(proposals)} proposition(s), {len(group_before)} entrée(s)")
        if proposals:
            await self.j("carol", "POST", self.url(f"/group-memory/proposals/{proposals[0]['id']}/decision"), json={"decision": "accept"})
            group_after = await self.j("carol", "GET", self.url("/group-memory"))
            reader = await self.agent("alice", "s4-lecteur", [], group_memory_enabled=True)
            run4 = await self.launch("alice", reader["id"], "Lis le fichier /group-memory/faits/tarifs.md et dis-moi le tarif 2026.")
            await self.wait_status(run4["id"], TERMINAL)
            gread = [e["payload"] for e in await self.events(run4["id"]) if e["type"] == "memory_read"]
            self.r.check(sc, "après acceptation par Carol, un autre agent lit l'entrée (scope = group)", len(group_after) == 1 and any(r["scope"] == "group" for r in gread), str(gread[:1]))
        copy = await self.j("alice", "POST", self.url(f"/agents/{agent['id']}/duplicate"))
        listing = await self.j("alice", "GET", self.url(f"/agents/{copy['id']}/memory"))
        self.r.check(sc, "agent dupliqué : mémoire d'agent vide", listing == [])

    async def s5(self) -> None:
        sc = "S5"
        print("S5 : isolation entre groupes et comptes de service")
        agent = await self.agent("alice", "s5-agent", [self.ops["lookup"], self.ops["send_message"]])
        run = await self.launch("alice", agent["id"], "Appelle crm_lookup avec query='acme'.")
        await self.wait_status(run["id"], TERMINAL)
        probes = [
            f"/groups/{self.other_group}/agents/{agent['id']}", f"/groups/{self.other_group}/runs/{run['id']}",
            f"/groups/{self.other_group}/runs/{run['id']}/events", f"/groups/{self.other_group}/tools/{self.tool['id']}",
            f"/groups/{self.other_group}/agents/{agent['id']}/memory", f"/groups/{self.group}/agents/{agent['id']}",
        ]
        codes = [(await self.call("dan", "GET", p)).status_code for p in probes]
        self.r.check(sc, "Dan (autre groupe) : 404 sur agent, trace et mémoire de « demo »", codes[:5] == [404] * 5 and codes[5] == 404, str(codes))

        account = await self.j("alice", "POST", self.url("/service-accounts"), json={"name": "ci", "allowed_agent_ids": [agent["id"]]})
        token = {"Authorization": f"Bearer {account['token']}"}
        async with httpx.AsyncClient(base_url=API, timeout=60) as program:
            launched = (await program.post(self.url("/runs"), json={"agent_id": agent["id"], "task": "Envoie le message « salut » avec crm_send_message."}, headers=token)).json()
            self.r.check(sc, "lancement par jeton : launch_mode = programmatic", launched.get("launch_mode") == "programmatic", str(launched.get("launch_mode")))
            await self.wait_event(launched["id"], "approval_requested")
            pending = [a for a in await self.j("alice", "GET", self.url("/approvals?status=pending")) if a["run_id"] == launched["id"]]
            denied = await program.post(self.url(f"/approvals/{pending[0]['id']}/decision"), json={"decision": "approve"}, headers=token)
            self.r.check(sc, "le jeton ne peut pas approuver : 403", denied.status_code == 403, str(denied.status_code))
            await self.j("alice", "POST", self.url(f"/approvals/{pending[0]['id']}/decision"), json={"decision": "reject"})
            await self.wait_status(launched["id"], TERMINAL)
            revoked = await self.call("alice", "POST", self.url(f"/service-accounts/{account['id']}/revoke"))
            after = await program.post(self.url("/runs"), json={"agent_id": agent["id"], "task": "x"}, headers=token)
            self.r.check(sc, "révocation : nouveau lancement refusé (401)", revoked.status_code == 204 and after.status_code == 401, f"{revoked.status_code}/{after.status_code}")

    async def s6(self) -> None:
        sc = "S6"
        print("S6 : redémarrage de la plateforme")
        waiter = await self.agent("alice", "s6-attente", [self.ops["wait"]])
        sender = await self.agent("alice", "s6-approbation", [self.ops["send_message"]])
        long_run = await self.launch("alice", waiter["id"], "Appelle crm_wait avec seconds=120.")
        await self.wait_event(long_run["id"], "tool_call_started")
        pending = await self.launch("alice", sender["id"], "Envoie le message « après redémarrage » avec crm_send_message.")
        await self.wait_event(pending["id"], "approval_requested")

        compose("restart", "backend")
        await self.wait_backend()
        for who in list(self.clients):  # la session est un cookie signé : elle survit au redémarrage
            assert (await self.call(who, "GET", "/me")).status_code == 200

        first = await self.run(long_run["id"])
        last = (await self.events(long_run["id"]))[-1]
        self.r.check(sc, "l'exécution en cours devient « interrompue » (platform_restart)", first["status"] == "interrupted" and last["payload"].get("stop_reason") == "platform_restart", f"{first['status']} / {last['payload'].get('stop_reason')}")
        second = await self.run(pending["id"])
        self.r.check(sc, "l'exécution en attente d'approbation est conservée", second["status"] == "awaiting_approval", second["status"])
        approval = next(a for a in await self.j("alice", "GET", self.url("/approvals?status=pending")) if a["run_id"] == pending["id"])
        sent = len([c for c in self.mcp.calls if c[0] == "send_message"])
        await self.j("alice", "POST", self.url(f"/approvals/{approval['id']}/decision"), json={"decision": "approve"})
        done = await self.wait_status(pending["id"], TERMINAL)
        self.r.check(sc, "elle reprend après la décision et exécute l'appel", done["status"] == "succeeded" and len([c for c in self.mcp.calls if c[0] == "send_message"]) == sent + 1, done["status"])
        quick = await self.agent("alice", "s6-relance", [self.ops["lookup"]])
        relaunch = await self.launch("alice", quick["id"], "Appelle crm_lookup avec query='acme'.", relaunch_of=long_run["id"])
        await self.wait_status(relaunch["id"], TERMINAL)
        started = (await self.events(relaunch["id"]))[0]["payload"]
        self.r.check(sc, "« Relancer » crée une exécution liée (relaunched_from)", relaunch["relaunched_from"] == long_run["id"] and started["relaunched_from"] == long_run["id"])

    def close(self) -> None:
        self.mcp.close()
        self.webhook.close()


def render(report: Report) -> str:
    scenarios: dict[str, list[Check]] = {}
    for c in report.checks:
        scenarios.setdefault(c.scenario, []).append(c)
    total, passed = len(report.checks), sum(c.ok for c in report.checks)
    lines = [
        "# Validation de quickstart.md sur docker compose",
        "",
        f"- Date : {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- Pile : `deploy/docker-compose.yml` (postgres, dex, backend, frontend) + `deploy/e2e/docker-compose.e2e.yml`",
        f"- Modèle : Ollama `{report.model}` (hôte, via host.docker.internal)",
        f"- Résultat : **{passed}/{total} vérifications réussies**",
        "",
    ]
    for name, checks in scenarios.items():
        lines += [f"## {name}", "", "| Vérification | Résultat | Détail |", "|---|---|---|"]
        lines += [f"| {c.name} | {'✅' if c.ok else '❌'} | {c.detail.replace('|', '/')[:160]} |" for c in checks]
        lines.append("")
    if report.notes:
        lines += ["## Notes", "", *(f"- {n}" for n in report.notes), ""]
    return "\n".join(lines)


async def main(model: str, out: Path, only: list[str]) -> int:
    report = Report(model)
    stack = Stack(report, model)
    try:
        await stack.setup()
        for name in ("s1", "s2", "s3", "s4", "s5", "s6"):
            if only and name not in only:
                continue
            try:
                await getattr(stack, name)()
            except Exception as exc:  # noqa: BLE001 - un scénario en échec n'arrête pas les suivants
                report.check(name.upper(), f"scénario interrompu : {type(exc).__name__}", False, str(exc)[:200])
    finally:
        stack.close()
        out.write_text(render(report))
        print(f"\nRapport : {out}")
    return 0 if all(c.ok for c in report.checks) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.5:latest")
    parser.add_argument("--out", default=str(ROOT / "specs" / "001-agent-harness-platform" / "checklists" / "quickstart-run.md"))
    parser.add_argument("--only", nargs="*", default=[], help="ex. s1 s2")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.model, Path(args.out), args.only)))
