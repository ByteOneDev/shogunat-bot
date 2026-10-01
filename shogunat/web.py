"""Panneau d'administration : connexion avec Discord + API JSON utilisée par static/app.js."""
import base64
import html
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from . import config, db, minecraft

log = logging.getLogger("shogunat.web")

STATIC = config.RACINE / "shogunat" / "static"
DUREE_SESSION = 7 * 86400
API_DISCORD = "https://discord.com/api/v10"
MODE_DEV = (os.environ.get("DEV_ADMIN") == "1" and config.WEB_HOST == "127.0.0.1"
            and config.PUBLIC_URL.startswith(("http://localhost", "http://127.0.0.1")))


# --- session signée (cookie) ------------------------------------------------------------

def _signer(donnees):
    brut = base64.urlsafe_b64encode(json.dumps(donnees, separators=(",", ":")).encode()).decode()
    sig = hmac.new(config.SESSION_SECRET.encode(), brut.encode(), hashlib.sha256).hexdigest()
    return f"{brut}.{sig}"


def _verifier(cookie):
    try:
        brut, sig = cookie.rsplit(".", 1)
        attendu = hmac.new(config.SESSION_SECRET.encode(), brut.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, attendu):
            return None
        donnees = json.loads(base64.urlsafe_b64decode(brut.encode()))
        return donnees if donnees.get("exp", 0) > time.time() else None
    except (ValueError, json.JSONDecodeError):
        return None


def _poser_cookie(reponse, nom, valeur, max_age):
    reponse.set_cookie(nom, valeur, max_age=max_age, httponly=True, samesite="Lax",
                       secure=config.PUBLIC_URL.startswith("https://"), path="/")


