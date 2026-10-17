# Cahier des charges — Beta

Document de référence pour l'exécution. La description générale, les justifications de stack et le périmètre sont dans le `README.md`, en anglais.

**Règle de fonctionnement** : une phase n'est pas commencée tant que la précédente n'a pas coché tous ses critères d'acceptation. Pas de « je finirai les tests plus tard ». C'est la seule protection contre la dérive de périmètre sur un projet solo.

---

## Conventions transverses

Ces règles s'appliquent à toutes les phases. Elles sont vérifiées par la CI, pas par la bonne volonté.

### Qualité de code

- `ruff check` et `ruff format` sans erreur
- `mypy --strict` sans erreur sur `src/`, tolérance assouplie sur `tests/`
- Pas de `Any` non justifié, pas de `# type: ignore` sans commentaire expliquant pourquoi
- Pydantic v2 à toutes les frontières : entrées HTTP, sorties HTTP, configuration, payloads de tâches Celery
- Configuration par `pydantic-settings`, jamais de `os.environ` dispersé, jamais de secret versionné, un `.env.example` à jour

### Base de données

- Toute évolution de schéma passe par une migration Alembic. Jamais de `Base.metadata.create_all()` en dehors des fixtures de test.
- Chaque migration est testée dans les deux sens : `upgrade` puis `downgrade`.
- Pas de requête SQL brute sans raison écrite en commentaire.

### Tests

- Tests d'intégration sur **vrai** Postgres et **vrai** stockage objet via `testcontainers`. Mocker la base de données est interdit : ça teste le mock, pas le système.
- Les appels réseau externes (LLM, téléchargement de poids) sont les seuls à être mockés.
- Seuil de couverture sur le code métier fixé en phase 0 et jamais abaissé. La CI échoue si la couverture baisse.
- Chaque bug corrigé arrive avec le test qui l'aurait attrapé.

### CI

Sur chaque push et chaque pull request : lint → typage → tests unitaires → tests d'intégration. Un job rouge bloque la fusion. Branche `main` protégée, même en travaillant seul : tout changement passe par une pull request.

### Définition de « terminé »

Une phase est terminée quand :

1. Tous les critères d'acceptation de la phase sont vérifiables par une commande ou une capture
2. La CI est verte sur `main`
3. Le `README.md` permet à quelqu'un d'autre de lancer le projet sans poser de question
4. Un `docs/decisions/` contient une note courte par décision structurante prise pendant la phase (format ADR léger : contexte, décision, alternatives écartées)

Le point 4 garde la trace du raisonnement : six mois plus tard, on ne se souvient plus pourquoi on a choisi Celery plutôt qu'arq.

### Estimations

Les charges sont en **jours-homme pleins** (une journée = environ 6 h de travail effectif). Le calendrier dépend du temps hebdomadaire disponible.

---

# Phase 0 — Plomberie

**Charge estimée : 8 à 12 j·h**

## Objectif

Construire le squelette complet de l'application sans une seule ligne de machine learning. À la fin de la phase, une vidéo peut être uploadée, stockée, enregistrée en base, et traitée par un worker qui ne fait rien d'utile mais qui le fait proprement.

C'est la phase la moins gratifiante et la plus importante. Toutes les suivantes sont des ajouts sur cette fondation ; si elle est bancale, chaque phase suivante devient une refonte.

## Périmètre

### Schéma de base initial

Tables à créer : `user`, `gym`, `gym_color_scale`, `wall`, `route`, `attempt`, `processing_job`.

`gym_color_scale` dès maintenant, parce qu'elle change la forme de `route` : chez Arkose la couleur du bloc **est** son niveau, et l'échelle est propre à chaque réseau de salles. Donc pas de champ `cotation` sur `route`, mais une clé vers l'échelle de la salle.

| Colonne | Type | Rôle |
|---|---|---|
| `gym_id` | fk | |
| `color` | text | Nom de la couleur dans la salle : `jaune`, `vert`, `bleu`, `rouge`, `noir`, `violet`, parfois `blanc` |
| `rank` | int | Ordre de difficulté croissant. C'est lui qu'on trie et qu'on filtre, pas le nom |
| `lab_reference` | `float8[3]` | Couleur de référence en espace Lab. Sert d'ancre au classifieur de couleur de la phase 1 |
| `font_equivalent` | text nullable | Équivalence Fontainebleau, **officieuse** : Arkose ne la publie pas. Indicative, jamais affichée comme une cotation officielle |

