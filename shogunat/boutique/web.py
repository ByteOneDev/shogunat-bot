"""Routes de la boutique, montées sous /boutique sur le même serveur que le panneau.

Pages : /boutique/  /boutique/merci  /boutique/conditions
Connexion Microsoft : /boutique/connexion → Microsoft → /boutique/connexion/retour
API (JSON) : catalogue, moi, commande, commandes/{id}, mes-commandes ; webhook Stripe : /boutique/stripe/webhook
"""
import html
import logging
import os
import secrets
import time

from aiohttp import web

from .. import config
from . import catalogue, livraison, microsoft, paiement, securite as sec

log = logging.getLogger("shogunat.boutique")
STATIC = config.RACINE / "shogunat" / "boutique" / "static"

# Aperçu local uniquement : connexion fictive et paiement simulé. Jamais actif sur une adresse publique.
MODE_DEV = (os.environ.get("BOUTIQUE_DEV") == "1" and config.WEB_HOST == "127.0.0.1"
            and config.PUBLIC_URL.startswith(("http://localhost", "http://127.0.0.1")))
JOUEUR_DEV = {"uuid": "00000000-0000-4000-8000-000000000000", "nom": "Joueur_test"}


class Boutique:
    def __init__(self, bot=None):
        self.livreur = livraison.Livreur(bot)
        self.limiteur = sec.Limiteur()

    # --- garde commune ------------------------------------------------------------------------

    @web.middleware
    async def garde(self, request, handler):
        api = "/api/" in request.path or request.path.endswith("/webhook")
        try:
            if api and not self.limiteur.autoriser(("api", sec.ip_client(request)), 120):
                reponse = sec.erreur("Trop de requêtes. Patiente une minute.", 429)
            else:
                reponse = await handler(request)
        except web.HTTPException as e:  # redirections et erreurs HTTP : mêmes en-têtes de sécurité
            sec.entetes(e, api=api)
            raise
        except Exception:  # noqa: BLE001 — jamais de détail technique renvoyé au visiteur
            log.exception("Erreur boutique sur %s", request.path)
            reponse = sec.erreur("Erreur inattendue. Réessaie dans un instant.", 500) if api else \
                web.Response(text="Erreur inattendue.", status=500)
        sec.entetes(reponse, api=api)
        return reponse

    def exiger_joueur(self, request):
        joueur = sec.session_joueur(request)
        if not joueur:
            raise web.HTTPUnauthorized(text='{"erreur": "Connecte-toi avec ton compte Microsoft."}',
                                       content_type="application/json")
        return joueur

    def exiger_meme_origine(self, request):
        if not sec.origine_autorisee(request):
            raise web.HTTPForbidden(text='{"erreur": "Requête refusée."}', content_type="application/json")

    # --- pages --------------------------------------------------------------------------------

    async def page(self, request):
        nom = request.match_info.get("page") or "index"
        return web.FileResponse(STATIC / f"{nom}.html")

    def page_message(self, titre, message, statut=400):
        modele = (STATIC / "message.html").read_text("utf-8")
        texte = modele.replace("{{titre}}", html.escape(titre)).replace("{{message}}", html.escape(message))
        return web.Response(text=texte, content_type="text/html", status=statut)

    # --- connexion Microsoft --------------------------------------------------------------------

    async def connexion(self, request):
        if not self.limiteur.autoriser(("connexion", sec.ip_client(request)), 10):
            return self.page_message("Doucement", "Trop de tentatives de connexion. Patiente une minute.", 429)
        if MODE_DEV:
            r = web.HTTPFound("/boutique/?connecte=1")
            sec.poser_cookie(r, sec.COOKIE_SESSION, sec.signer(dict(JOUEUR_DEV, exp=time.time() + sec.DUREE_SESSION),
                                                                "session"), sec.DUREE_SESSION)
            raise r
        if not microsoft.configure():
            return self.page_message("Bientôt", "La connexion à la boutique n'est pas encore ouverte.", 503)
        etat = secrets.token_urlsafe(32)
        verificateur, defi = microsoft.nouveau_pkce()
        r = web.HTTPFound(microsoft.url_autorisation(etat, defi))
        sec.poser_cookie(r, sec.COOKIE_OAUTH, sec.signer({"etat": etat, "verif": verificateur,
                                                          "exp": time.time() + sec.DUREE_OAUTH}, "oauth"), sec.DUREE_OAUTH)
        raise r

    async def retour_microsoft(self, request):
        attente = sec.verifier(request.cookies.get(sec.COOKIE_OAUTH, ""), "oauth")
        etat, code = request.query.get("state", ""), request.query.get("code", "")
        if request.query.get("error"):
            reponse = self.page_message("Connexion annulée", "Tu as annulé la connexion Microsoft.", 400)
        elif not attente or not etat or not secrets.compare_digest(etat, str(attente.get("etat", ""))):
            reponse = self.page_message("Connexion expirée", "Cette connexion a expiré ou n'a pas été demandée ici. "
                                        "Recommence depuis la boutique.", 400)
        else:
            try:
                uuid, nom = await microsoft.profil_depuis_code(code, attente["verif"])
            except microsoft.ErreurConnexion as e:
                reponse = self.page_message("Connexion impossible", str(e), 400)
            else:
                reponse = web.HTTPFound("/boutique/?connecte=1")
                sec.poser_cookie(reponse, sec.COOKIE_SESSION, sec.signer(
                    {"uuid": uuid, "nom": nom, "exp": time.time() + sec.DUREE_SESSION}, "session"), sec.DUREE_SESSION)
                log.info("Connexion boutique : %s", nom)
        sec.effacer_cookie(reponse, sec.COOKIE_OAUTH)  # à usage unique, quoi qu'il arrive
        if isinstance(reponse, web.HTTPException):
            raise reponse
        return reponse

    async def deconnexion(self, request):
        self.exiger_meme_origine(request)
        r = web.json_response({"ok": True})
        sec.effacer_cookie(r, sec.COOKIE_SESSION)
        return r

    # --- API -----------------------------------------------------------------------------------

    async def api_catalogue(self, request):
        donnees = catalogue.catalogue_public()
        donnees["connexion_ouverte"] = MODE_DEV or microsoft.configure()
        donnees["paiement_ouvert"] = paiement.configure() or MODE_DEV
        donnees["mode_test"] = paiement.mode() == "test" or MODE_DEV
        donnees["apercu"] = MODE_DEV
        return web.json_response(donnees)

    async def api_moi(self, request):
        return web.json_response({"joueur": sec.session_joueur(request)})

    async def api_commande(self, request):
        self.exiger_meme_origine(request)
        joueur = self.exiger_joueur(request)
        if not self.limiteur.autoriser(("commande", joueur["uuid"]), 10):
            return sec.erreur("Trop de commandes d'un coup. Patiente une minute.", 429)
        try:
            d = await request.json()
        except ValueError:
            return sec.erreur("Requête invalide.")
        if not isinstance(d, dict) or d.get("accepte_conditions") is not True:
            return sec.erreur("Accepte les conditions de vente pour continuer.")
        try:
            lignes, total = catalogue.calculer_panier(d.get("lignes"))
        except catalogue.PanierInvalide as e:
            return sec.erreur(str(e))
        cid = catalogue.creer_commande(joueur["uuid"], joueur["nom"], lignes, total)
        if MODE_DEV and not paiement.configure():
            return web.json_response({"url": f"/boutique/merci?commande={cid}", "simulation": True})
        try:
            url = await paiement.creer_session(cid, joueur, lignes, total)
        except paiement.ErreurPaiement as e:
            return sec.erreur(str(e), 503)
        return web.json_response({"url": url})

    async def api_une_commande(self, request):
        joueur = self.exiger_joueur(request)
        c = catalogue.commande_du_joueur(request.match_info["id"], joueur["uuid"])
        if not c:
            return sec.erreur("Commande introuvable.", 404)
        return web.json_response(c)

    async def api_mes_commandes(self, request):
        joueur = self.exiger_joueur(request)
        return web.json_response({"commandes": catalogue.commandes_du_joueur(joueur["uuid"])})

    async def api_dev_payer(self, request):
        """Aperçu local uniquement : simule le paiement d'une commande pour tester la page « merci »."""
        if not MODE_DEV:
            raise web.HTTPNotFound()
        self.exiger_meme_origine(request)
        joueur = self.exiger_joueur(request)
        c = catalogue.commande_du_joueur(request.match_info["id"], joueur["uuid"])
        if not c or c["statut"] != "en_attente":
            return sec.erreur("Commande introuvable.", 404)
        from .. import db
        db.maj("boutique_commandes", "id", c["id"], {"statut": "payee", "payee_le": db.maintenant()})
        self.livreur.reveiller()
        return web.json_response({"ok": True})

    async def webhook(self, request):
        corps = await request.read()
        if not paiement.configure():
            return sec.erreur("Paiement non configuré.", 503)
        code, message, payee = paiement.traiter_webhook(corps, request.headers.get("Stripe-Signature", ""))
        if payee:
            self.livreur.reveiller()
        return web.json_response({"message": message}, status=code)

    # --- application ---------------------------------------------------------------------------

    def application(self):
        app = web.Application(middlewares=[self.garde])
        r = app.router
        r.add_get("/", self.page)
        r.add_get("/{page:merci|conditions}", self.page)
        r.add_static("/static/", STATIC)
        r.add_get("/connexion", self.connexion)
        r.add_get("/connexion/retour", self.retour_microsoft)
        r.add_post("/deconnexion", self.deconnexion)
        r.add_get("/api/catalogue", self.api_catalogue)
        r.add_get("/api/moi", self.api_moi)
        r.add_post("/api/commande", self.api_commande)
        r.add_get("/api/commandes/{id:[A-Za-z0-9_-]{20,40}}", self.api_une_commande)
        r.add_get("/api/mes-commandes", self.api_mes_commandes)
        r.add_post("/api/dev/payer/{id:[A-Za-z0-9_-]{20,40}}", self.api_dev_payer)
        r.add_post("/stripe/webhook", self.webhook)
        app.on_startup.append(self.livreur.demarrer)
        app.on_cleanup.append(self.livreur.arreter)
        return app


def application(bot=None):
    catalogue.initialiser()
    if MODE_DEV:
        log.warning("BOUTIQUE EN APERÇU LOCAL : connexion fictive et paiement simulé (BOUTIQUE_DEV=1).")
    return Boutique(bot).application()