class Panneau:
    def __init__(self, bot):
        self.bot = bot  # None si DISCORD_TOKEN est absent (aperçu local)
        self.cache_admin = {}

    # --- garde d'accès ----------------------------------------------------------------

    async def utilisateur(self, request):
        session = _verifier(request.cookies.get("session", ""))
        if not session:
            return None
        if session.get("dev"):
            return session if MODE_DEV else None
        ok, expire = self.cache_admin.get(session["id"], (None, 0))
        if expire < time.time():  # le rôle peut avoir été retiré : on revérifie toutes les 5 minutes
            ok = bool(self.bot) and await self.bot.est_admin(int(session["id"]))
            self.cache_admin[session["id"]] = (ok, time.time() + 300)
        return session if ok else None

    @web.middleware
    async def securite(self, request, handler):
        if request.path.startswith("/api/"):
            request["user"] = await self.utilisateur(request)
            if not request["user"]:
                return web.json_response({"erreur": "non connecté"}, status=401)
            # en-tête personnalisé : bloque les requêtes forgées depuis un autre site (CSRF)
            if request.method != "GET" and request.headers.get("X-Shogunat") != "1":
                return web.json_response({"erreur": "requête refusée"}, status=403)
        try:
            reponse = await handler(request)
        except web.HTTPException:
            raise
        except Erreur as e:
            reponse = web.json_response({"erreur": str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            log.exception("erreur API")
            reponse = web.json_response({"erreur": f"erreur interne : {e}"}, status=500)
        reponse.headers.update({
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; img-src * data:; style-src 'self'; script-src 'self'; "
                                       "frame-ancestors 'none'",
        })
        return reponse

    def exiger_bot(self):
        if not self.bot or not self.bot.is_ready():
            raise Erreur("le bot n'est pas connecté à Discord (vérifie DISCORD_TOKEN dans .env).")
        return self.bot

    # --- connexion avec Discord -------------------------------------------------------

    async def connexion(self, request):
        if MODE_DEV:
            r = web.HTTPFound("/")
            _poser_cookie(r, "session", _signer({"id": "0", "nom": "Admin (aperçu local)", "avatar": None, "dev": True,
                                                  "exp": time.time() + DUREE_SESSION}), DUREE_SESSION)
            raise r
        etat = secrets.token_urlsafe(24)
        url = "https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": config.DISCORD_CLIENT_ID, "response_type": "code", "scope": "identify",
            "redirect_uri": f"{config.PUBLIC_URL}/callback", "state": etat, "prompt": "none"})
        r = web.HTTPFound(url)
        _poser_cookie(r, "oauth_etat", etat, 600)
        raise r

    async def retour_discord(self, request):
        etat, code = request.query.get("state", ""), request.query.get("code", "")
        if not code or not hmac.compare_digest(etat, request.cookies.get("oauth_etat", "")):
            return self.page_erreur("Connexion annulée ou expirée. Réessaie.")
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{API_DISCORD}/oauth2/token", data={
                    "client_id": config.DISCORD_CLIENT_ID, "client_secret": config.DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code", "code": code,
                    "redirect_uri": f"{config.PUBLIC_URL}/callback"}) as r:
                if r.status != 200:
                    return self.page_erreur("Discord a refusé la connexion.")
                jeton = (await r.json())["access_token"]
            async with http.get(f"{API_DISCORD}/users/@me", headers={"Authorization": f"Bearer {jeton}"}) as r:
                moi = await r.json()
        if not (self.bot and await self.bot.est_admin(int(moi["id"]))):
            return self.page_erreur("Ce panneau est réservé au staff du Shogunat.")
        avatar = (f"https://cdn.discordapp.com/avatars/{moi['id']}/{moi['avatar']}.png?size=64"
                  if moi.get("avatar") else None)
        r = web.HTTPFound("/")
        _poser_cookie(r, "session", _signer({"id": moi["id"], "nom": moi.get("global_name") or moi["username"],
                                              "avatar": avatar, "exp": time.time() + DUREE_SESSION}), DUREE_SESSION)
        r.del_cookie("oauth_etat", path="/")
        raise r

    async def deconnexion(self, request):
        r = web.HTTPFound("/")
        r.del_cookie("session", path="/")
        raise r

    def page_erreur(self, message):
        page = (STATIC / "erreur.html").read_text("utf-8").replace("{{message}}", html.escape(message))
        return web.Response(text=page, content_type="text/html", status=403)

    async def accueil(self, request):
        return web.FileResponse(STATIC / "index.html")

    # --- API : tableau de bord --------------------------------------------------------

    async def api_moi(self, request):
        return web.json_response(request["user"])

    async def api_tableau(self, request):
        bot = self.bot
        stats = minecraft.stats_en_cache()
        statut = (bot.dernier_statut if bot and bot.dernier_statut else await minecraft.statut())
        return web.json_response({
            "statut": statut,
            "bot": {"connecte": bool(bot and bot.is_ready()), "nom": str(bot.user) if bot and bot.user else None,
                    "serveur": bot.guild.name if bot and bot.guild else None},
            "config_manquante": config.problemes(),
            "compteurs": {
                "tickets_ouverts": db.un("SELECT COUNT(*) n FROM tickets WHERE statut = 'ouvert'")["n"],
                "suggestions_en_attente": db.un("SELECT COUNT(*) n FROM suggestions WHERE statut = 'en_attente'")["n"],
                "annonces_prevues": db.un("SELECT COUNT(*) n FROM annonces WHERE envoyee_le IS NULL AND erreur IS NULL")["n"],
                "joueurs_suivis": len(stats.get("joueurs", {})),
            },
            "stats_maj_le": stats.get("maj_le"),
            "adresse": config.MC_ADRESSE,
        })

    # --- annonces ---------------------------------------------------------------------

    async def api_annonces(self, request):
        return web.json_response(db.tous("SELECT * FROM annonces ORDER BY COALESCE(envoyee_le, prevue_le, creee_le) DESC LIMIT 100"))

    async def api_annonce_creer(self, request):
        d = await request.json()
        titre, contenu = texte(d, "titre", 256), texte(d, "contenu", 4000)
        if not titre or not contenu:
            raise Erreur("titre et message obligatoires.")
        prevue = d.get("prevue_le")  # horodatage calculé par le navigateur (heure locale de l'admin)
        if prevue is not None and (not isinstance(prevue, int) or prevue < 0):
            raise Erreur("date de programmation invalide.")
        mention = d.get("mention") if d.get("mention") in ("aucune", "everyone", "here") else "aucune"
        clan = d.get("clan") or None
        if clan and not db.un("SELECT slug FROM clans WHERE slug = ?", clan):
            raise Erreur("clan inconnu.")
        image = texte(d, "image_url", 500) or None
        if image and not image.startswith("https://"):
            raise Erreur("l'image doit être un lien https://")
        aid = db.executer("INSERT INTO annonces (titre, contenu, image_url, mention, en_jeu, clan, prevue_le, auteur, creee_le) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", titre, contenu, image, mention,
                          1 if d.get("en_jeu") else 0, clan, prevue, request["user"]["nom"], db.maintenant())
        return web.json_response(db.un("SELECT * FROM annonces WHERE id = ?", aid))

    async def api_annonce_supprimer(self, request):
        db.executer("DELETE FROM annonces WHERE id = ? AND envoyee_le IS NULL", int(request.match_info["id"]))
        return web.json_response({"ok": True})

    async def api_annonce_relancer(self, request):
        db.maj("annonces", "id", int(request.match_info["id"]), {"envoyee_le": None, "erreur": None, "prevue_le": None})
        return web.json_response({"ok": True})

    # --- classements ------------------------------------------------------------------

    async def api_classements(self, request):
        stats = minecraft.stats_en_cache()
        tops = {}
        for cat, (cle, titre, fmt) in minecraft.CATEGORIES.items():
            tops[cat] = {"titre": titre, "lignes": [{"nom": j["n"], "valeur": fmt(j.get(cle) or 0)}
                                                    for j in minecraft.top(stats, cat)]}
        clans = {c["slug"]: c for c in db.tous("SELECT slug, nom, kanji, couleur FROM clans")}
        tops["clans"] = {"titre": "⛩️ Clans", "lignes": [
            {"nom": clans.get(c["slug"], {}).get("nom", c["slug"]), "couleur": clans.get(c["slug"], {}).get("couleur"),
             "valeur": f"{c['kills']} kills · ${minecraft.format_argent(c['argent'])} · {c['membres']} membre(s)"}
            for c in minecraft.classement_clans(stats)]}
        return web.json_response({"maj_le": stats.get("maj_le"), "en_ligne": stats.get("en_ligne", []), "tops": tops})

    async def api_classements_rafraichir(self, request):
        try:
            await minecraft.rafraichir_stats()
        except (minecraft.RconErreur, ValueError) as e:
            raise Erreur(f"impossible de lire les stats : {e}")
        if self.bot and self.bot.is_ready():
            await self.bot.publier_classements()
        return await self.api_classements(request)

    # --- tickets ------------------------------------------------------------------------

    async def api_tickets(self, request):
        tickets = db.tous("SELECT * FROM tickets ORDER BY statut = 'ferme', id DESC LIMIT 200")
        gid = config.GUILD_ID
        for t in tickets:
            t["lien"] = f"https://discord.com/channels/{gid}/{t['fil_id']}" if t["fil_id"] else None
            t["user_id"] = str(t["user_id"])
            t["fil_id"] = str(t["fil_id"])
        return web.json_response(tickets)

    async def api_ticket_fermer(self, request):
        from .interactions import fermer_ticket
        t = db.un("SELECT * FROM tickets WHERE id = ?", int(request.match_info["id"]))
        if not t or t["statut"] != "ouvert":
            raise Erreur("ticket introuvable ou déjà fermé.")
        bot = self.exiger_bot()
        fil = bot.guild.get_thread(t["fil_id"])
        if fil:
            await fil.send(f"🔒 Ticket fermé par {request['user']['nom']} depuis le panneau.")
        await fermer_ticket(bot, t, request["user"]["nom"])
        return web.json_response({"ok": True})

    # --- FAQ ----------------------------------------------------------------------------

    async def api_faq(self, request):
        return web.json_response(db.tous("SELECT * FROM faq ORDER BY ordre, id"))

    async def api_faq_enregistrer(self, request):
        d = await request.json()
        q, r = texte(d, "question", 256), texte(d, "reponse", 1024)
        if not q or not r:
            raise Erreur("question et réponse obligatoires.")
        ordre = int(d.get("ordre") or 0)
        if request.match_info.get("id"):
            db.maj("faq", "id", int(request.match_info["id"]), {"question": q, "reponse": r, "ordre": ordre})
        else:
            db.executer("INSERT INTO faq (question, reponse, ordre) VALUES (?, ?, ?)", q, r, ordre)
        return await self.api_faq(request)

    async def api_faq_supprimer(self, request):
        db.executer("DELETE FROM faq WHERE id = ?", int(request.match_info["id"]))
        return await self.api_faq(request)

    async def api_faq_publier(self, request):
        from .interactions import publier_faq
        try:
            await publier_faq(self.exiger_bot())
        except RuntimeError as e:
            raise Erreur(str(e))
        return web.json_response({"ok": True})

    # --- suggestions --------------------------------------------------------------------

    async def api_suggestions(self, request):
        lignes = db.tous("SELECT * FROM suggestions ORDER BY statut != 'en_attente', (pour - contre) DESC, id DESC LIMIT 200")
        for s in lignes:
            s["user_id"] = str(s["user_id"])
            s["lien_discord"] = (f"https://discord.com/channels/{config.GUILD_ID}/{(db.reglage('salons') or {}).get('suggestions')}/{s['message_id']}"
                                 if s["message_id"] else None)
        return web.json_response(lignes)

    async def api_suggestion_statut(self, request):
        from .interactions import maj_suggestion
        d = await request.json()
        if d.get("statut") not in ("en_attente", "acceptee", "refusee", "faite"):
            raise Erreur("statut inconnu.")
        sid = int(request.match_info["id"])
        if not db.un("SELECT id FROM suggestions WHERE id = ?", sid):
            raise Erreur("suggestion introuvable.")
        await maj_suggestion(self.exiger_bot(), sid, d["statut"], texte(d, "note", 1000))
        return web.json_response({"ok": True})

    # --- clans et mascottes ---------------------------------------------------------------

    async def api_clans(self, request):
        clans = db.tous("SELECT slug, nom, kanji, couleur, mascotte_nom, mascotte_avatar, salon_id, role_id, "
                        "webhook_url IS NOT NULL AS pret FROM clans ORDER BY slug")
        stats = minecraft.stats_en_cache()
        for c in clans:
            c["salon_id"], c["role_id"] = str(c["salon_id"] or ""), str(c["role_id"] or "")
            membres = (stats.get("clans", {}).get(c["slug"]) or {}).get("membres", [])
            c["membres"] = [m.get("n") or (stats.get("joueurs", {}).get(m["u"]) or {}).get("n") or "?" for m in membres]
        return web.json_response({"clans": clans, "mascotte": db.reglage("mascotte", db.MASCOTTE_DEFAUT)})

    async def api_clan_maj(self, request):
        d = await request.json()
        avatar = texte(d, "mascotte_avatar", 500)
        if avatar and not avatar.startswith("https://"):
            raise Erreur("l'avatar doit être un lien https://")
        db.maj("clans", "slug", request.match_info["slug"],
               {"mascotte_nom": texte(d, "mascotte_nom", 80) or None, "mascotte_avatar": avatar or None})
        return await self.api_clans(request)

    async def api_clan_message(self, request):
        d = await request.json()
        contenu = texte(d, "contenu", 2000)
        if not contenu:
            raise Erreur("message vide.")
        clan = db.un("SELECT * FROM clans WHERE slug = ?", request.match_info["slug"])
        try:
            await self.exiger_bot().parler_en_clan(clan, contenu=contenu)
        except RuntimeError as e:
            raise Erreur(str(e))
        return web.json_response({"ok": True})

    async def api_mascotte(self, request):
        d = await request.json()
        nom, avatar = texte(d, "nom", 32), texte(d, "avatar_url", 500)
        if avatar and not avatar.startswith("https://"):
            raise Erreur("l'avatar doit être un lien https://")
        ancien = db.reglage("mascotte", {})
        db.definir("mascotte", {"nom": nom, "avatar_url": avatar})
        resultats = await self.exiger_bot().appliquer_mascotte(
            nom, avatar if avatar != ancien.get("avatar_url") else None)
        return web.json_response({"ok": True, "resultats": resultats})

    # --- structure du serveur Discord ------------------------------------------------------

    async def api_structure(self, request):
        from .bot import PERMS_QUOTIDIEN, PERMS_STRUCTURE
        bot = self.bot
        pret = bool(bot and bot.is_ready() and bot.guild)
        salons = db.reglage("salons") or {}
        return web.json_response({
            "bot_pret": pret,
            "admin": pret and bot.guild.me.guild_permissions.administrator,
            "manque_quotidien": bot.permissions_manquantes(PERMS_QUOTIDIEN) if pret else [],
            "manque_structure": bot.permissions_manquantes(PERMS_STRUCTURE) if pret else [],
            "salons": {k: {"id": str(v), "nom": (bot.guild.get_channel(v).name if pret and bot.guild.get_channel(v) else None)}
                       for k, v in salons.items()},
            "invitation": bot.lien_invitation() if bot and config.DISCORD_CLIENT_ID else None,
            "invitation_structure": bot.lien_invitation(structure=True) if bot and config.DISCORD_CLIENT_ID else None,
        })

    async def api_structure_creer(self, request):
        try:
            rapport = await self.exiger_bot().creer_structure()
        except PermissionError as e:
            raise Erreur("permissions manquantes pour le bot : " + ", ".join(e.args[0]))
        return web.json_response({"rapport": rapport or ["tout était déjà en place"]})

    # --- application --------------------------------------------------------------------

    def application(self):
        app = web.Application(middlewares=[self.securite], client_max_size=64 * 1024)
        r = app.router
        r.add_get("/", self.accueil)
        r.add_get("/login", self.connexion)
        r.add_get("/callback", self.retour_discord)
        r.add_get("/logout", self.deconnexion)
        r.add_static("/static/", STATIC)
        r.add_get("/api/moi", self.api_moi)
        r.add_get("/api/tableau", self.api_tableau)
        r.add_get("/api/annonces", self.api_annonces)
        r.add_post("/api/annonces", self.api_annonce_creer)
        r.add_delete("/api/annonces/{id:\\d+}", self.api_annonce_supprimer)
        r.add_post("/api/annonces/{id:\\d+}/relancer", self.api_annonce_relancer)
        r.add_get("/api/classements", self.api_classements)
        r.add_post("/api/classements/rafraichir", self.api_classements_rafraichir)
        r.add_get("/api/tickets", self.api_tickets)
        r.add_post("/api/tickets/{id:\\d+}/fermer", self.api_ticket_fermer)
        r.add_get("/api/faq", self.api_faq)
        r.add_post("/api/faq", self.api_faq_enregistrer)
        r.add_put("/api/faq/{id:\\d+}", self.api_faq_enregistrer)
        r.add_delete("/api/faq/{id:\\d+}", self.api_faq_supprimer)
        r.add_post("/api/faq/publier", self.api_faq_publier)
        r.add_get("/api/suggestions", self.api_suggestions)
        r.add_post("/api/suggestions/{id:\\d+}/statut", self.api_suggestion_statut)
        r.add_get("/api/clans", self.api_clans)
        r.add_put("/api/clans/{slug:[a-z]+}", self.api_clan_maj)
        r.add_post("/api/clans/{slug:[a-z]+}/message", self.api_clan_message)
        r.add_put("/api/mascotte", self.api_mascotte)
        r.add_get("/api/structure", self.api_structure)
        r.add_post("/api/structure/creer", self.api_structure_creer)
        return app


class Erreur(Exception):
    """Erreur affichée telle quelle dans le panneau."""


def texte(d, cle, longueur_max):
    v = d.get(cle)
    return str(v).strip()[:longueur_max] if v is not None else ""


async def demarrer(bot):
    if MODE_DEV:
        log.warning("MODE APERÇU LOCAL : connexion sans Discord activée (DEV_ADMIN=1).")
    runner = web.AppRunner(Panneau(bot).application())
    await runner.setup()
    await web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT).start()
    log.info("Panneau : %s (écoute sur %s:%s)", config.PUBLIC_URL, config.WEB_HOST, config.WEB_PORT)
    return runner
