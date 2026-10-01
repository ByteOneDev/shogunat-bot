"""Base SQLite : réglages, annonces, FAQ, tickets, suggestions, clans."""
import json
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reglages (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS annonces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  titre TEXT NOT NULL,
  contenu TEXT NOT NULL,
  image_url TEXT,
  mention TEXT NOT NULL DEFAULT 'aucune',      -- aucune | everyone | here
  en_jeu INTEGER NOT NULL DEFAULT 0,           -- 1 = aussi affichée dans le chat Minecraft
  clan TEXT,                                   -- publiée par la mascotte d'un clan dans son salon
  prevue_le INTEGER,                           -- horodatage, NULL = tout de suite
  envoyee_le INTEGER,
  erreur TEXT,
  auteur TEXT,
  creee_le INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS faq (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT NOT NULL,
  reponse TEXT NOT NULL,
  ordre INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fil_id INTEGER,
  user_id INTEGER NOT NULL,
  user_nom TEXT,
  categorie TEXT NOT NULL,
  sujet TEXT NOT NULL,
  statut TEXT NOT NULL DEFAULT 'ouvert',       -- ouvert | ferme
  ouvert_le INTEGER NOT NULL,
  ferme_le INTEGER,
  ferme_par TEXT
);

CREATE TABLE IF NOT EXISTS suggestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  user_nom TEXT,
  type TEXT NOT NULL,                          -- mod | fonctionnalite
  titre TEXT NOT NULL,
  description TEXT NOT NULL,
  lien TEXT,
  statut TEXT NOT NULL DEFAULT 'en_attente',   -- en_attente | acceptee | refusee | faite
  note_admin TEXT,
  message_id INTEGER,
  pour INTEGER NOT NULL DEFAULT 0,
  contre INTEGER NOT NULL DEFAULT 0,
  creee_le INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS clans (
  slug TEXT PRIMARY KEY,
  nom TEXT NOT NULL,
  kanji TEXT,
  couleur TEXT NOT NULL,
  mascotte_nom TEXT,
  mascotte_avatar TEXT,
  salon_id INTEGER,
  role_id INTEGER,
  webhook_url TEXT
);
"""

# Les 4 clans du script KubeJS shogunat_clans.js, avec leur mascotte (nom par défaut, image)
CLANS_DEFAUT = [
    ("akamatsu", "Akamatsu", "赤松", "#C0392B", "Hibana"),
    ("mizuki", "Mizuki", "水樹", "#2980B9", "Shizuku"),
    ("kurogane", "Kurogane", "黒鉄", "#8E44AD", "Murasaki"),
    ("shinrin", "Shinrin", "森林", "#27AE60", "Kodama"),
]
MASCOTTE_DEFAUT = {"nom": "Sakura", "avatar_url": config.mascotte_url("sakura")}

_conn = None


def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(config.DB_PATH))
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        for slug, nom, kanji, couleur, mascotte in CLANS_DEFAUT:
            _conn.execute("INSERT OR IGNORE INTO clans (slug, nom, kanji, couleur, mascotte_nom, mascotte_avatar) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (slug, nom, kanji, couleur, mascotte, config.mascotte_url(slug)))
        _conn.commit()
    return _conn


def maintenant():
    return int(time.time())


def tous(sql, *args):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def un(sql, *args):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def executer(sql, *args):
    cur = conn().execute(sql, args)
    conn().commit()
    return cur.lastrowid


def maj(table, cle, valeur_cle, champs):
    """UPDATE table SET champs... WHERE cle = valeur_cle (noms de colonnes vérifiés par l'appelant)."""
    if not champs:
        return
    cols = ", ".join(f"{c} = ?" for c in champs)
    executer(f"UPDATE {table} SET {cols} WHERE {cle} = ?", *champs.values(), valeur_cle)


# --- réglages clé/valeur (salons configurés, IDs de messages à éditer, mascotte…) -----

def reglage(cle, defaut=None):
    r = un("SELECT valeur FROM reglages WHERE cle = ?", cle)
    return json.loads(r["valeur"]) if r else defaut


def definir(cle, valeur):
    executer("INSERT INTO reglages (cle, valeur) VALUES (?, ?) ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
             cle, json.dumps(valeur, ensure_ascii=False))
