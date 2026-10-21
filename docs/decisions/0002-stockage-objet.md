# ADR 0002 : SeaweedFS comme stockage objet de développement

- Statut : accepté
- Date : 2026-10-20

## Contexte

Le projet stocke des fichiers volumineux qui n'ont pas leur place en base : vidéos téléversées, images extraites, résultats en Parquet. Ils vont dans un stockage objet compatible S3. Le client téléverse les vidéos directement dans le stockage, par une URL pré-signée que l'API lui fournit, sans faire transiter les octets par l'API.

En production, la cible est un service S3 managé. En développement et dans les tests d'intégration, il faut un serveur compatible S3 qui tourne en conteneur. Le code ne lui parle qu'à travers l'API S3, par `boto3` ; le serveur choisi est donc remplaçable sans toucher au code, et seule l'URL de l'endpoint change.

Le cahier des charges prévoyait MinIO. Son dépôt public a été archivé le 25 avril 2026 : l'édition communautaire n'est plus distribuée qu'en sources, sans binaires ni images, et ne reçoit plus de correctifs, de sécurité compris. Sa console d'administration avait déjà été retirée en 2025.

La licence n'est pas un critère éliminatoire ici, et la distinction mérite d'être posée. Le projet exclut toute dépendance copyleft *importée* par le code : une bibliothèque AGPL importée forme avec le programme une œuvre dérivée, et la licence se propage. Un serveur lancé dans son propre conteneur et utilisé à travers un protocole réseau ne crée pas ce lien : le code en est l'utilisateur, comme il l'est de PostgreSQL. Un serveur de stockage sous AGPL est donc acceptable ; ce qui départage les candidats, c'est leur maintenance et leur exploitation.

## Décision

Le stockage objet de développement et de test est SeaweedFS (Apache-2.0), dans son image officielle à version épinglée, lancé en un seul conteneur qui regroupe ses composants et expose la passerelle S3.

Le code reste indépendant du serveur : il ne connaît que l'API S3, et l'endpoint, les identifiants et le nom du bucket viennent de la configuration.

## Alternatives écartées

- MinIO, à une version épinglée. Aucun effort de migration, mais une dette de sécurité qui ne peut que croître : plus aucun correctif ne sera publié.
- Garage (AGPL-3.0). Actif et léger, pensé pour l'auto-hébergement. Sa couverture de l'API S3 est volontairement partielle, et même un nœud unique demande une étape d'initialisation propre (disposition du cluster, création des clés) avant de servir la moindre requête.
- RustFS (Apache-2.0). Actif, avec une console web intégrée, mais créé fin 2023 : trop peu de recul en production pour un composant dont dépendent tous les tests d'intégration.
- LocalStack. Il émule une grande partie d'AWS, bien au-delà du seul S3, ce qui en fait l'option la plus lourde pour un besoin limité au stockage objet. Le dépôt public de son édition libre est archivé depuis mars 2026.

## Conséquences

- L'API S3 est le seul contrat entre le code et le stockage. Passer à un service S3 managé, ou à un autre serveur, revient à changer l'endpoint et les identifiants dans la configuration.
- La compatibilité S3 de SeaweedFS n'est pas totale. Les fonctions utilisées par le projet (buckets, `PUT` et `GET` d'objets, URL pré-signées) sont couvertes ; toute fonction S3 supplémentaire doit être vérifiée sur SeaweedFS avant d'être adoptée.
- Les tests d'intégration lancent la même image par `testcontainers`. Ils testent un vrai serveur S3, pas un simulacre.
- Le bucket n'est pas créé par le serveur : un service d'initialisation le crée au démarrage de l'environnement, de façon idempotente.
- La version de l'image est épinglée et mise à jour volontairement, comme celle de PostgreSQL.
