---
icon: lucide/zap
---

# Démarrage rapide

Démarrer un serveur piighost-api, effectuer votre première requête d'anonymisation, voir les placeholders en action. Cinq minutes depuis un clone de repo.

## 1. Écrire un `pipeline.py`

Le serveur charge un unique pipeline au démarrage, spécifié via `module:variable`. Créez `pipeline.py` à l'endroit d'où vous lancerez le serveur. Une configuration regex seule suffit pour jouer :

```python
from piighost.anonymizer import Anonymizer
from piighost.detector import RegexDetector
from piighost.linker.entity import ExactEntityLinker
from piighost.pipeline.thread import ThreadAnonymizationPipeline
from piighost.placeholder import LabelCounterPlaceholderFactory
from piighost.resolver.entity import MergeEntityConflictResolver
from piighost.resolver.span import ConfidenceSpanConflictResolver

pipeline = ThreadAnonymizationPipeline(
    detector=RegexDetector(
        patterns={
            "EMAIL": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
            "PHONE": r"\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{1,4}){1,4}",
        }
    ),
    span_resolver=ConfidenceSpanConflictResolver(),
    entity_linker=ExactEntityLinker(),
    entity_resolver=MergeEntityConflictResolver(),
    anonymizer=Anonymizer(LabelCounterPlaceholderFactory()),
)
```

## 2. Démarrer le serveur

```bash
piighost-api serve pipeline:pipeline --host 0.0.0.0 --port 8000
```

Log attendu : `Pipeline ready: RegexDetector` et uvicorn en écoute sur `0.0.0.0:8000`.

## 3. Première requête

```bash
curl -X POST http://localhost:8000/v1/anonymize \
  -H "Content-Type: application/json" \
  -d '{"text": "Email me at patrick@acme.com", "thread_id": "demo"}'
```

Réponse :

```json
{
  "anonymized_text": "Email me at <<EMAIL:1>>",
  "entities": [
    {
      "label": "EMAIL",
      "placeholder": "<<EMAIL:1>>",
      "detections": [{"text": "patrick@acme.com", "label": "EMAIL", "start_pos": 12, "end_pos": 28, "confidence": 1.0}]
    }
  ]
}
```

## 4. Aller-retour

Renvoyez le texte anonymisé via `/v1/deanonymize` (chemin avec cache) pour récupérer l'original :

```bash
curl -X POST http://localhost:8000/v1/deanonymize \
  -H "Content-Type: application/json" \
  -d '{"text": "Email me at <<EMAIL:1>>", "thread_id": "demo"}'
```

Réponse : le texte original `Email me at patrick@acme.com`.

## 5. Optionnel : observation

Définissez `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (ou `OPIK_API_KEY`) dans votre environnement avant de démarrer le serveur. Chaque appel anonymize émet alors un arbre de traces (`piighost.anonymize_pipeline` → `detect` → `link` → `placeholder` → `guard`). Voir [Endpoints REST](../reference/endpoints.md) pour le comportement par endpoint.

## Chemin Docker

Si vous préférez Docker, la page [Installation](installation.md) documente l'image GHCR. Le même `pipeline.py` se monte via un volume.

## Suite

- [Endpoints REST](../reference/endpoints.md) — chaque endpoint, avec les schémas de requête et de réponse.
- [CLI](../reference/cli.md) — flags du serveur et sous-commandes `dataset extract|metrics`.
