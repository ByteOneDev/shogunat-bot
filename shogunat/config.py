"""Réglages lus dans le fichier .env (jamais commité) ou les variables d'environnement."""
import os
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def _charger_env(fichier):
    if not fichier.exists():
        return
    for ligne in fichier.read_text("utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, val = ligne.split("=", 1)
        os.environ.setdefault(cle.strip(), val.strip().strip('"').strip("'"))


_charger_env(RACINE / ".env")


def _int(nom, defaut=0):
    val = os.environ.get(nom, "").strip()
    return int(val) if val else defaut


# Discord
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
GUILD_ID = _int("GUILD_ID")
# Rôle Discord qui donne accès au panneau (en plus des membres ayant « Gérer le serveur »)
ADMIN_ROLE_ID = _int("ADMIN_ROLE_ID")

# Panneau web
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/")
WEB_HOST = os.environ.get("WEB_HOST", "127.0.0.1")
WEB_PORT = _int("WEB_PORT", 8080)
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

# Serveur Minecraft
MC_ADRESSE = os.environ.get("MC_ADRESSE", "91.197.6.134:22112")
RCON_HOST = os.environ.get("RCON_HOST", "91.197.6.134")
RCON_PORT = _int("RCON_PORT")
RCON_PASSWORD = os.environ.get("RCON_PASSWORD", "")

DB_PATH = Path(os.environ.get("DB_PATH", str(RACINE / "shogunat.db")))


def problemes():
    """Réglages manquants, affichés au démarrage et dans le panneau."""
    manque = []
    if not DISCORD_TOKEN:
        manque.append("DISCORD_TOKEN")
    if not (DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET):
        manque.append("DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET (connexion au panneau)")
    if not GUILD_ID:
        manque.append("GUILD_ID")
    if len(SESSION_SECRET) < 32:
        manque.append("SESSION_SECRET (au moins 32 caractères)")
    if not (RCON_PORT and RCON_PASSWORD):
        manque.append("RCON_PORT / RCON_PASSWORD (classements et annonces en jeu)")
    return manque
