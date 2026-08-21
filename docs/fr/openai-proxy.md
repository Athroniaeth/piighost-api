---
icon: lucide/link
---

# Proxy compatible OpenAI

`piighost-api` peut se comporter comme un proxy transparent compatible OpenAI sous `/openai/v1`. Pointez le `base_url` de votre client OpenAI vers le proxy, nommez le vrai upstream dans un header, et le proxy anonymise chaque requête avant qu'elle ne sorte, la relaie vers cet upstream, puis désanonymise la réponse. Le fournisseur upstream ne voit jamais que des jetons comme `<<PERSON:1>>`, jamais `Patrick`.

## Deux headers

- `X-PIIGhost-Upstream` (requis) : l'URL de base de l'endpoint compatible OpenAI vers lequel relayer, par exemple `https://api.openai.com/v1`, un endpoint Azure, un vLLM auto-hébergé, ou OpenRouter.
- `X-PIIGhost-Thread-Id` (optionnel) : épingle un thread d'anonymisation fixe à travers les requêtes. Absent, chaque requête utilise un thread éphémère oublié dès que la réponse est renvoyée.

Le header `Authorization: Bearer` de l'appelant est relayé tel quel vers l'upstream, la clé d'API de votre client OpenAI devient donc la clé de l'upstream. Le proxy n'ajoute aucune authentification propre sur `/openai`.

## Routes

| Route | Ce que fait le proxy |
|-------|---------------------|
| `POST /openai/v1/chat/completions` | anonymise les messages et les arguments d'appels d'outils, désanonymise la réponse ; streaming pris en charge |
| `POST /openai/v1/completions` | anonymise le prompt, désanonymise le texte de complétion |
| `POST /openai/v1/embeddings` | anonymise l'entrée, relaie le vecteur |
| `POST /openai/v1/moderations` | anonymise l'entrée, relaie les scores |
| `GET /openai/v1/models`, `/openai/v1/models/{id}` | relais pur, aucune PII |
| `POST /openai/v1/images/*`, `/openai/v1/audio/*` | relais pur (multipart et binaire), aucune anonymisation |

Une route inconnue sous `/openai/v1` renvoie 404 : le proxy ne relaie jamais aveuglément une route non gérée, aucune future route texte ne peut donc laisser fuiter de PII.

## Pointez votre client vers le proxy

Avec le SDK Python OpenAI, changez uniquement le `base_url` et ajoutez le header upstream :

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/openai/v1",
    api_key="sk-your-upstream-key",
    default_headers={"X-PIIGhost-Upstream": "https://api.openai.com/v1"},
)
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Email Patrick at patrick@example.com"}],
)
```

L'upstream a reçu `<<PERSON:1>>` et `<<EMAIL:1>>` ; la réponse porte `Patrick` et `patrick@example.com` restaurés.

Ou avec curl :

```bash
curl http://localhost:8000/openai/v1/chat/completions \
  -H "Authorization: Bearer sk-your-upstream-key" \
  -H "X-PIIGhost-Upstream: https://api.openai.com/v1" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "I am Patrick"}]}'
```

## Ce que fait la bibliothèque, et ce qu'elle ne fait pas

L'anonymisation vient de la bibliothèque `piighost` : le pipeline conversationnel, le décodeur de jetons en streaming, et la dé-identification à la frontière des outils. `piighost-api` câble ces pièces derrière HTTP. La bibliothèque n'est pas elle-même un proxy HTTP ; le proxy est une affaire de `piighost-api`.

## Limites connues

- Les erreurs de streaming se manifestent comme un flux cassé, pas comme un statut. Une requête en streaming s'engage sur un HTTP 200 avant l'ouverture du flux upstream, une erreur upstream en cours de flux atteint donc le client comme un flux tronqué plutôt qu'un statut d'erreur propre. Une requête non-streaming relaie fidèlement le statut de l'upstream.
- Les images et l'audio sont relayés intacts. La dé-identification multimodale est hors périmètre, et ces routes ne portent aucun texte que le proxy tokenise.
- L'API Assistants (`/v1/threads`) n'est pas proxifiée. Elle est stateful côté serveur et en cours de retrait par OpenAI, alors que Chat Completions est sans état et universelle.
