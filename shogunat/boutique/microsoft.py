"""Connexion avec Microsoft : prouve que le joueur possède le compte Minecraft Java dont il se réclame.

Chaîne officielle. Aucun mot de passe ne transite par nous, et les jetons sont jetés aussitôt utilisés
(jamais stockés, jamais écrits dans les journaux) :
  1. Microsoft (OAuth 2.0 : code + PKCE + state)        → jeton d'accès, portée XboxLive.signin
  2. Xbox Live (user.auth.xboxlive.com)                  → jeton XBL
  3. XSTS (pour rp://api.minecraftservices.com/)         → jeton XSTS + identifiant uhs
  4. Minecraft (login_with_xbox)                         → jeton Minecraft
     ⚠ Mojang n'ouvre cette étape qu'aux applications Azure qu'il a validées : sinon, erreur 403.
  5. Profil Minecraft                                    → UUID + pseudo (404 si Minecraft Java n'est pas acheté)
"""
import asyncio
import base64
import hashlib
import logging
import secrets
from urllib.parse import urlencode

import aiohttp

from .. import config
from . import securite

log = logging.getLogger("shogunat.boutique.microsoft")

URLS = {
    "autoriser": "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize",
    "jeton": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    "xbl": "https://user.auth.xboxlive.com/user/authenticate",
    "xsts": "https://xsts.auth.xboxlive.com/xsts/authorize",
    "minecraft": "https://api.minecraftservices.com/authentication/login_with_xbox",
    "profil": "https://api.minecraftservices.com/minecraft/profile",
}
PORTEE = "XboxLive.signin"
DELAI = aiohttp.ClientTimeout(total=15)

# Codes d'erreur XSTS documentés par la communauté (wiki Minecraft, « Microsoft Authentication Scheme »)
ERREURS_XSTS = {
    2148916233: "Ce compte Microsoft n'a pas encore de profil Xbox. Connecte-toi une fois sur minecraft.net, puis réessaie.",
    2148916235: "Xbox Live n'est pas disponible dans le pays de ce compte.",
    2148916236: "Ce compte doit d'abord être vérifié comme majeur sur le site Xbox.",
    2148916237: "Ce compte doit d'abord être vérifié comme majeur sur le site Xbox.",
    2148916238: "Compte mineur : il doit être ajouté à une famille Microsoft par un adulte avant de se connecter.",
}


class ErreurConnexion(Exception):
    """Le message est montré tel quel au joueur (jamais de détail technique ni de jeton)."""


def configure():
    return bool(config.MS_CLIENT_ID and config.MS_CLIENT_SECRET)


def adresse_retour():
    return f"{config.PUBLIC_URL}/boutique/connexion/retour"


def nouveau_pkce():
    verificateur = secrets.token_urlsafe(64)  # 86 caractères, dans la plage 43–128 imposée par la norme
    defi = base64.urlsafe_b64encode(hashlib.sha256(verificateur.encode()).digest()).rstrip(b"=").decode()
    return verificateur, defi


def url_autorisation(etat, defi):
    return URLS["autoriser"] + "?" + urlencode({
        "client_id": config.MS_CLIENT_ID, "response_type": "code", "redirect_uri": adresse_retour(),
        "scope": PORTEE, "state": etat, "code_challenge": defi, "code_challenge_method": "S256",
        "prompt": "select_account", "response_mode": "query"})


async def _json(reponse):
    try:
        return await reponse.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return {}


async def profil_depuis_code(code, verificateur, http=None):
    """Échange le code Microsoft contre le profil Minecraft. Renvoie (uuid avec tirets, pseudo)."""
    if not isinstance(code, str) or not 10 <= len(code) <= 4096:
        raise ErreurConnexion("Connexion Microsoft invalide. Réessaie.")
    propre = http is None
    http = http or aiohttp.ClientSession(timeout=DELAI)
    try:
        # 1. code → jeton Microsoft
        async with http.post(URLS["jeton"], data={
                "client_id": config.MS_CLIENT_ID, "client_secret": config.MS_CLIENT_SECRET, "grant_type": "authorization_code",
                "code": code, "redirect_uri": adresse_retour(), "code_verifier": verificateur, "scope": PORTEE}) as r:
            d = await _json(r)
            if r.status != 200 or not d.get("access_token"):
                log.warning("Microsoft a refusé l'échange du code (HTTP %s, %s)", r.status, d.get("error"))
                raise ErreurConnexion("Microsoft a refusé la connexion. Réessaie.")
            jeton_ms = d["access_token"]

        # 2. Xbox Live
        async with http.post(URLS["xbl"], json={
                "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": f"d={jeton_ms}"},
                "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"},
                headers={"Accept": "application/json"}) as r:
            d = await _json(r)
            if r.status != 200 or not d.get("Token"):
                log.warning("Xbox Live a refusé le jeton (HTTP %s)", r.status)
                raise ErreurConnexion("Xbox Live n'a pas accepté ce compte. Réessaie.")
            jeton_xbl = d["Token"]

        # 3. XSTS pour les services Minecraft
        async with http.post(URLS["xsts"], json={
                "Properties": {"SandboxId": "RETAIL", "UserTokens": [jeton_xbl]},
                "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"},
                headers={"Accept": "application/json"}) as r:
            d = await _json(r)
            if r.status != 200:
                code_x = d.get("XErr")
                raise ErreurConnexion(ERREURS_XSTS.get(code_x, "Xbox Live a refusé ce compte."))
            try:
                jeton_xsts, uhs = d["Token"], d["DisplayClaims"]["xui"][0]["uhs"]
            except (KeyError, IndexError, TypeError):
                raise ErreurConnexion("Réponse Xbox Live inattendue. Réessaie.")

        # 4. Minecraft
        async with http.post(URLS["minecraft"], json={"identityToken": f"XBL3.0 x={uhs};{jeton_xsts}"},
                             headers={"Accept": "application/json"}) as r:
            d = await _json(r)
            if r.status == 403:
                log.error("Mojang refuse l'application Azure (403) : elle doit être validée pour l'API Minecraft.")
                raise ErreurConnexion("La connexion Minecraft n'est pas encore ouverte pour la boutique "
                                      "(validation de Mojang en attente). Réessaie plus tard.")
            if r.status != 200 or not d.get("access_token"):
                log.warning("Minecraft a refusé la connexion (HTTP %s)", r.status)
                raise ErreurConnexion("Minecraft a refusé la connexion. Réessaie.")
            jeton_mc = d["access_token"]

        # 5. Profil (UUID + pseudo)
        async with http.get(URLS["profil"], headers={"Authorization": f"Bearer {jeton_mc}", "Accept": "application/json"}) as r:
            d = await _json(r)
            if r.status == 404:
                raise ErreurConnexion("Ce compte ne possède pas Minecraft Java Edition.")
            if r.status != 200:
                raise ErreurConnexion("Impossible de lire ton profil Minecraft. Réessaie.")
        uuid_brut, nom = d.get("id"), d.get("name")
        if not isinstance(uuid_brut, str) or not isinstance(nom, str) or not securite.PSEUDO.match(nom):
            raise ErreurConnexion("Profil Minecraft inattendu. Réessaie.")
        try:
            return securite.uuid_avec_tirets(uuid_brut), nom
        except ValueError:
            raise ErreurConnexion("Profil Minecraft inattendu. Réessaie.")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise ErreurConnexion("Microsoft ne répond pas pour le moment. Réessaie dans un instant.")
    finally:
        if propre:
            await http.close()
