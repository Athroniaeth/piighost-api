---
icon: lucide/download
---

# Installation

## Pré-requis

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommandé), pip ou Docker
- Optionnel : une instance Redis pour le cache partagé, un compte Langfuse ou Opik pour les traces d'observation

## Installation Python

=== "uv"

    ```bash
    uv add piighost-api
    ```

=== "pip"

    ```bash
    pip install piighost-api
    ```

L'installation de base n'embarque que les détecteurs regex. Les détecteurs NER proviennent des extras de la bibliothèque `piighost` (par exemple `piighost[gliner2]`).

## Extras optionnels

`piighost-api` expose trois extras optionnels qui apportent l'observation ou l'outillage dataset :

=== "uv"

    ```bash
    uv add piighost-api[langfuse]   # observation traces to Langfuse
    uv add piighost-api[opik]       # observation traces to Opik
    uv add piighost-api[dataset]    # piighost-api dataset extract|metrics CLI
    ```

=== "pip"

    ```bash
    pip install piighost-api[langfuse]
    pip install piighost-api[opik]
    pip install piighost-api[dataset]
    ```

Les extras se composent : `piighost-api[langfuse,dataset]` active l'observation et le CLI dataset en une seule installation.

## Docker

Une image pré-construite est publiée sur GitHub Container Registry :

```bash
docker pull ghcr.io/athroniaeth/piighost-api:latest
```

Montez votre `pipeline.py` et surchargez `EXTRA_PACKAGES` pour installer les extras du détecteur au démarrage :

```yaml
services:
  piighost-api:
    image: ghcr.io/athroniaeth/piighost-api:latest
    environment:
      - EXTRA_PACKAGES=piighost[gliner2,langfuse]
      - LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}
      - LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}
    volumes:
      - ./pipeline.py:/app/pipeline.py
```

L'entrypoint exécute `uv pip install $EXTRA_PACKAGES` au démarrage, donc la même image sert les déploiements regex seuls et NER.

## Vérification

```bash
piighost-api --help
```

Attendu : une bannière d'aide Typer avec les sous-commandes `serve` et `dataset`.

## Suite

Continuez avec le [Démarrage rapide](quickstart.md) pour écrire un pipeline et effectuer votre première requête.
