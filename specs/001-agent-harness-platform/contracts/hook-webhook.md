# Contrat : hooks de type service externe (webhook)

Appel émis par la plateforme vers le service enregistré dans un `HookDefinition` de type
`webhook` (FR-012a, research R-08).

## Requête

- `POST <url>` en HTTPS uniquement.
- En-têtes :
  - `Content-Type: application/json`
  - `X-Harness-Event: before_tool_call` (ou `run_start`, `run_end`, `after_tool_call`)
  - `X-Harness-Delivery: <uuid>` : identifiant unique de l'appel
  - `X-Harness-Timestamp: <secondes epoch>`
  - `X-Harness-Signature: sha256=<hex>` : HMAC-SHA256 de `timestamp + "." + corps`, avec le
    secret de signature du hook. Le service DOIT rejeter une signature invalide ou un horodatage
    de plus de 5 minutes.
- Corps : informations **minimales** nécessaires à la décision (FR-012b). Jamais d'identifiant
  d'accès d'outil, jamais de contenu de mémoire.

```json
{
  "event": "before_tool_call",
  "group_id": "uuid",
  "run_id": "uuid",
  "agent": { "id": "uuid", "name": "veille-concurrence" },
  "triggered_by": { "type": "person", "id": "uuid" },
  "tool_call": {
    "tool": "crm",
    "operation": "create_ticket",
    "classification": "external_effect",
    "arguments": { "title": "…" }
  }
}
```

Pour `run_start` et `run_end`, `tool_call` est absent ; `run_end` ajoute `status` et
`stop_reason`. Les `arguments` sont masqués comme dans les traces.

## Réponse attendue

`200 OK` avec :

```json
{ "decision": "allow", "reason": "texte libre facultatif" }
```

- `decision` : `allow` | `block` | `log`. Pour `run_end` et `after_tool_call`, seule la
  journalisation a un effet : `block` y est consigné mais ne peut pas annuler l'action passée.
- `reason` : consignée dans l'événement `hook_evaluated`.

## Échec sûr (FR-013)

Toute situation suivante vaut `block` et est tracée avec sa cause :

- pas de réponse en 10 secondes ;
- erreur réseau ou TLS ;
- statut HTTP autre que 200 ;
- corps invalide ou `decision` inconnue.

Aucune nouvelle tentative automatique : un appel d'outil bloqué peut être relancé par l'agent,
ce qui produit un nouvel appel de hook.
