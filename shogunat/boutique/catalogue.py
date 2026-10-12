"""Catalogue et commandes de la boutique.

Règle de sécurité centrale : le navigateur n'envoie que des identifiants de produits et des quantités.
Les prix, les noms et le total sont toujours relus ici, dans la base, au moment de la commande.
Les montants sont en centimes (entiers) : jamais de nombres à virgule pour de l'argent.
"""
import json
import secrets

from .. import db

DEVISE = "eur"

SCHEMA = """
CREATE TABLE IF NOT EXISTS boutique_produits (
  id TEXT PRIMARY KEY,                     -- identifiant court, ex. grade-sakura
  categorie TEXT NOT NULL,                 -- grades | cosmetiques | soutien
  nom TEXT NOT NULL,
  kanji TEXT,                              -- illustration provisoire tant qu'il n'y a pas d'image
  accroche TEXT NOT NULL,
  avantages TEXT NOT NULL DEFAULT '[]',    -- liste JSON de textes
  prix_centimes INTEGER NOT NULL CHECK (prix_centimes > 0),
  quantite_max INTEGER NOT NULL DEFAULT 1 CHECK (quantite_max BETWEEN 1 AND 99),
  image TEXT,                              -- nom de fichier dans static/img/produits/, NULL = illustration provisoire
  commandes TEXT NOT NULL DEFAULT '[]',    -- commandes RCON de livraison (liste JSON, voir livraison.py)
  actif INTEGER NOT NULL DEFAULT 1,
  ordre INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS boutique_commandes (
  id TEXT PRIMARY KEY,                     -- aléatoire (non devinable), jamais un simple numéro
  joueur_uuid TEXT NOT NULL,
  joueur_nom TEXT NOT NULL,
  lignes TEXT NOT NULL,                    -- JSON figé au moment de l'achat : [{id, nom, prix_centimes, quantite}]
  total_centimes INTEGER NOT NULL CHECK (total_centimes > 0),
  devise TEXT NOT NULL,
  statut TEXT NOT NULL DEFAULT 'en_attente',
     -- en_attente | attente_paiement | payee | livree | expiree | echec | remboursee | litige | anomalie
  stripe_session TEXT UNIQUE,
  stripe_paiement TEXT,
  conditions_acceptees_le INTEGER NOT NULL, -- case « livraison immédiate et renonciation à la rétractation »
  cree_le INTEGER NOT NULL,
  payee_le INTEGER,
  livree_le INTEGER,
  tentatives INTEGER NOT NULL DEFAULT 0,
  etapes_faites TEXT NOT NULL DEFAULT '[]', -- commandes de livraison déjà exécutées (jamais rejouées)
  erreur TEXT
);
CREATE INDEX IF NOT EXISTS boutique_commandes_joueur ON boutique_commandes (joueur_uuid);
CREATE INDEX IF NOT EXISTS boutique_commandes_statut ON boutique_commandes (statut);

-- Chaque événement Stripe n'est traité qu'une fois (Stripe peut renvoyer le même plusieurs fois)
CREATE TABLE IF NOT EXISTS boutique_evenements (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  recu_le INTEGER NOT NULL
);
"""

# Produits de départ : prix provisoires, modifiables plus tard depuis le panneau.
# Uniquement des avantages cosmétiques ou de soutien : les règles de Mojang interdisent de vendre
# un avantage de jeu (pas de « pay to win »).
MERCI = ('tellraw {joueur} {"text":"⛩ Merci pour ton soutien au Shogunat ! Ton achat sera activé '
         'très vite.","color":"light_purple"}')
PRODUITS_DEFAUT = [
    ("grade-sakura", "grades", "Grade Sakura", "桜", "Le premier pas sous les cerisiers.",
     ["Préfixe 🌸 Sakura dans le chat", "Pseudo en rose sakura", "Rôle Donateur sur Discord"], 499, 1, 10),
    ("grade-lanterne", "grades", "Grade Lanterne", "灯", "Ta lumière guide les voyageurs de l'archipel.",
     ["Tous les avantages Sakura", "Préfixe 🏮 Lanterne", "Particules de lanternes flottantes"], 999, 1, 20),
    ("grade-jade", "grades", "Grade Dragon de Jade", "龍", "Gardien du Trône de Jade, honoré par les quatre clans.",
     ["Tous les avantages Lanterne", "Titre « Gardien du Trône de Jade »", "Effet d'arrivée du dragon"], 1999, 1, 30),
    ("cos-petales", "cosmetiques", "Traînée de pétales", "花", "Chacun de tes pas fait pleuvoir des pétales.",
     ["Particules de pétales derrière toi", "Activable à volonté"], 299, 1, 40),
    ("cos-banniere", "cosmetiques", "Bannière brodée", "旗", "Porte fièrement les couleurs de ton clan.",
     ["Cape aux couleurs de ton clan", "Brodée de son kanji"], 349, 1, 50),
    ("cos-familier", "cosmetiques", "Familier esprit", "霊", "Un petit esprit de l'archipel te suit partout.",
     ["Familier cosmétique qui te suit", "Sans effet sur le jeu"], 449, 1, 60),
    ("don-2", "soutien", "Offrande de thé", "茶", "Un geste simple pour l'hébergement du serveur.",
     ["100 % reversés au serveur", "Ton nom au Temple des donateurs"], 200, 10, 70),
    ("don-5", "soutien", "Offrande de riz", "米", "De quoi nourrir le serveur une semaine.",
     ["100 % reversés au serveur", "Ton nom au Temple des donateurs"], 500, 10, 80),
    ("don-10", "soutien", "Offrande de saké", "酒", "Les esprits de l'archipel te remercient.",
     ["100 % reversés au serveur", "Ton nom au Temple des donateurs"], 1000, 10, 90),
]


