# Rapport d'évaluation de la mémoire : memory

> Avant : première version de la section « Mémoire » du prompt système (simple index des fichiers, sans
> consigne d'écrire ni de consulter). Conservé pour la comparaison avant/après exigée par la constitution (V)
> lorsqu'un prompt change ; le rapport après est `memory.md`.

- Fournisseur : ollama (modèle qwen3.5:latest)
- Sans mémoire : 40.0 %
- Avec mémoire : 20.0 %
- Écart : -20.0 points
- SC-006 (au moins +20 points) : NON atteint

| Tâche | Sans mémoire | Avec mémoire |
|---|---|---|
| canal-de-contact | ✅ | ❌ |
| code-projet | ❌ | ❌ |
| devise-de-facturation | ✅ | ✅ |
| responsable | ❌ | ❌ |
| jour-de-livraison | ❌ | ❌ |

Cause : le modèle répondait « noté » sans appeler write_file, puis ne consultait pas /memories/ ; il devinait ou inventait.
