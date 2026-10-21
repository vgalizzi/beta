# ADR 0003 : un broker sans persistance

- Statut : accepté
- Date : 2026-10-20

## Contexte

L'API ne fait aucun calcul lourd. Elle dépose une tâche dans une file (« traiter la vidéo 42 ») et répond immédiatement ; un worker Celery prend la tâche et l'exécute. La file vit dans un serveur à part, le broker : Valkey, fork de Redis sous licence BSD-3-Clause, qui parle le même protocole.

Valkey garde ses données en mémoire et propose deux mécanismes pour les écrire sur disque :

- RDB : un instantané complet de la mémoire, écrit périodiquement ;
- AOF : un journal de chaque écriture, rejoué au démarrage.

L'image officielle active RDB par défaut (`save 3600 1 300 100 60 10000`) et écrit l'instantané dans `/data`. Sans volume, ce répertoire appartient à la couche inscriptible du conteneur : l'instantané survit à un redémarrage du conteneur, mais disparaît dès qu'il est recréé (`docker compose down`, changement d'image). Ce comportement par défaut n'est ni une persistance, ni son absence.

Le cahier des charges impose par ailleurs une table `processing_job` en base, qui porte le statut de chaque traitement. Une tâche n'existe donc pas seulement dans la file : elle existe d'abord comme ligne dans Postgres.

## Décision

Valkey tourne sans aucune persistance : RDB et AOF sont désactivés explicitement dans la commande de démarrage du service, et aucun volume n'est monté. Le broker est un serveur purement en mémoire.

Postgres est la source de vérité. La file n'est qu'un moyen de transport : si un message est perdu, la ligne `processing_job` correspondante reste dans un état non terminé, et la tâche peut être déposée de nouveau à partir de la base.

## Alternatives écartées

- Garder le comportement par défaut de l'image. L'instantané écrit dans la couche du conteneur donne une persistance partielle et implicite : la file survit à `restart`, pas à `down`, sans que rien dans la configuration ne le dise. Il coûte aussi des écritures disque inutiles, et ajoute un mode de panne : par défaut, si un instantané échoue (disque plein, par exemple), Valkey refuse toute nouvelle écriture (`stop-writes-on-bgsave-error`), ce qui bloquerait le dépôt de tâches pour une donnée dont on n'a pas besoin.
- Un volume nommé avec RDB. La file survivrait à la recréation du conteneur, mais seulement jusqu'au dernier instantané : les tâches déposées entre deux instantanés seraient perdues de toute façon. La garantie obtenue est partielle, et elle ne dispense pas du mécanisme de reprise depuis Postgres.
- Un volume nommé avec AOF. C'est la seule option qui rend la file durable. Elle duplique dans le broker une garantie déjà fournie par Postgres, et complique l'exploitation (taille du journal, réécriture périodique, réparation après une coupure brutale) sans bénéfice pour ce projet.

## Conséquences

- Un redémarrage ou une recréation de Valkey vide la file. C'est un fonctionnement normal, pas un incident.
- Le projet doit fournir un moyen de redéposer les tâches dont le `processing_job` est resté non terminé. Sans ce mécanisme, la décision reviendrait à accepter la perte de traitements.
- Le dépôt d'une tâche suit un ordre fixe : la ligne `processing_job` est validée en base avant que le message ne parte dans la file. Dans l'ordre inverse, un worker pourrait recevoir une tâche qui n'existe pas encore en base.
- Le healthcheck du service vérifie que Valkey répond `PONG`, et pas seulement qu'il accepte les connexions : le client en ligne de commande renvoie un code de succès même quand le serveur répond par une erreur.
- Le port 6379 est publié sur `127.0.0.1` uniquement, pour qu'un worker ou des tests lancés hors de Compose puissent joindre le broker pendant le développement, sans l'exposer au réseau local. Valkey n'a pas de mot de passe dans cette configuration de développement.
- Le service redémarre automatiquement (`restart: unless-stopped`), comme Postgres.
