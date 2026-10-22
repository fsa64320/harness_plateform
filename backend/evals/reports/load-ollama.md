# Charge Ollama

- Hôte : http://localhost:11434
- Modèle : qwen3.5:latest

| Générations en parallèle | Débit par génération (tokens/s) | Débit total (tokens/s) | Latence p50 (s) | Latence max (s) | Échecs |
|---|---|---|---|---|---|
| 1 | 45.4 | 22.2 | 4.3 | 4.3 | 0 |
| 2 | 45.4 | 42.7 | 3.4 | 4.5 | 0 |
| 4 | 45.4 | 43.5 | 5.6 | 8.8 | 0 |
| 8 | 45.3 | 43.8 | 9.9 | 17.5 | 0 |

Lecture : SC-007 demande 20 exécutions simultanées par groupe. Le serveur Ollama ne traite en parallèle que `OLLAMA_NUM_PARALLEL` générations (les autres attendent) : si la latence max à 20 générations est trop haute pour l'usage visé, augmenter ce réglage et la mémoire GPU, ou remplacer le serveur de modèle (vLLM) derrière la même interface `ModelProvider`.
