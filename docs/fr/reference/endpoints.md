---
icon: lucide/server
---

# Endpoints REST

Tous les endpoints sont montés sous la racine de l'API et acceptent des corps JSON (msgspec). Quand des clés d'API sont configurées (variables d'environnement `API_KEY_*` au démarrage), tous les endpoints sauf `GET /` et `GET /health` exigent l'en-tête configuré.

Le schéma OpenAPI / Swagger est également servi en direct sur `/schema/swagger`.

---

## `GET /`

Index. Renvoie le nom du projet, la version, et un pointeur vers la doc Swagger. Aucune authentification requise.

```bash
curl http://localhost:8000/
```

```json
{"name": "piighost-api", "version": "0.6.0", "docs": "/schema/swagger"}
```

---

## `GET /health`

Sonde de vivacité. Renvoie le statut du serveur et le nom de la classe de détecteur chargée. Aucune authentification requise.

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "detector": "CompositeDetector"}
```

---

## `GET /v1/config`

Renvoie les labels que le pipeline déclare (quand le détecteur expose `.labels`) et le nom de la classe de la fabrique de placeholders. Utile pour les clients qui souhaitent afficher le vocabulaire de labels dans une interface.

```bash
curl http://localhost:8000/v1/config
```

```json
{"labels": ["PERSON", "LOCATION", "EMAIL"], "placeholder_factory": "LabelCounterPlaceholderFactory"}
```

---

## `POST /v1/detect`

Exécute la détection du modèle seul (sans anonymisation). Renvoie les entités que le pipeline aurait remplacées. Effet de bord : peuple le cache de détection pour `(text, thread_id)` afin qu'un appel `POST /v1/anonymize` ultérieur sur le même texte ne ré-exécute pas le détecteur.

**Corps de la requête** (`DetectRequest`)

| Champ | Type | Défaut |
|---|---|---|
| `text` | string | requis |
| `thread_id` | string | `"default"` |

**Réponse** (`DetectResponse`)

| Champ | Type | Description |
|---|---|---|
| `entities` | list | Entités avec leurs détections (sans placeholders). |

```bash
curl -X POST http://localhost:8000/v1/detect \
  -H "Content-Type: application/json" \
  -d '{"text": "Email patrick@acme.com", "thread_id": "u1"}'
```

---

## `PUT /v1/detect`

Surcharge HITL du cache de détection. Remplace les détections du modèle pour `(text, thread_id)` par la liste fournie par l'utilisateur, et invalide le cache de résultat anonymise afin que le prochain `POST /v1/anonymize` ré-exécute avec les détections corrigées.

Quand l'observation est configurée, cela émet aussi une trace `piighost.hitl_correction` portant les détections du modèle et de l'humain ; voir le CLI `dataset extract` pour utiliser ces traces comme jeu d'entrainement NER.

**Corps de la requête** (`OverrideDetectRequest`)

| Champ | Type | Défaut |
|---|---|---|
| `text` | string | requis |
| `detections` | list | requis (chacune : `{text, label, start_pos, end_pos, confidence}`) |
| `thread_id` | string | `"default"` |

**Réponse** — 200 vide.

```bash
curl -X PUT http://localhost:8000/v1/detect \
  -H "Content-Type: application/json" \
  -d '{"text": "Hi Alice", "thread_id": "u1", "detections": [{"text":"Alice","label":"PERSON","start_pos":3,"end_pos":8,"confidence":1.0}]}'
```

---

## `POST /v1/anonymize`

Exécute le pipeline complet (detect → resolve spans → link → resolve entities → anonymize). Renvoie le texte anonymisé et l'arbre des entités.

**Corps de la requête** (`AnonymizeRequest`) : `{text, thread_id}` (même structure que `/v1/detect`).

**Réponse** (`AnonymizeResponse`)

| Champ | Type | Description |
|---|---|---|
| `anonymized_text` | string | Le texte avec les PII remplacées par des placeholders. |
| `entities` | list | Une entité par groupe lié : `{label, placeholder, detections}`. |

```bash
curl -X POST http://localhost:8000/v1/anonymize \
  -H "Content-Type: application/json" \
  -d '{"text": "Email patrick@acme.com", "thread_id": "u1"}'
```

```json
{
  "anonymized_text": "Email <<EMAIL:1>>",
  "entities": [{"label": "EMAIL", "placeholder": "<<EMAIL:1>>", "detections": [...]}]
}
```

---

## `POST /v1/deanonymize`

Chemin avec cache. Cherche le mapping stocké précédemment pour `(anonymised_text, thread_id)` ; renvoie le texte original. Renvoie une 404 si le mapping a expiré ou n'a jamais existé.

**Corps de la requête** (`DeanonymizeRequest`) : `{text, thread_id}`.

**Réponse** (`DeanonymizeResponse`) : `{text, entities}` (les entités utilisées pour l'appel d'anonymisation original).

```bash
curl -X POST http://localhost:8000/v1/deanonymize \
  -H "Content-Type: application/json" \
  -d '{"text": "Email <<EMAIL:1>>", "thread_id": "u1"}'
```

---

## `POST /v1/deanonymize/entities`

Chemin de remplacement par token. Remplace chaque token connu dans *text* par sa valeur originale, en une seule passe regex, en utilisant la mémoire d'entités accumulée du thread. Fonctionne sur du texte que le pipeline n'a jamais anonymisé (par exemple une réponse générée par un LLM qui contient des placeholders), contrairement au chemin avec cache ci-dessus.

**Corps de la requête** (`DeanonymizeRequest`) : `{text, thread_id}`.

**Réponse** (`DeanonymizeEntResponse`) : `{text}`.

```bash
curl -X POST http://localhost:8000/v1/deanonymize/entities \
  -H "Content-Type: application/json" \
  -d '{"text": "Hi <<PERSON:1>>!", "thread_id": "u1"}'
```

---

## Authentification

Quand les variables d'environnement `API_KEY_<NAME>=<key>` sont définies au démarrage, chaque endpoint protégé exige la clé correspondante dans un en-tête `Authorization`. Voir [keyshield](https://github.com/Athroniaeth/keyshield) pour le détail des scopes, de la rotation et du hash Argon2.

Quand aucune clé d'API n'est configurée, l'authentification est désactivée (le serveur logge `auth disabled` au démarrage).
