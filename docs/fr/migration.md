---
icon: lucide/arrow-right-left
---

# Migrer des fichiers Python vers TOML

`piighost-api` n'accepte plus de chemin d'import Python au format `module:variable` pour la configuration du pipeline. Le nouveau format est un fichier TOML déclaratif consommé par `piighost.config.load_pipeline`. Cette page présente l'équivalent TOML des pipelines Python courants.

---

## Détecteur regex unique

Avant (`pipeline.py`) :

```python
from piighost.detector import RegexDetector
# ... autres imports ...

detector = RegexDetector(patterns={"EMAIL": r"[a-z]+@[a-z]+\.[a-z]+"})
# ... assemblage du reste du pipeline ...
```

Lancement : `piighost-api serve pipeline:pipeline`

Après (`pipeline.toml`) :

```toml
[[detectors]]
type = "regex"

[detectors.patterns]
EMAIL = "[a-z]+@[a-z]+\\.[a-z]+"
```

Lancement : `piighost-api serve --config pipeline.toml`

---

## GLiNER2 + regex composite

Avant :

```python
model = GLiNER2.from_pretrained("fastino/gliner2-multi-v1")
gliner = Gliner2Detector(model=model, threshold=0.5, labels=["person", "city"])
common = RegexDetector(patterns={"EMAIL": "..."})
detector = CompositeDetector(detectors=[gliner, common])
```

Après :

```toml
[[detectors]]
type = "gliner2"
model = "fastino/gliner2-multi-v1"
threshold = 0.5
labels = ["person", "city"]

[[detectors]]
type = "regex"

[detectors.patterns]
EMAIL = "..."
```

Le `CompositeDetector` est créé implicitement quand plus d'une entrée `[[detectors]]` est déclarée.

---

## Variable d'environnement

La variable `PIPELINE_PATH` a été renommée en `PIIGHOST_CONFIG`. Définissez-la sur le chemin de votre fichier TOML, ou passez `--config <chemin>` directement au CLI.

```bash
# Avant
export PIPELINE_PATH=/app/pipeline.py

# Après
export PIIGHOST_CONFIG=/app/pipeline.toml
```

---

## Changements d'endpoint

`GET /v1/config` a été supprimé. Utilisez `GET /v1/labels` pour les informations équivalentes (et plus complètes). Voir la [référence des endpoints](reference/endpoints.md).

---

## Valider votre TOML

```bash
piighost validate pipeline.toml
```

Code de sortie 0 en cas de succès, 1 en cas d'erreur avec un message préfixé du chemin.

---

## Docker

Si vous maintenez un `docker-compose.yml` faisant référence à cette image, mettez à jour :

- `PIPELINE_PATH` → `PIIGHOST_CONFIG` (renommage de la variable d'environnement)
- `./pipeline.py:/app/pipeline.py` → `./pipeline.toml:/app/pipeline.toml` (volume)

Exemple de diff :

```yaml
# Avant
environment:
  - PIPELINE_PATH=/app/pipeline.py
volumes:
  - ./pipeline.py:/app/pipeline.py

# Après
environment:
  - PIIGHOST_CONFIG=/app/pipeline.toml
volumes:
  - ./pipeline.toml:/app/pipeline.toml
```
