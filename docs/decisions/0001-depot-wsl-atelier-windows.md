# ADR 0001 : dépôt dans WSL, données sur un disque Windows

- Statut : accepté
- Date : 2026-10-19

## Contexte

Le projet se développe sur une machine Windows. Tout ce qui s'exécute tourne dans une distribution Ubuntu sous WSL2 : Python et uv, les tests, Git, Docker Engine et l'accès au GPU. L'éditeur tourne sous Windows et travaille dans la distribution à distance.

Deux familles de fichiers cohabitent, avec des besoins opposés :

- le code et la documentation : peu de fichiers, mais des outils (uv, pytest, Git, Docker) qui en parcourent des milliers à chaque exécution, dépendances installées comprises ;
- les données et les poids : jeux de données publics (environ 21 Go pour The Way Up seul), vidéos, annotations, poids de modèles tiers. Des fichiers volumineux, dont certains irremplaçables : les annotations.

WSL2 voit les disques Windows sous `/mnt/<lettre>`, à travers une couche de traduction de fichiers (9P) que chaque accès traverse.

## Décision

Le dépôt vit sur le système de fichiers ext4 de la distribution, dans `~/dev/beta`.

Les données, les poids tiers et les fichiers jetables vivent hors du dépôt, sur un disque Windows, dans `V:\Projet` (`/mnt/v/Projet` vu de WSL).

Le code ne connaît l'emplacement des données que par la variable d'environnement `BETA_DATA_ROOT`, lue par la configuration. Le dépôt ne versionne que des manifestes : pour chaque jeu de données, la liste de ses fichiers avec leur empreinte.

## Alternatives écartées

- Tout sur le disque Windows, dépôt compris. Sous `/mnt`, les opérations qui touchent des milliers de fichiers (`uv sync`, `pytest`, `git status`) sont nettement plus lentes que sur ext4. Les permissions Linux n'y sont pas représentées : chaque fichier apparaît exécutable, et Git le signale comme modifié. La détection des modifications de fichiers (inotify), dont dépend le rechargement automatique du serveur de développement, n'y est pas fiable.
- Le même choix, en montant le disque avec l'option `metadata`. Cette option enregistre les permissions Linux sur le disque Windows et règle une partie du problème, mais ni la lenteur ni la détection des modifications.
- Tout dans la distribution, données comprises. Le disque virtuel de la distribution se trouve par défaut sur le disque système ; il grossit avec les données et ne rend pas la place libérée sans intervention manuelle. Les données dépendraient aussi de la vie de la distribution : une réinstallation efface son disque, annotations comprises. Elles seraient enfin moins accessibles aux outils Windows.

## Conséquences

- Les outils de développement travaillent à la vitesse d'un système Linux natif, avec des permissions et une détection des modifications qui fonctionnent normalement.
- Le dépôt reste léger : les données et les poids vivent physiquement ailleurs, et le `.gitignore` n'est qu'une seconde protection.
- Lire les données à travers `/mnt/v` reste lent. Avant un entraînement, le sous-ensemble utile est copié dans la distribution.
- Deux choses sont à sauvegarder : le dépôt, par GitHub, et le dossier de données, à part. Les annotations, seul contenu impossible à recréer, demandent une sauvegarde séparée.
- Aucun chemin vers les données n'apparaît dans le code : changer de machine revient à donner une autre valeur à `BETA_DATA_ROOT`.
- Un fichier copié du disque Windows vers le dépôt doit l'être sans ses permissions (`cp --no-preserve=mode`), sinon Git l'enregistre comme exécutable.
