# Bot Discord du Shogunat + panneau d'admin

Un seul programme Python qui fait tourner :

- **le bot** (la mascotte principale) sur ton serveur Discord ;
- **le panneau d'admin**, un site web où le staff se connecte avec son compte Discord.

## Ce que ça fait

| Fonction | Côté joueurs (Discord) | Côté staff (panneau) |
|---|---|---|
| Statut du serveur | Message mis à jour chaque minute dans #statut, statut du bot (« 3/20 joueurs »), `/statut`, `/ip` | Tableau de bord |
| Classements | Message mis à jour toutes les 10 min dans #classements, `/classement` | Aperçu + bouton « Actualiser » |
| Annonces | Publiées dans #annonces (ou par la mascotte d'un clan dans son salon), et aussi dans le chat Minecraft si coché | Rédaction, programmation à une date, mention @everyone/@here |
| Tickets | Menu dans #tickets : ouvre un fil privé avec le staff | Liste, lien vers le fil, fermeture |
| FAQ | `/faq` (avec recherche), salon #faq | Ajout, modification, publication |
| Suggestions | `/suggestion` (mod ou fonctionnalité), vote 👍 / 👎 dans #suggestions | Accepter / refuser / « ajoutée », avec réponse ; l'auteur est prévenu en MP |
| Mascottes | Le bot porte le nom et l'avatar de la mascotte ; dans le salon de chaque clan, il parle en tant que mascotte du clan | Noms, avatars, faire parler une mascotte de clan |
| Structure | — | Crée catégories, salons, rôles des 4 clans et du staff, en un clic |

Les classements viennent du script KubeJS `shogunat_scores.js` du serveur (kills, morts, temps de jeu, argent, clans),
lus via RCON grâce au petit script `kubejs/shogunat_discord.js` fourni ici.

**Permissions administrateur :** le bot n'a **jamais** besoin d'être administrateur. Il lui faut seulement
« Gérer les salons / les rôles / les webhooks » le temps de créer la structure ; le panneau te donne un lien
pour les ajouter, puis tu les retires (voir étape 4).

---

## Étape 1 : créer l'application Discord

1. Va sur https://discord.com/developers/applications → **New Application**, nomme-la comme ta mascotte.
2. **General Information** : mets l'image de la mascotte en icône.
3. **Bot** : clique **Reset Token** et copie le jeton (`DISCORD_TOKEN`). Aucun « Privileged Gateway Intent » n'est nécessaire.
4. **OAuth2** : copie **Client ID** et **Client Secret** (`DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`).
   Dans **Redirects**, ajoute `https://<adresse du panneau>/callback` (voir étape 3).
5. Dans Discord : Paramètres → Avancés → **Mode développeur**, puis clic droit sur ton serveur → **Copier l'identifiant** (`GUILD_ID`).

## Étape 2 : activer RCON sur le serveur Minecraft (MineStrator)

1. Dans le panel MineStrator, onglet des ports : **ajoute un port public** (MineStrator l'attribue, entre 40000 et 49999).
2. Dans `server.properties` :
   ```
   enable-rcon=true
   rcon.port=<le port attribué>
   rcon.password=<un long mot de passe aléatoire>
   ```
3. Copie `kubejs/shogunat_discord.js` dans `kubejs/server_scripts/` du serveur.
4. Redémarre le serveur. Dans la console, `shogunat_discord export` doit afficher du JSON.

Le mot de passe RCON donne un accès complet à la console : ne le partage jamais.

## Étape 3 : héberger sur Railway (recommandé, environ 5 $/mois)

Railway fait tourner le bot et le panneau 24h/24, avec une adresse HTTPS fournie, sans rien installer.
Offre **Hobby** : 5 $/mois, qui incluent 5 $ de consommation ; ce bot en consomme nettement moins.

1. Va sur https://railway.com et connecte-toi avec GitHub, puis passe à l'offre **Hobby**.
2. **New Project → Deploy from GitHub repo** → choisis le dépôt privé `shogunat-bot`.
   Railway détecte Python, installe `requirements.txt` et lance `python run.py` (réglé dans `railway.json`).
3. **Volume** (pour garder la base de données entre deux redémarrages) : dans le projet, clic droit sur le service
   → **Attach Volume** → chemin de montage `/data`.
4. **Adresse** : onglet **Settings → Networking → Generate Domain**, ex. `shogunat-bot-production.up.railway.app`.
5. **Variables** : onglet **Variables → Raw Editor**, colle :
   ```
   DISCORD_TOKEN=...
   DISCORD_CLIENT_ID=...
   DISCORD_CLIENT_SECRET=...
   GUILD_ID=...
   SESSION_SECRET=...
   PUBLIC_URL=https://shogunat-bot-production.up.railway.app
   DB_PATH=/data/shogunat.db
   RCON_HOST=91.197.6.134
   RCON_PORT=...
   RCON_PASSWORD=...
   ```
   Ne mets pas `WEB_HOST` ni `WEB_PORT` : Railway fournit le port lui-même.
6. Dans le portail Discord, **OAuth2 → Redirects** : ajoute `https://shogunat-bot-production.up.railway.app/callback`.
7. Railway redéploie tout seul. Les journaux du bot sont dans l'onglet **Deployments → View logs**.
   Chaque `git push` sur le dépôt met le bot à jour automatiquement.

## Variante : héberger sur Oracle Cloud (gratuit, plus long à installer)

1. Crée un compte sur https://www.oracle.com/cloud/free/ (une carte bancaire est demandée pour vérification, rien n'est débité
   tant que tu restes sur les offres « Always Free »).
