"""Briques de sécurité de la boutique : sessions signées, limite de requêtes, validations, en-têtes."""
import base64
import hashlib
import hmac
import json
import re
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from aiohttp import web

from .. import config

COOKIE_SESSION = "boutique_session"
COOKIE_OAUTH = "boutique_oauth"
DUREE_SESSION = 12 * 3600
DUREE_OAUTH = 600

PSEUDO = re.compile(r"^[A-Za-z0-9_]{3,16}$")          # règle des pseudos Minecraft Java
UUID_BRUT = re.compile(r"^[0-9a-f]{32}$")             # format renvoyé par l'API Minecraft
UUID_TIRETS = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _cle():
    # Clé dérivée et propre à la boutique : un cookie du panneau ne peut pas servir ici, et inversement.
    return hmac.new(config.SESSION_SECRET.encode(), b"shogunat-boutique-v1", hashlib.sha256).digest()


def signer(donnees, usage):
    donnees = dict(donnees, usage=usage)
    brut = base64.urlsafe_b64encode(json.dumps(donnees, separators=(",", ":")).encode()).decode()
    sig = hmac.new(_cle(), brut.encode(), hashlib.sha256).hexdigest()
    return f"{brut}.{sig}"


def verifier(cookie, usage):
    """Renvoie les données si la signature, l'usage et la date d'expiration sont bons, sinon None."""
    if not cookie or len(cookie) > 4096:
        return None
    try:
        brut, sig = cookie.rsplit(".", 1)
        attendu = hmac.new(_cle(), brut.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, attendu):
            return None
        donnees = json.loads(base64.urlsafe_b64decode(brut.encode()))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(donnees, dict) or donnees.get("usage") != usage:
        return None
    if not isinstance(donnees.get("exp"), (int, float)) or donnees["exp"] < time.time():
        return None
    return donnees


def https():
    return config.PUBLIC_URL.startswith("https://")


def poser_cookie(reponse, nom, valeur, duree):
    reponse.set_cookie(nom, valeur, max_age=duree, path="/boutique", httponly=True, secure=https(), samesite="Lax")


def effacer_cookie(reponse, nom):
    reponse.del_cookie(nom, path="/boutique")


def uuid_avec_tirets(brut):
    brut = (brut or "").replace("-", "").lower()
    if not UUID_BRUT.match(brut):
        raise ValueError("uuid invalide")
    return f"{brut[:8]}-{brut[8:12]}-{brut[12:16]}-{brut[16:20]}-{brut[20:]}"


def joueur_valide(uuid, nom):
    return isinstance(uuid, str) and isinstance(nom, str) and bool(UUID_TIRETS.match(uuid)) and bool(PSEUDO.match(nom))


def session_joueur(request):
    s = verifier(request.cookies.get(COOKIE_SESSION, ""), "session")
    if s and joueur_valide(s.get("uuid"), s.get("nom")):
        return {"uuid": s["uuid"], "nom": s["nom"]}
    return None


# --- limite de requêtes (en mémoire, par adresse IP et par type d'action) --------------------

class Limiteur:
    def __init__(self):
        self.traces = defaultdict(deque)

    def autoriser(self, cle, maximum, fenetre=60.0):
        maintenant = time.monotonic()
        file = self.traces[cle]
        while file and maintenant - file[0] > fenetre:
            file.popleft()
        if len(file) >= maximum:
            return False
        file.append(maintenant)
        if len(self.traces) > 50_000:  # garde-fou mémoire
            self.traces.clear()
        return True


def ip_client(request):
    if config.TRUST_PROXY:
        transmis = request.headers.get("X-Forwarded-For", "")
        if transmis:
            return transmis.split(",")[0].strip()[:64]
    return request.remote or "?"


def origine_autorisee(request):
    """Requêtes qui modifient quelque chose : même origine obligatoire (protection CSRF)."""
    if request.headers.get("X-Boutique") != "1":
        return False
    origine = request.headers.get("Origin")
    adresse = urlsplit(config.PUBLIC_URL)
    return origine is None or origine == f"{adresse.scheme}://{adresse.netloc}"


# --- en-têtes de sécurité ---------------------------------------------------------------------

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data: https://mc-heads.net; connect-src 'self'; "
       "media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


def entetes(reponse, api=False):
    reponse.headers["Content-Security-Policy"] = CSP
    reponse.headers["X-Frame-Options"] = "DENY"
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    reponse.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    reponse.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if https():
        reponse.headers["Strict-Transport-Security"] = "max-age=31536000"
    if api:
        reponse.headers["Cache-Control"] = "no-store"


def erreur(message, statut=400):
    return web.json_response({"erreur": message}, status=statut)
