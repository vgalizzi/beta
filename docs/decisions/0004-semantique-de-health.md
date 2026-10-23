# ADR 0004 : sémantique de `/health`

- Statut : accepté
- Date : 2026-10-22

## Contexte

L'API dépend de trois services : Postgres, qui porte l'état de chaque traitement ; le broker, où elle dépose les tâches ; le stockage objet, qui reçoit les vidéos et les résultats. Le cahier des charges demande un endpoint `GET /health` qui vérifie les trois.

Cet endpoint a deux lecteurs, qui n'en lisent pas la même partie :

- des machines : le healthcheck Docker du service `api` aujourd'hui, un répartiteur de charge et le pipeline de déploiement plus tard (phase 4 : rollback automatique si la vérification de santé échoue). Elles ne lisent que le **code HTTP** ;
- un humain qui diagnostique une panne. Il lit le **corps** de la réponse.

La question est ce que l'endpoint renvoie quand une partie seulement des dépendances répond. Le schéma de réponse initial prévoyait trois états globaux, `ok`, `degraded` et `down`, sans fixer le code HTTP de chacun.

Aucune des trois dépendances n'est accessoire :

- sans Postgres, l'API ne peut ni créer ni lire un traitement ;
- sans broker, elle peut créer un traitement en base mais pas le faire exécuter ;
- sans stockage, elle peut encore signer une URL d'envoi, puisque la signature est un calcul local, mais l'envoi lui-même échouera chez le client, loin de la cause.

## Décision

**Le code HTTP est binaire.** `/health` renvoie 200 si les trois dépendances répondent, 503 (*Service Unavailable*) dès qu'une seule ne répond pas.

**L'état global est binaire lui aussi** : `ok` ou `down`, en correspondance stricte avec le code. L'état `degraded` est supprimé.

**Le corps détaille chaque dépendance** dans un champ dédié, `database`, `broker` et `storage`, chacun valant `ok` ou `down`. La forme est fixe et documentée par le schéma OpenAPI.

**Le corps ne contient aucun message d'erreur.** La cause d'un échec (exception, délai dépassé) est journalisée côté serveur, pas renvoyée : l'endpoint n'est pas authentifié, et un message d'exception révèle des noms d'hôtes, des ports ou des versions.

**Les vérifications sont minimales et bornées dans le temps** : `PING` au broker, `SELECT 1` à la base, `head_bucket` sur le bucket de l'application. Chacune a un délai maximal explicite, et les trois s'exécutent en parallèle, de sorte que la réponse arrive avant le délai du healthcheck Docker (3 secondes), même quand toutes les dépendances sont figées.

**Le résultat n'est pas mis en cache** : chaque appel interroge les trois services.

## Alternatives écartées

- **200 avec le détail quand une partie des dépendances répond**, en réservant le 503 à la panne totale. Les lecteurs automatiques ne lisent que le code : une API sans base serait déclarée saine, recevrait du trafic, et un déploiement qui casse l'accès au stockage passerait la vérification de santé sans rollback. Un `/health` qui masque une panne est pire qu'une absence de `/health`.
- **Pondérer les dépendances** : 503 seulement pour Postgres, 200 « dégradé » sans broker ou sans stockage. Les trois fonctions de l'API (créer un traitement, l'exécuter, en recevoir la vidéo) en exigent chacune au moins une autre que la base. Le service rendu sans broker ou sans stockage n'est pas réduit, il est faux : il accepte des demandes qu'il ne pourra pas honorer. Si des fonctions indépendantes apparaissent, la question sera rouverte.
- **Garder `degraded` avec le code 503.** L'état apporterait la nuance « partiel ou total », déjà lisible dans le détail par dépendance, au prix d'une valeur dont le code HTTP n'est pas déductible de son nom.
- **Le format du brouillon IETF *Health Check Response Format for HTTP APIs*** (`application/health+json`, états `pass`, `warn`, `fail`). Le brouillon a expiré sans devenir un standard, et son niveau `warn` réintroduit l'ambiguïté écartée ci-dessus.
- **Mettre le résultat en cache quelques secondes.** Le coût d'une vérification (trois requêtes minimales) est négligeable au rythme d'une sonde toutes les dix secondes ; un cache retarderait la détection d'une panne et d'un rétablissement.
- **Renvoyer le message d'erreur de chaque dépendance.** Plus pratique pour diagnostiquer, mais l'information est déjà dans les journaux de l'API, et l'exposer sur un endpoint public donne une cartographie de l'infrastructure.

## Conséquences

- `/health` est une vérification de *readiness* (« l'API peut faire son travail »), pas de *liveness* (« le processus tourne »). Le healthcheck Docker l'utilise tel quel. C'est acceptable sous Compose, qui ne redémarre pas un conteneur `unhealthy`. Un orchestrateur qui redémarre un conteneur sur échec de son healthcheck (phase 4) redémarrerait l'API à chaque panne de Postgres sans rien réparer : il faudra alors un second endpoint de *liveness*, qui ne vérifie aucune dépendance.
- Une panne de n'importe quelle dépendance rend l'API `unhealthy`. Les services qui attendent l'API avec `condition: service_healthy` ne démarrent pas tant que les trois dépendances ne répondent pas.
- Le schéma de réponse change : l'état global perd la valeur `degraded`, et un champ par dépendance s'ajoute. Une nouvelle dépendance vérifiée impose un nouveau champ, donc une évolution du schéma, ce qui est voulu : la forme de la réponse documente ce que l'API vérifie.
- Chaque client (Redis, moteur SQLAlchemy, client S3) doit avoir un délai maximal configuré explicitement, connexion et réponse, et les nouvelles tentatives automatiques de `boto3` doivent être limitées à une seule tentative au total. Sans cela, une dépendance figée fait dépasser le délai du healthcheck.
- Une vérification ne laisse remonter aucune exception : toute erreur d'un client se traduit en `down` pour sa dépendance et en une entrée de journal. Une exception non rattrapée produirait un 500, qui ne désigne aucune dépendance.