2. **Compute → Instances → Create instance** : image **Ubuntu 24.04**, forme **VM.Standard.A1.Flex** (Ampere, gratuite),
   1 OCPU et 6 Go suffisent. Télécharge la clé SSH proposée.
3. Ouvre le web : **Networking → Virtual Cloud Networks → ton réseau → Security Lists → Default** →
   **Add Ingress Rules** : source `0.0.0.0/0`, ports `80` et `443` (TCP).
4. Ton adresse de panneau sera ton IP publique avec des tirets + `.sslip.io`, ex. IP `152.70.12.34` →
   `152-70-12-34.sslip.io`. Ça donne un vrai certificat HTTPS sans acheter de domaine.
5. Sur ton Mac, prépare le fichier `.env` : copie `.env.example` en `.env` et remplis-le, avec
   `PUBLIC_URL=https://152-70-12-34.sslip.io`. Pour `SESSION_SECRET` :
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
6. Envoie le projet sur le serveur et installe :
   ```bash
   scp -i ~/Downloads/ta-cle.key -r shogunat-bot ubuntu@152.70.12.34:~/
   ssh -i ~/Downloads/ta-cle.key ubuntu@152.70.12.34
   cd shogunat-bot && sudo bash deploy/installer.sh 152-70-12-34.sslip.io
   ```
   Le bot redémarre tout seul en cas de plantage ou de redémarrage de la machine.

## Étape 4 : premier lancement

1. Ouvre le panneau (ton adresse Railway, ou `https://152-70-12-34.sslip.io` sur Oracle), onglet **Structure Discord**.
2. Clique **« Lien avec les permissions de structure »** et ajoute le bot à ton serveur.
3. Connecte-toi au panneau avec Discord (il faut avoir « Gérer le serveur » sur Discord).
4. **Créer / réparer la structure** : salons, rôles, mascottes de clan, messages de statut, classements, FAQ et tickets.
5. Retire les permissions de structure : clique **« Lien d'invitation (quotidien) »** et valide, ou décoche
   « Gérer les salons / rôles / webhooks » sur le rôle du bot.
6. Donne le rôle **Staff Shogunat** aux membres du staff : ils pourront se connecter au panneau, voir #journal-staff et les tickets.
7. Donne à chaque joueur le rôle de son clan pour qu'il voie le salon du clan.

## Mises à jour

```bash
scp -i ~/Downloads/ta-cle.key -r shogunat-bot ubuntu@152.70.12.34:~/
ssh -i ~/Downloads/ta-cle.key ubuntu@152.70.12.34 "cd shogunat-bot && sudo bash deploy/installer.sh 152-70-12-34.sslip.io"
```
La base de données (`/opt/shogunat-bot/shogunat.db`) et le `.env` déjà en place sont conservés.

## Tester sur ton Mac

```bash
cd shogunat-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # puis remplis-le
.venv/bin/python run.py
```
Panneau sur http://localhost:8080 (ajoute `http://localhost:8080/callback` dans les Redirects Discord).
Pour regarder le panneau sans Discord : `DEV_ADMIN=1 .venv/bin/python run.py`, puis http://localhost:8080/login.
Ce mode n'est actif qu'en local (`localhost`), jamais sur le serveur.

## Organisation du code

```
run.py                      lance le bot et le panneau
shogunat/config.py          réglages (.env)
shogunat/db.py              base SQLite
shogunat/minecraft.py       statut (ping), RCON, statistiques
shogunat/bot.py             bot : mascotte, structure, statut, classements, annonces, /statut /classement /ip
shogunat/interactions.py    tickets, FAQ (/faq), suggestions (/suggestion)
shogunat/web.py             panneau : connexion Discord + API
shogunat/static/            interface du panneau
kubejs/shogunat_discord.js  à copier sur le serveur Minecraft
railway.json                réglages Railway
deploy/                     installation sur Oracle Cloud (variante)
```