class PanierInvalide(ValueError):
    """Panier refusé : le message est montré tel quel au joueur."""


def initialiser():
    c = db.conn()
    c.executescript(SCHEMA)
    for pid, cat, nom, kanji, accroche, avantages, prix, qmax, ordre in PRODUITS_DEFAUT:
        c.execute("INSERT OR IGNORE INTO boutique_produits (id, categorie, nom, kanji, accroche, avantages, prix_centimes, "
                  "quantite_max, commandes, ordre) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (pid, cat, nom, kanji, accroche, json.dumps(avantages, ensure_ascii=False), prix, qmax,
                   json.dumps([MERCI], ensure_ascii=False), ordre))
    c.commit()


def catalogue_public():
    """Ce que voit le navigateur : jamais les commandes de livraison."""
    produits = db.tous("SELECT id, categorie, nom, kanji, accroche, avantages, prix_centimes, quantite_max, image "
                       "FROM boutique_produits WHERE actif = 1 ORDER BY ordre, id")
    for p in produits:
        p["avantages"] = json.loads(p["avantages"] or "[]")
    return {"devise": DEVISE, "produits": produits}


def calculer_panier(lignes_demandees):
    """Valide le panier envoyé par le navigateur et recalcule tout depuis la base.

    Renvoie (lignes figées, total en centimes). Refuse : format inattendu, produit inconnu ou retiré,
    quantité hors limites, doublons, panier vide ou trop gros.
    """
    if not isinstance(lignes_demandees, list) or not lignes_demandees:
        raise PanierInvalide("Ton panier est vide.")
    if len(lignes_demandees) > 20:
        raise PanierInvalide("Panier trop grand.")
    vus, lignes, total = set(), [], 0
    for l in lignes_demandees:
        if not isinstance(l, dict):
            raise PanierInvalide("Panier invalide.")
        pid, quantite = l.get("id"), l.get("quantite")
        # bool est un int en Python : on l'écarte explicitement (True ne doit pas valoir 1 article)
        if not isinstance(pid, str) or not isinstance(quantite, int) or isinstance(quantite, bool):
            raise PanierInvalide("Panier invalide.")
        if pid in vus:
            raise PanierInvalide("Panier invalide.")
        vus.add(pid)
        p = db.un("SELECT id, nom, prix_centimes, quantite_max FROM boutique_produits WHERE id = ? AND actif = 1", pid)
        if not p:
            raise PanierInvalide("Un article de ton panier n'est plus disponible. Recharge la page.")
        if not 1 <= quantite <= p["quantite_max"]:
            raise PanierInvalide(f"Quantité impossible pour « {p['nom']} ».")
        lignes.append({"id": p["id"], "nom": p["nom"], "prix_centimes": p["prix_centimes"], "quantite": quantite})
        total += p["prix_centimes"] * quantite
    if not 50 <= total <= 50_000:  # Stripe refuse moins de 0,50 € ; plafond anti-erreur à 500 €
        raise PanierInvalide("Montant de commande hors limites.")
    return lignes, total


def creer_commande(joueur_uuid, joueur_nom, lignes, total):
    cid = secrets.token_urlsafe(18)
    db.executer("INSERT INTO boutique_commandes (id, joueur_uuid, joueur_nom, lignes, total_centimes, devise, "
                "conditions_acceptees_le, cree_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                cid, joueur_uuid, joueur_nom, json.dumps(lignes, ensure_ascii=False), total, DEVISE,
                db.maintenant(), db.maintenant())
    return cid


def commande_du_joueur(cid, joueur_uuid):
    """Une commande n'est visible que par le joueur qui l'a passée."""
    c = db.un("SELECT id, lignes, total_centimes, devise, statut, cree_le, payee_le, livree_le FROM boutique_commandes "
              "WHERE id = ? AND joueur_uuid = ?", cid, joueur_uuid)
    if c:
        c["lignes"] = json.loads(c["lignes"])
    return c


def commandes_du_joueur(joueur_uuid, limite=20):
    lignes = db.tous("SELECT id, lignes, total_centimes, devise, statut, cree_le FROM boutique_commandes "
                     "WHERE joueur_uuid = ? AND statut NOT IN ('en_attente', 'expiree') ORDER BY cree_le DESC LIMIT ?",
                     joueur_uuid, limite)
    for c in lignes:
        c["lignes"] = json.loads(c["lignes"])
    return lignes