Seed initial à remplir avec l'échelle Arkose, mesurée sur place plutôt que reprise d'un nuancier web.

Deux colonnes d'`attempt` sont posées maintenant et ne seront remplies qu'en phase 2, pour la même raison que `processing_job` : les ajouter plus tard coûte une migration et un backfill, les poser vides coûte zéro.

| Colonne | Type | Rôle |
|---|---|---|
| `homography` | `jsonb` ou `float8[]` (3×3) | Transformation topo canonique → repère image de la vidéo. Une seule matrice par essai tant que la caméra est fixe (README, section *Hold detection*) |
| `background_key` | text nullable | Chemin sur le stockage objet de l'image de fond reconstruite par médiane temporelle |

`processing_job` dès maintenant, avec les colonnes qui ne serviront qu'en phase 1 :

| Colonne | Type | Rôle |
|---|---|---|
| `id` | uuid | |
| `attempt_id` | fk | |
| `status` | enum | `pending`, `running`, `succeeded`, `failed` |
| `stage` | text nullable | étape courante, pour l'affichage de progression |
| `model_versions` | jsonb | `{"hold_detector": "v3", "pose": "rtmpose-m"}`, vide en phase 0 |
| `artifacts` | jsonb | chemins des sorties sur le stockage objet |
| `started_at`, `finished_at` | timestamptz | |
| `error` | text nullable | trace en cas d'échec |

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/attempts` | Crée un essai, renvoie une URL pré-signée d'upload + l'id |
| `POST` | `/attempts/{id}/complete` | Signale la fin de l'upload, vérifie l'objet, empile la tâche |
| `GET` | `/attempts/{id}` | État de l'essai et de son job |
| `GET` | `/attempts` | Liste paginée, filtrable par bloc et par date |
| `POST` | `/gyms`, `/walls`, `/routes` | CRUD minimal. `/gyms` porte aussi l'échelle de couleurs |
| `GET` | `/health` | Vérifie base + broker + stockage objet |
| `GET` | `/metrics` | Format Prometheus, même si personne ne scrape encore |

### Authentification

Minimale : un en-tête d'API key qui résout vers un `user`. Suffisant pour poser les clés étrangères. L'auth sérieuse est en phase 3.

### Worker

Une tâche Celery `process_attempt(attempt_id)` qui : passe le job en `running`, télécharge la vidéo, extrait la durée, la résolution et le nombre d'images avec `ffprobe`, écrit une vignette sur le stockage objet, passe le job en `succeeded`. Gestion explicite de l'échec et des retries.

### Infrastructure locale

`docker-compose.yml` avec `api`, `worker`, `postgres`, le broker, le stockage objet, et un conteneur d'initialisation qui crée le bucket. Healthchecks sur tous les services. Dockerfile multi-stage, image finale sans outils de build, utilisateur non-root, cibles de build distinctes pour l'API et le worker.

**Deux briques demandent un ADR chacune.** L'édition communautaire de MinIO a été archivée le 25 avril 2026 : candidat de remplacement SeaweedFS (Apache 2.0). Redis est passé en tri-licence AGPL avec la 8.0 : Valkey, son fork BSD sous Linux Foundation, est protocole-compatible. Dans les deux cas, la licence d'un service lancé en conteneur ne contamine pas le code qui lui parle en HTTP : l'argument à retenir est la maintenance, pas le copyleft.

## Critères d'acceptation

- [ ] Sur une machine vierge : `git clone`, `cp .env.example .env`, `docker compose up` → l'API répond sur `/health` avec tous les services verts
- [ ] Un aller-retour d'URL pré-signée est vérifié **avant** de construire quoi que ce soit dessus : c'est la fonctionnalité où les serveurs S3-compatibles divergent le plus du vrai S3
- [ ] `alembic upgrade head` puis `alembic downgrade base` s'exécutent sans erreur
- [ ] Un test d'intégration fait le parcours complet : création d'essai → upload sur le stockage objet via l'URL pré-signée → `complete` → exécution de la tâche → job en `succeeded` → vignette présente sur le stockage
- [ ] `mypy --strict` et `ruff` passent
- [ ] La CI est verte et bloque la fusion quand un test échoue
- [ ] Un job en échec laisse `status = failed` et une trace exploitable dans `error`, sans planter le worker

## Pièges à éviter

- Commencer par le ML « pour voir si ça marche » avant que la plomberie tienne : c'est le moyen le plus sûr de bloquer le projet à mi-chemin.
- Faire transiter la vidéo par l'API. Le pré-signé est le point technique de la phase, ne pas le contourner par facilité.
- Signer une URL avec l'endpoint interne du stockage et l'utiliser depuis le navigateur. Le nom d'hôte fait partie de la signature : deux variables de configuration distinctes dès le départ, une interne et une publique.
- Session SQLAlchemy async mal gérée entre l'API et le worker. Le worker est synchrone, il lui faut sa propre configuration de session. À régler maintenant, pas en phase 2.

---

# Phase 1 — Détection des prises et cycle de vie du modèle

**Charge estimée : 10 à 15 j·h, dont 1 à 2 j·h d'annotation**

> Le pré-entraînement sur données publiques limite l'annotation maison à 50-100 images.

## Objectif

Photographier un pan, obtenir une topo exploitable, et faire circuler un modèle entraîné depuis MLflow jusqu'au worker de production.

Livrable visuel : une image de mur avec chaque prise entourée et classée par couleur.

## Périmètre

### Décision prise

**Architecture de détection : RF-DETR en segmentation d'instances, variantes Apache 2.0 uniquement.** Ultralytics (AGPL-3.0) est écarté : importé dans le code, il étendrait l'AGPL à tout le dépôt. Piège à ne pas rater : dans le même dépôt RF-DETR, les variantes **XL et 2XL et le paquet `rfdetr_plus` sont sous PML 1.0**, pas Apache. Replis si l'entraînement résiste : RTMDet (MIT), Mask R-CNN `torchvision` (BSD).

**Segmentation plutôt que boîte**, parce que le clustering couleur est un critère d'acceptation de cette phase et qu'une bbox laisse entrer le fond du mur dans l'histogramme.

ADR à écrire quand même : la décision est prise, la trace de *pourquoi* ne l'est pas.

### Dataset

Stratégie en deux temps.

1. **Pré-entraînement sur données publiques.** Plus de 10 000 images de prises annotées sur Roboflow Universe, dont ~4 300 en CC BY 4.0 (Rock-Climbing-Hold-Detection, 2 classes prise / volume). Qualité hétérogène et conventions de classes incompatibles entre jeux : prévoir un script de remapping, versionné comme le reste.
2. **Fine-tune sur 50 à 100 images de mes murs.** C'est là que le temps d'annotation se justifie.
3. **Pré-annotation par model-in-the-loop** : le détecteur du point 1 propose, je corrige, je réentraîne. Chaque tour est un run MLflow avec son hash de dataset. SAM 3 irait plus vite, mais sa licence est une licence Meta maison, pas Apache : si je l'utilise, c'est écrit dans l'ADR.
4. **Couleur** : le seul jeu public annotant la couleur des prises est *Bouldering holds* (Pwals, segmentation, 16 classes de couleur, CC BY 4.0). Petit, et ses classes ne sont pas celles d'Arkose — utile pour amorcer, pas pour valider. La vérité terrain qui compte sera maison : photographier une prise de chaque couleur de l'échelle Arkose sous trois éclairages différents, ça prend vingt minutes et ça donne les ancres Lab **et** le jeu de validation.
5. Versionner le dataset (a minima un manifeste JSON avec hash des images et date, DVC si l'envie vient).
6. Split train/val/test **par mur**, pas aléatoire. Un split aléatoire mettrait des crops du même mur des deux côtés et donnerait des scores flattés qui ne veulent rien dire. Le pré-entraînement public compte comme un mur à part entière : aucune de ses images ne doit atterrir dans le test.

### Entraînement

- Toute exécution loggée dans MLflow : hyperparamètres, métriques (mAP@50, mAP@50-95, rappel par classe), échantillons d'images de validation, hash du dataset
- Modèle promu dans le registry avec un alias `champion`
- Le worker charge le modèle **par alias depuis le registry**, jamais par chemin de fichier en dur.
- La version de modèle utilisée est écrite dans `processing_job.model_versions` à chaque traitement

### Traitement de mur

Nouvelle tâche `detect_holds(wall_id)` : téléchargement de la photo, inférence, filtrage par confiance, suppression des doublons, extraction de la couleur dominante par masque, **classification dans l'échelle de la salle**, écriture en base des `hold`, génération d'une image de topo annotée sur le stockage objet.

Deux étapes distinctes, à ne pas confondre :

**Classification de couleur.** Distance en espace Lab entre la couleur dominante du masque et les ancres de `gym_color_scale`, avec une classe de rejet pour les prises hors palette — volumes, prises structurelles beiges ou grises utilisables par tous les blocs, et prises trop délavées pour trancher. Six ou sept classes connues, pas un clustering ouvert : ça s'évalue avec une matrice de confusion.

**Proposition de bloc.** Chez Arkose, plusieurs blocs de la même couleur cohabitent sur un pan, donc la couleur seule ne sépare rien. Regroupement spatial des prises d'une même classe (DBSCAN sur les positions), qui produit des **candidats**. L'interface les affiche, l'utilisateur valide ou corrige. Ne jamais appeler ça une détection de bloc dans le code, dans l'API, ni dans l'interface : le nommage induit en erreur et finit par induire le développeur en erreur aussi.

Cette tâche produit la **topo canonique** du mur, celle qui porte le tracé des blocs. Elle est distincte de l'image de fond reconstruite depuis chaque vidéo, qui arrive en phase 2 et qui sert à l'association membre ↔ prise (README, section *Hold detection*). Le même détecteur tourne sur les deux : une seule brique, deux points d'entrée.

### Interface minimale

Une page qui affiche la photo du mur avec les prises superposées, colorées selon leur classe, et permet de sélectionner des prises pour définir un bloc (`route_hold`). Les candidats issus du regroupement spatial sont proposés en un clic, et restent entièrement modifiables : ajouter une prise oubliée, en retirer une qui appartient au bloc voisin de même couleur. C'est le parcours nominal, pas un mode dégradé : l'interface est dimensionnée en conséquence.

Pas de framework lourd nécessaire : HTML + un peu de JS suffit, le sujet du projet n'est pas le front.

## Critères d'acceptation

- [ ] Un modèle entraîné est visible dans MLflow avec ses métriques et son alias
- [ ] Le worker charge le modèle par alias, et changer l'alias dans MLflow change le comportement sans redéploiement
- [ ] `POST /walls/{id}/detect-holds` produit des `hold` en base et une image de topo consultable
- [ ] mAP@50 sur le test split documenté dans le README, quelle que soit sa valeur, avec les cas d'échec illustrés
- [ ] Deux chiffres publiés, pas un : mAP après pré-entraînement public seul, et mAP après fine-tune sur mes murs. L'écart entre les deux est la mesure de ce que le fine-tune apporte, et c'est la partie intéressante.
- [ ] La classification de couleur atteint au moins 90 % d'exactitude sur les prises de la palette Arkose, matrice de confusion publiée. Une confusion systématique entre deux couleurs voisines est une information utile, pas un échec à masquer
- [ ] Les prises hors palette (volumes, prises structurelles) tombent dans la classe de rejet plutôt que d'être forcées dans une couleur
- [ ] Le regroupement spatial propose un candidat correct ou quasi correct sur au moins 70 % des blocs testés, mesuré à la main sur une dizaine de blocs. Le reste se corrige à la main, et le temps médian de correction est mesuré : c'est lui qui dit si l'interface est utilisable
- [ ] L'échelle de couleurs d'une salle est modifiable sans redéploiement, et une seconde salle avec une autre échelle peut être ajoutée sans toucher au code
- [ ] Test d'intégration qui mocke uniquement le chargement des poids et valide toute la chaîne

## Risques

La classification de couleur va casser sur les prises bicolores, sur les murs à éclairage variable, et sur les prises délavées par l'usage — un rouge de deux ans tire vers l'orange. Les ancres Lab mesurées sur place plutôt que reprises d'un nuancier limitent le problème sans le supprimer. L'éclairage se traite par une normalisation simple : une carte de blanc, ou une constance de couleur type Gray World avant la classification.

Le vrai risque n'est pas là. **Il est que le regroupement spatial soit pris pour une détection de bloc.** Sur un pan chargé, deux blocs bleus s'entremêlent et aucun signal automatique ne les sépare de façon fiable. C'est une propriété du système Arkose, pas un défaut à corriger. La mitigation est un choix de conception, pas un correctif : la correction manuelle est le parcours nominal et l'interface est dimensionnée pour ça. Le dire clairement vaut mieux que promettre un automatisme puis expliquer pourquoi il rate.

Point à vérifier sur place avant d'écrire le code : le marquage des prises de départ chez Arkose. S'il existe et qu'il est détectable, c'est le signal qui désambiguïse deux blocs de même couleur, et ça change la stratégie de regroupement.

---

# Phase 2 — Pose, mouvements et métriques

**Charge estimée : 15 à 20 j·h**

## Objectif

Le cœur du projet et la phase où l'expertise personnelle apporte le plus. Extraire la pose du grimpeur, associer les membres aux prises, produire la séquence de mouvements et les métriques, générer la vidéo annotée.

## Périmètre

### Image de fond et prises de l'essai

Première étape, et elle conditionne tout le reste.

1. **Médiane temporelle** sur N images réparties sur la durée de l'essai : le grimpeur bouge, le mur non, la médiane par pixel efface le premier et garde le second. Écriture de l'image reconstruite sur le stockage objet, clé dans `attempt.background_key`.
2. Passe de raffinement pour le cas dégénéré — grimpeur immobile sur les mêmes pixels plus de la moitié de la vidéo, typiquement en position de départ : médiane des pixels non couverts par sa boîte détectée.
3. Détection des prises sur cette image de fond avec le modèle de la phase 1, chargé par alias depuis MLflow.
4. **Homographie** topo canonique ↔ repère image, par RANSAC sur les centres de prises appariés. Écriture dans `attempt.homography`, appariement des détections aux `hold` du mur.

Conséquence à garder en tête pendant l'implémentation : l'association de l'étape suivante travaille **en coordonnées image**, sans recalage. L'homographie ne sert qu'à relier l'essai à la topo canonique, donc aux autres essais. Une erreur d'homographie dégrade la comparaison entre essais, pas la détection des contacts.

**Garde-fou caméra fixe.** Estimer l'homographie entre la première et la dernière image de la vidéo : si la dérive dépasse un seuil, la caméra a bougé, l'essai est rejeté avec un message explicite. Mieux vaut refuser un essai que sortir une trajectoire de centre de masse qui mesure le tremblement du cameraman.

### Suivi et pose

- Détection du grimpeur image par image, suivi par **`roboflow/trackers`** (ByteTrack, Apache 2.0) pour gérer les images où la détection échoue. Écrire un IoU maison reste possible, mais autant garder le temps pour l'association.
- Estimation de pose top-down sur la boîte suivie, servie par **`rtmlib`** (Apache 2.0, ONNX Runtime, sans `mmcv` ni `mmpose` — l'image Docker du worker reste raisonnable).
- **Benchmarker deux variantes sur *The Way Up* avant de choisir** : RTMPose-m (rapide) et ViTPose / ViTPose+ (précis, c'est la meilleure baseline du papier). Demi-journée, et ça produit un tableau dans le README.
- **Tester RTMW / RTMPose-133** (whole-body, mains et pieds) : les modèles 17 keypoints sortent le poignet et la cheville, alors que le contact se fait aux doigts et aux orteils. C'est un biais systématique, et aucune des baselines publiées ne le corrige. Piste la plus crédible pour les dépasser.
- Lissage temporel : filtre de Kalman ou moyenne glissante pondérée par la confiance
- Écriture des keypoints en Parquet sur le stockage objet, chemin en base
- Métrique de qualité calculée et stockée : proportion de keypoints sous le seuil de confiance

### Association membres ↔ prises

1. Distance keypoint d'extrémité ↔ masque de prise, image par image, dans le repère image de la vidéo
2. **Zone d'intérêt autour du keypoint paramétrée par membre et par modèle** : un modèle qui sort l'orteil n'a pas besoin de la même tolérance qu'un modèle qui sort la cheville
3. Contact déclaré au-delà de N images consécutives sous le seuil, hystérésis à la sortie. **Valeur de référence : 0,5 s**, soit 12,5 images à 25 fps
4. Écriture de la table `move` : `(index, membre, hold_id, t_début, t_fin)`

Les seuils sont des paramètres de configuration, pas des constantes enfouies dans le code.

### Évaluation

Deux temps, dans cet ordre : valider d'abord la méthode sur un benchmark public, mesurer ensuite son transfert sur mes murs.

**1. Benchmark public — *The Way Up*.** Jeu de données CC BY 4.0 (CVPR 2025 W CVSPORTS, Zenodo `10.5281/zenodo.15196867`, 20,9 Go) : 22 vidéos, 940 usages de prises annotés avec membre, frames de début et de fin, occlusions, boîtes de prises et matrices d'homographie. Vérité terrain gratuite, protocole publié, baselines publiées.

- Métrique : **tIoU temporel aux seuils 0,0 et 0,5**, plus accuracy / precision / sensitivity
- Cibles à tIoU > 0 : 86,6 % (ViTPose-L), 83,5 % (MediaPipe Heavy), 75,3 % (YOLOv8-pose X). À tIoU > 0,5, entre 62 et 69 %
- Les matrices d'homographie fournies permettent de **chiffrer l'erreur** de ma propre estimation au lieu de la supposer bonne

Limite à rappeler avec les résultats : c'est de la voie sur mur vertical, pas du bloc. Postures moins extrêmes qu'en dévers, occlusions moindres. Un score là-dessus est un plancher, pas une garantie.

**2. Vérité terrain maison.** Annoter à la main la séquence de mouvements de **10 essais** sur mes murs, et mesurer les mêmes métriques. Ce qui est évalué ici n'est plus la validité de la méthode, mais le **transfert** : mon matériel, mes murs, du dévers. Résultats dans le README, y compris s'ils sont mauvais : c'est l'écart avec le benchmark public qui est intéressant.

### Métriques

Calculées et écrites dans `attempt_metrics` : durée, nombre de mouvements, cadence, temps de contact par prise, répétitions, trajectoire du centre de masse, longueur de trajectoire rapportée à la hauteur, jerk normalisé, ratio statique/mouvement.

Sur le centre de masse, comparer deux estimateurs avant de trancher : la pondération anthropométrique par segments, et l'approximation par moyenne de quatre keypoints du tronc. Si l'écart est négligeable sur mes essais, la version simple gagne et la complexité part au backlog.

### Vidéo annotée

Rendu avec OpenCV ou `ffmpeg` : squelette superposé, prises en contact surlignées, compteur de mouvements, trace du centre de masse. Encodage H.264, upload sur le stockage objet, URL pré-signée de lecture.

C'est le premier livrable qu'on regarde : il mérite le temps nécessaire pour être propre.

### Page de résultat

Vidéo annotée, timeline des mouvements, fiche de métriques, comparaison avec les autres essais du même bloc.

## Critères d'acceptation

- [ ] Un essai uploadé produit une vidéo annotée consultable et une séquence de mouvements en base
- [ ] **Résultats sur *The Way Up* publiés dans le README**, en tIoU @ 0,0 et @ 0,5, à côté des trois baselines du papier
- [ ] Précision, rappel et tIoU de la détection de contacts mesurés sur 10 essais maison annotés et publiés, avec l'écart au benchmark public commenté
- [ ] L'erreur de recalage de l'homographie est chiffrée contre les matrices de référence fournies
- [ ] Le traitement d'une vidéo de 30 s tient sous une durée cible fixée et documentée. Ordre de grandeur publié pour un pipeline comparable : 4 min pour 30 s de vidéo
- [ ] Un essai dont la pose est de mauvaise qualité est signalé comme tel plutôt que de produire des métriques fantaisistes
- [ ] Un essai filmé caméra mobile est rejeté avec un message explicite, pas traité silencieusement
- [ ] L'image de fond reconstruite est consultable, pour pouvoir diagnostiquer une détection de prises ratée
- [ ] Les seuils sont configurables sans modification de code

## Point d'arrêt acceptable

Fin de phase 2, le projet est complet et utilisable. Les phases suivantes étendent le système sans être nécessaires à son usage principal.

---

# Phase 3 — Similarité et agent coach

**Charge estimée : 8 à 12 j·h**

## Objectif

Ajouter une base vectorielle et un agent LLM outillé, avec observabilité et évaluation.

## Périmètre

### Embeddings et Qdrant

- **V1 pragmatique** : vecteur de caractéristiques construit à la main (statistiques de mouvements + trajectoire du CoM rééchantillonnée à taille fixe + profil de temps de contact), normalisé.
- Évolution possible : petit encodeur temporel entraîné en auto-supervision. À ne faire que si la V1 donne des voisins manifestement absurdes. La littérature sur les embeddings contrastifs spatio-temporels de squelettes (type DeepHuMS) confirme plutôt le choix de la V1 : ces approches demandent un volume que quelques centaines d'essais ne fourniront pas. À citer nommément dans l'ADR, comme alternative écartée faute de volume.
- Collection Qdrant avec payload filtrable : utilisateur, bloc, **couleur et rang de difficulté**, résultat. Le niveau vient de la couleur des prises, donc il est renseigné sur tous les essais sans saisie manuelle : c'est un filtre fiable, pas déclaratif.
- Endpoints : `GET /attempts/{id}/similar`, `GET /routes/{id}/similar`.
- Usage réel visé : « montre-moi mes essais où j'ai eu le même schéma d'échec », « des blocs au profil de mouvement proche de celui-ci ».

Réserve à écrire dans l'ADR : à ce volume de données, une recherche exhaustive en NumPy ferait le travail. Qdrant est un choix d'apprentissage assumé.

### Agent coach — LangGraph

Graphe explicite, pas une chaîne floue :

```
        ┌─────────────────┐
        │ Lecture métriques│
        └────────┬─────────┘
                 ▼
     ┌───────────────────────┐
     │ Récupération contexte │  essais passés sur le même bloc,
     │  (SQL + Qdrant)       │  essais similaires réussis
     └───────────┬───────────┘
                 ▼
     ┌───────────────────────┐
     │  RAG corpus technique │  ressources d'entraînement escalade
     └───────────┬───────────┘
                 ▼
     ┌───────────────────────┐
     │ Rédaction du retour   │
     └───────────┬───────────┘
                 ▼
     ┌───────────────────────┐
     │ Garde-fous            │  pas de conseil médical, pas
     └───────────────────────┘  d'affirmation hors des métriques
