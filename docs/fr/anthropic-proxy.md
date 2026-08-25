---
icon: lucide/link
---

# Utiliser Claude Code via le proxy

Le proxy Anthropic permet à Claude Code de communiquer avec un upstream compatible Anthropic pendant que piighost dé-identifie chaque requête et restaure chaque réponse. Claude Code n'envoie jamais les vraies données personnelles au modèle.

## Deux headers

- `X-PIIGhost-Upstream` (optionnel) : surcharge l'URL de base de l'upstream par requête, par exemple `https://api.anthropic.com/v1` ou une passerelle compatible. Absent, le serveur utilise `PIIGHOST_ANTHROPIC_UPSTREAM` (par défaut `https://api.anthropic.com/v1`).
- `X-PIIGhost-Thread-Id` (optionnel) : épingle un thread d'anonymisation fixe à travers les requêtes. Absent, chaque requête utilise un thread éphémère oublié dès que la réponse est renvoyée.

Le header `x-api-key` ou `Authorization: Bearer` de l'appelant est relayé tel quel vers l'upstream. Le proxy n'ajoute aucune authentification propre sur `/anthropic`.

## Routes

| Route | Ce que fait le proxy |
|-------|---------------------|
| `POST /anthropic/v1/messages` | anonymise le système, les messages et les entrées/sorties d'outils, désanonymise la réponse, streaming pris en charge |
| `POST /anthropic/v1/messages/count_tokens` | anonymise la requête, relaie le nombre de jetons |

Une route inconnue sous `/anthropic/v1` renvoie 404.

## Pointez Claude Code vers le proxy

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000/anthropic
export ANTHROPIC_API_KEY=sk-ant-...
claude
```

Claude Code lit `ANTHROPIC_BASE_URL` automatiquement et route chaque appel via le proxy.

## La démonstration classique

1. Démarrez l'API avec un pipeline dont le détecteur reconnaît votre prénom.
   La configuration reproductible la plus simple est un `ExactMatchDetector`,
   décrit dans un fichier TOML :

       [detector]
       type = "exact"

       [detector.values]
       Patrick = "PERSON"

   Lancez le serveur avec cette configuration sur un port local, par exemple 8080.

2. Pointez Claude Code vers le proxy et fournissez une vraie clé :

       export ANTHROPIC_BASE_URL=http://localhost:8080/anthropic
       export ANTHROPIC_API_KEY=sk-ant-...
       claude

3. Dans Claude Code, demandez la première lettre de ce prénom, par exemple
   "Quelle est la première lettre de mon prénom, Patrick ?".

4. Observez deux choses. Claude ne peut pas donner la lettre, car il n'a vu que
   `<<PERSON:1>>`, jamais `Patrick`. La transcription que vous lisez est restaurée,
   vous voyez donc toujours `Patrick`. Le modèle était aveugle aux données
   personnelles du début à la fin.

## Notes

- L'upstream par défaut est `https://api.anthropic.com/v1`. Surchargez-le par
  requête avec un header `X-PIIGhost-Upstream`, ou globalement avec la variable
  d'environnement `PIIGHOST_ANTHROPIC_UPSTREAM`, pour cibler une passerelle.
- Tous les headers de l'appelant sont relayés vers l'upstream, sauf les headers
  hop-by-hop, `host`, `content-length`, `accept-encoding` et les headers de
  contrôle `X-PIIGhost-*`. Cela préserve le user-agent et les flags beta de
  l'appelant pour les upstreams qui les valident.
- Une courte consigne est préfixée au prompt système pour que le modèle manie
  les jetons de substitution avec fluidité : les réutiliser verbatim et ne pas
  signaler qu'une valeur est masquée. Personnalisez-la avec
  `PIIGHOST_ANTHROPIC_PLACEHOLDER_NOTE`, ou désactivez-la en mettant cette
  variable à une chaîne vide.
- Chaque requête est un thread éphémère, ce qui correspond au comportement de
  Claude Code qui renvoie tout l'historique à chaque tour. Passez
  `X-PIIGhost-Thread-ID` uniquement si vous gérez vous-même un thread persistant.

## Mode abonnement (OAuth)

L'approche par URL de base ci-dessus vise l'usage par clé d'API ou passerelle. Un
abonnement Pro ou Max s'authentifie en OAuth, et Claude Code code en dur le vrai
`api.anthropic.com` pour ses appels OAuth et de rafraîchissement. Définir
`ANTHROPIC_BASE_URL` ne fait donc pas passer le trafic d'abonnement par le proxy,
cela vous sort du mode abonnement ou échoue. Anthropic valide aussi l'empreinte du
client des requêtes OAuth, donc un proxy qui modifie les requêtes n'est pas un
chemin pris en charge pour un abonnement.

Deux réglages aident un abonnement, ou une passerelle sensible à l'empreinte, et
le premier est le défaut car un harness de code est la cible principale :

- `PIIGHOST_ANTHROPIC_ANONYMIZE_SYSTEM` (défaut `false`) : par défaut le prompt
  système est relayé intact afin que l'upstream puisse encore valider le client.
  Mettez-le à `true` pour anonymiser aussi le prompt système. Le contenu des
  messages et des outils reste anonymisé dans tous les cas. Si votre prompt
  système peut contenir des données personnelles, mettez-le à `true`.
- Le forwarding permissif des headers (toujours actif sur `/anthropic`) conserve
  le user-agent et les flags beta OAuth de l'appelant, que l'upstream peut exiger.

## Limites connues

- Les erreurs de streaming se manifestent comme un flux cassé, pas comme un statut. Une requête en streaming s'engage sur un HTTP 200 avant l'ouverture du flux upstream, une erreur upstream en cours de flux atteint donc le client comme un flux tronqué plutôt qu'un statut d'erreur propre. Une requête non-streaming relaie fidèlement le statut de l'upstream.
- Les images et documents sont relayés intacts. La dé-identification multimodale est hors périmètre.
- Les définitions d'outils (`tools[]`) sont transmises intactes : le proxy anonymise uniquement le contenu qui transite par le modèle, pas le schéma que vous définissez.
