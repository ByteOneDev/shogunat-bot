"""Tests : chaque fichier utilise une base SQLite temporaire, jamais la vraie base du bot.

Lancer : .venv/bin/python -m unittest discover -s tests -v
"""
import os
import tempfile

_dossier = tempfile.mkdtemp(prefix="shogunat-tests-")
os.environ["DB_PATH"] = os.path.join(_dossier, "test.db")
os.environ["SESSION_SECRET"] = "secret-de-test-qui-fait-plus-de-32-caracteres-0123456789"
os.environ["PUBLIC_URL"] = "https://boutique.example.test"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_cle_factice"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_secret_de_test"
os.environ["MS_CLIENT_ID"] = "00000000-0000-0000-0000-00000000c1e1"
os.environ["MS_CLIENT_SECRET"] = "secret-microsoft-factice"
os.environ.setdefault("BOUTIQUE", "on")
for cle in ("DISCORD_TOKEN", "GUILD_ID", "PORT", "SERVER_PORT", "DEV_ADMIN", "BOUTIQUE_DEV", "RCON_PORT", "RCON_PASSWORD"):
    os.environ.pop(cle, None)