```

Contrainte forte sur le prompt : l'agent n'a le droit d'affirmer que ce qui figure dans les métriques fournies. Interdiction de commenter la « technique » de façon générique. Un retour vague est pire qu'une absence de retour.

La couleur donne une comparaison naturelle et gratuite : « tes essais sur les bleus », « ce mouvement te coûte plus cher que sur tes autres rouges ». L'agent doit s'en servir comme axe de regroupement, et jamais comme prétexte à un discours sur la progression : il ne voit qu'une poignée d'essais.

### Langfuse

Traçage de chaque exécution, coût par appel, latence par nœud du graphe. Mise en place d'un jeu d'évaluation : 20 essais de référence, notation de la qualité des retours (par soi-même ou par LLM-as-judge), suivi de la note dans le temps quand le prompt change.

### Authentification réelle

Passage de l'API key à du JWT avec mots de passe hachés (argon2), refresh tokens, isolation stricte des données par utilisateur. Test d'intégration qui vérifie qu'un utilisateur ne peut pas lire l'essai d'un autre.

## Critères d'acceptation

- [ ] `GET /attempts/{id}/similar` renvoie des voisins qualitativement pertinents, vérifié à la main sur 10 requêtes
- [ ] L'agent produit un retour ancré dans les métriques, vérifié sur le jeu d'évaluation
- [ ] Toutes les exécutions sont visibles dans Langfuse avec coût et latence
- [ ] Une note de qualité est suivie dans le temps et la variation entre deux versions de prompt est mesurable
- [ ] Un utilisateur ne peut accéder qu'à ses propres données, prouvé par test

---

# Phase 4 — Déploiement et observabilité

**Charge estimée : 8 à 12 j·h**

## Objectif

Mettre l'application en ligne, la surveiller, et automatiser le déploiement depuis la CI. Sans Terraform : déploiement manuel documenté d'abord, puis scripté dans GitHub Actions.

## Périmètre

### AWS

| Service | Usage |
|---|---|
| S3 | Vidéos, Parquet, overlays, remplace le stockage local sans changement de code |
| ECR | Registre d'images |
| ECS Fargate | API et Celery beat |
| EC2 | Worker, instance GPU spot si nécessaire, éteinte hors utilisation |
| RDS Postgres ou Postgres conteneurisé | Arbitrage coût / réalisme à trancher |
| ElastiCache ou Valkey conteneurisé | Idem |
| IAM | Rôles minimaux par service, jamais de clés en dur |

**Fargate n'offre pas de GPU.** Si le worker a besoin d'un GPU, c'est EC2. À arbitrer en début de phase : un worker CPU avec des modèles plus légers est peut-être suffisant et infiniment plus simple à opérer.

**Coûts** : chiffrer avec le calculateur AWS avant de déployer quoi que ce soit. Stratégie par défaut : infra allumée à la demande pour les démonstrations, pas en continu. Alerte de budget configurée dès le premier jour.

### CI/CD

Workflow de déploiement : build de l'image → push vers ECR → mise à jour du service ECS → vérification de santé → rollback automatique si le healthcheck échoue. Déclenchement sur tag, pas sur chaque push vers `main`.

Secrets dans GitHub Secrets, credentials AWS par OIDC plutôt que par clés longue durée : aucune clé permanente ne peut fuiter depuis la CI.

### Observabilité

- Métriques applicatives : latence par endpoint, taux d'erreur, longueur de la file, durée de traitement par étape
- Métriques modèle : distribution des scores de confiance, proportion d'essais rejetés pour qualité de pose insuffisante, nombre de prises détectées par mur
- Dérive : suivi des caractéristiques d'entrée (résolution, luminosité moyenne, durée) avec alerte si la distribution s'éloigne de celle du dataset d'entraînement
- Tableau de bord Grafana, logs structurés en JSON avec identifiant de corrélation propagé de l'API jusqu'au worker

## Critères d'acceptation

- [ ] L'application est joignable depuis internet et le parcours complet fonctionne en ligne
- [ ] Un tag git déclenche un déploiement automatique avec rollback en cas d'échec de healthcheck
- [ ] Le tableau de bord Grafana affiche métriques système et métriques modèle
- [ ] Une alerte de budget AWS est active
- [ ] Une procédure de restauration de base est documentée et testée au moins une fois
- [ ] Aucun secret dans le dépôt, aucune clé AWS longue durée dans la CI

---

# Phase 5 — Optionnelle

À n'ouvrir qu'avec un objectif précis.

**Optimisation d'inférence.** Export ONNX, quantization, comparaison de latence et de précision avant/après, éventuellement Triton.

**Multi-vue.** Deux téléphones synchronisés, calibration à partir des prises comme points de correspondance, reconstruction 3D de la pose. Débloque les métriques réellement intéressantes : distance bassin-mur, rotation du bassin, extension réelle. C'est le prolongement direct de mon travail publié sur la pose 3D multi-vue non calibrée. Référence du domaine : **CIMI4D** (CVPR 2023), dataset multimodal d'escalade avec LiDAR, IMU et poses SMPL.

**Prédiction de la couleur.** Modèle sur graphe prenant en entrée la structure du bloc (prises, positions, types) pour prédire sa couleur, donc son niveau. Dans une salle à échelle de couleurs, c'est une classification à six ou sept classes, avec des labels obtenus gratuitement à l'ouverture de chaque bloc. Nettement plus tractable qu'une régression sur une cotation continue, mais il faut quand même des centaines de blocs annotés. Réaliste à moyen terme si la base grossit, hors de portée aujourd'hui.

**Comparaison inter-salles.** Chaque réseau a son échelle, et passer de l'une à l'autre demande une table d'équivalence vers Fontainebleau qu'aucune salle ne publie officiellement. Faisable avec une correspondance approximative assumée, mais à ne pas ouvrir avant que l'usage mono-salle soit solide.

**Kubernetes.** Pas prévu : ECS couvre le besoin à cette échelle, et un cluster ajouterait de l'exploitation sans bénéfice.

---

# Backlog

Idées écartées du périmètre mais conservées :

- **Détection de techniques par règles** (drop knee, pieds hauts, flags avant / arrière / intérieur) à partir des angles articulaires et des positions relatives. Un précédent publié obtient des F1 de 0,71 à 0,93 avec une précision quasi parfaite. Coût faible, et « le système a repéré trois drop knees » se comprend plus vite qu'un jerk normalisé.
- Détection automatique des tentatives dans une vidéo longue de session
- Comparaison côte à côte de deux essais synchronisés temporellement
- Export des données personnelles au format ouvert
- Application mobile pour l'upload direct depuis la salle
- Détection de zone et de top automatique à partir de la position des mains
- Suivi de charge d'entraînement couplé aux données de musculation
