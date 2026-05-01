---
icon: lucide/terminal
---

# CLI

`piighost-api` embarque un CLI Typer avec trois sous-commandes.

```text
piighost-api serve         <pipeline> [options]
piighost-api dataset extract --output FILE [options]
piighost-api dataset metrics --input FILE  [options]
```

Exécutez `piighost-api --help` (ou n'importe quelle sous-commande avec `--help`) pour la bannière d'aide en direct.

---

## `serve`

Démarre le serveur HTTP. Charge le pipeline une seule fois et le garde chaud ; uvicorn gère le multiplexage des requêtes.

| Argument / option | Type | Défaut | Description |
|---|---|---|---|
| `pipeline` | string | requis | Chemin d'import du pipeline au format `module:variable` (ex. `pipeline:pipeline`). |
| `--host` | string | `127.0.0.1` | Hôte d'écoute. Mettre `0.0.0.0` pour exposer sur toutes les interfaces. |
| `--port` | int | `8000` | Port d'écoute. |
| `--log-level` | string | `info` | Niveau de log. L'un de `debug`, `info`, `warning`, `error`. |

Le chemin du pipeline est transmis à une factory uvicorn via la variable d'environnement `PIIGHOST_PIPELINE`, donc le serveur peut hot-reload sans reconstruire le chemin d'import.

```bash
piighost-api serve pipeline:pipeline --host 0.0.0.0 --port 8000
```

---

## `dataset extract`

Tire les traces HITL et/ou non-HITL du backend d'observation configuré (Langfuse) dans un fichier JSONL d'entrainement. Nécessite l'extra `dataset` (`uv add piighost-api[dataset]`).

La commande charge automatiquement un `.env` depuis le répertoire courant si `python-dotenv` est disponible, donc `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` peuvent y vivre au lieu d'être exportés manuellement.

| Option | Type | Défaut | Description |
|---|---|---|---|
| `--output` / `-o` | path | requis | Fichier JSONL de destination. |
| `--since` | datetime | non défini | Horodatage ISO ; ignore les traces plus anciennes. |
| `--until` | datetime | non défini | Horodatage ISO ; ignore les traces plus récentes. |
| `--mode` | enum | `all` | `all`, `hitl`, ou `model-only`. |
| `--limit` | int | non défini | S'arrête après N enregistrements. |

**Schéma de record JSONL**

```json
{
  "text": "Bonjour Patrick, comment vas tu ?",
  "entities": [[8, 15, "PERSON"]],
  "model_entities": [[8, 15, "ORG"]],
  "labels_universe": ["PERSON", "LOCATION"],
  "source": "hitl",
  "trace_id": "abc...",
  "session_id": "u1",
  "created_at": "2026-05-01T05:47:27.000Z"
}
```

- `entities` est la vérité-terrain (corrections humaines pour les records `hitl`, sortie modèle pour les records `model-only`).
- `model_entities` est toujours la prédiction du modèle ; identique à `entities` pour les records `model-only`.
- `labels_universe` est le vocabulaire du détecteur au moment de la correction quand le détecteur expose `.labels`, vide sinon.
- `source` vaut `"hitl"` pour les traces HITL, `"model"` pour les traces non-HITL.

**Sémantique des modes**

| `--mode` | Nom de trace | Source d'`entities` |
|---|---|---|
| `hitl` | `piighost.hitl_correction` | `output.detections` (humain) |
| `model-only` | `piighost.anonymize_pipeline` | enfant `piighost.detect` `output.detections` |
| `all` (défaut) | les deux | par trace |

**Exemple**

```bash
piighost-api dataset extract --output /tmp/dataset.jsonl --since 2026-04-01 --limit 1000
```

---

## `dataset metrics`

Calcule la précision / le rappel / le F1 par label depuis un JSONL produit par `dataset extract`. Stdlib pur ; aucune installation supplémentaire nécessaire.

| Option | Type | Défaut | Description |
|---|---|---|---|
| `--input` / `-i` | path | requis | Fichier JSONL à lire. |
| `--output` / `-o` | path | non défini | Écrit le rapport dans ce fichier plutôt que sur stdout. |
| `--output-format` | enum | `table` | `table`, `csv`, ou `json`. |
| `--match-mode` | enum | `strict` | `strict` (span+label exact) ou `lenient` (IoU ≥ `--iou-threshold`). |
| `--iou-threshold` | float | `0.5` | Seuil IoU en mode lenient. |
| `--source` | enum | `all` | `all`, `hitl`, ou `model` ; restreint l'agrégation à une source. |

**Colonnes de sortie**

| Colonne | Signification |
|---|---|
| `tp` | Vrai positif (modèle et humain d'accord). |
| `fp` | Faux positif (modèle a prédit, humain a supprimé ou recodifié). |
| `fn` | Faux négatif (humain a ajouté, modèle a manqué). |
| `P` | Précision = `tp / (tp + fp)`. |
| `R` | Rappel = `tp / (tp + fn)`. |
| `F1` | Moyenne harmonique de P et R. |

Le tableau indique aussi les moyennes macro et micro et, quand une confusion entre labels existe (même span, labels différents), une section de confusion.

**Exemple**

```bash
piighost-api dataset metrics --input /tmp/dataset.jsonl --source hitl
```

```text
label                    tp     fp     fn      P      R     F1
--------------------------------------------------------------
PERSON                    3      0      1   1.00   0.75   0.86
LOCATION                  2      0      1   1.00   0.67   0.80
--------------------------------------------------------------
macro avg                 -      -      -   1.00   0.71   0.83
micro avg                 -      -      -   1.00   0.71   0.83
```

---

## Workflow type

```bash
# 1. Extract the last week of HITL corrections
piighost-api dataset extract --output /tmp/last_week.jsonl --since "$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%S)"

# 2. Inspect the dataset before training
piighost-api dataset metrics --input /tmp/last_week.jsonl --source hitl

# 3. Convert to spaCy / GLiNER / your training tooling (out of scope of this CLI)
```
