"""Étapes 2 à 5 — sécurité de la boutique : sessions, connexion Microsoft, commandes, paiement Stripe, livraison.

Les appels externes (Microsoft, Xbox, Mojang, Stripe) sont remplacés par de faux serveurs :
aucun test ne contacte Internet, aucune vraie clé n'est utilisée.
"""
import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tests  # noqa: F401  (base temporaire + clés factices)
from shogunat import config, db
from shogunat import web as site
from shogunat.boutique import catalogue, livraison, microsoft, paiement, securite as sec

config.BOUTIQUE_ACTIVE = True
config.PANEL_ACTIF = True
UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "99999999-8888-7777-6666-555555555555"
ORIGINE = "https://boutique.example.test"


def cookie_session(uuid=UUID_A, nom="Kenji_", exp_dans=3600, usage="session"):
    return sec.signer({"uuid": uuid, "nom": nom, "exp": time.time() + exp_dans}, usage)


def entetes_post(origine=ORIGINE, session=None):
    h = {"X-Boutique": "1", "Origin": origine, "Content-Type": "application/json"}
    if session:
        h["Cookie"] = f"{sec.COOKIE_SESSION}={session}"
    return h


def signature_stripe(corps: bytes, horodatage=None, secret=None):
    t = int(horodatage or time.time())
    sig = hmac.new((secret or config.STRIPE_WEBHOOK_SECRET).encode(), f"{t}.".encode() + corps, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(site.construire_application(None)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()


class TestSessionsEtEntetes(Base):
    async def test_entetes_de_securite(self):
        r = await self.client.get("/boutique/")
        self.assertEqual(r.status, 200)
        for nom in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "Strict-Transport-Security"):
            self.assertIn(nom, r.headers)
        self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", r.headers["Content-Security-Policy"].split("script-src")[1].split(";")[0])
        r = await self.client.get("/boutique/api/catalogue")
        self.assertEqual(r.headers.get("Cache-Control"), "no-store")

    async def test_cookies_falsifies_refuses(self):
        valide = cookie_session()
        brut, sig = valide.rsplit(".", 1)
        cas = {
            "signature modifiée": f"{brut}.{'0' * len(sig)}",
            "contenu modifié": cookie_session(uuid=UUID_B).rsplit(".", 1)[0] + "." + sig,
            "expiré": cookie_session(exp_dans=-10),
            "cookie OAuth utilisé comme session": cookie_session(usage="oauth"),
            "cookie du panneau d'admin": site._signer({"uuid": UUID_A, "nom": "Kenji_", "exp": time.time() + 3600}),
            "pseudo piégé": cookie_session(nom="Kenji;op Kenji"),
            "uuid piégé": cookie_session(uuid="../../etc/passwd"),
            "charabia": "abc", "vide": "",
        }
        for nom, valeur in cas.items():
            with self.subTest(nom):
                r = await self.client.get("/boutique/api/moi", headers={"Cookie": f"{sec.COOKIE_SESSION}={valeur}"})
                self.assertIsNone((await r.json())["joueur"])
        r = await self.client.get("/boutique/api/moi", headers={"Cookie": f"{sec.COOKIE_SESSION}={valide}"})
        self.assertEqual((await r.json())["joueur"]["nom"], "Kenji_")

    async def test_routes_de_test_absentes_hors_apercu(self):
        cid = catalogue.creer_commande(UUID_A, "Kenji_", [{"id": "don-2", "nom": "x", "prix_centimes": 200, "quantite": 1}], 200)
        r = await self.client.post(f"/boutique/api/dev/payer/{cid}", headers=entetes_post(session=cookie_session()))
        self.assertEqual(r.status, 404)
        self.assertEqual(db.un("SELECT statut FROM boutique_commandes WHERE id = ?", cid)["statut"], "en_attente")

    async def test_panneau_admin_toujours_protege(self):
        r = await self.client.get("/api/tableau")
        self.assertEqual(r.status, 401)


class TestConnexionMicrosoft(Base):
    async def test_redirection_vers_microsoft(self):
        r = await self.client.get("/boutique/connexion", allow_redirects=False)
        self.assertEqual(r.status, 302)
        url = urlsplit(r.headers["Location"])
        q = parse_qs(url.query)
        self.assertEqual(url.netloc, "login.microsoftonline.com")
        self.assertIn("/consumers/", url.path)
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["redirect_uri"], [f"{ORIGINE}/boutique/connexion/retour"])
        self.assertGreaterEqual(len(q["state"][0]), 40)
        cookie = r.headers["Set-Cookie"]
        for attendu in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/boutique"):
            self.assertIn(attendu, cookie)

    async def _demarrer(self):
        r = await self.client.get("/boutique/connexion", allow_redirects=False)
        etat = parse_qs(urlsplit(r.headers["Location"]).query)["state"][0]
        cookie = r.cookies[sec.COOKIE_OAUTH].value
        return etat, cookie

    async def test_retour_avec_mauvais_etat_refuse(self):
        etat, cookie = await self._demarrer()
        with mock.patch.object(microsoft, "profil_depuis_code") as profil:
            for nom, (q, c) in {"état falsifié": ("state=AUTRE&code=xxxxxxxxxxxx", cookie),
                                "sans cookie": (f"state={etat}&code=xxxxxxxxxxxx", None),
                                "cookie d'un autre": (f"state={etat}&code=xxxxxxxxxxxx", cookie_session(usage="oauth"))}.items():
                with self.subTest(nom):
                    h = {"Cookie": f"{sec.COOKIE_OAUTH}={c}"} if c else {}
                    r = await self.client.get(f"/boutique/connexion/retour?{q}", headers=h, allow_redirects=False)
                    self.assertEqual(r.status, 400)
                    self.assertNotIn(sec.COOKIE_SESSION, r.cookies)
            profil.assert_not_called()

    async def test_retour_valide_cree_la_session_et_jette_le_cookie_oauth(self):
        etat, cookie = await self._demarrer()

        async def faux_profil(code, verif):
            self.assertEqual(code, "code-microsoft-12345")
            self.assertGreaterEqual(len(verif), 43)
            return UUID_A, "Kenji_"
        with mock.patch.object(microsoft, "profil_depuis_code", side_effect=faux_profil):
            r = await self.client.get(f"/boutique/connexion/retour?state={etat}&code=code-microsoft-12345",
                                      headers={"Cookie": f"{sec.COOKIE_OAUTH}={cookie}"}, allow_redirects=False)
        self.assertEqual(r.status, 302)
        self.assertEqual(sec.verifier(r.cookies[sec.COOKIE_SESSION].value, "session")["nom"], "Kenji_")
        self.assertEqual(r.cookies[sec.COOKIE_OAUTH].value, "")  # cookie OAuth effacé : pas de rejeu possible

    async def test_message_d_erreur_echappe(self):
        etat, cookie = await self._demarrer()
        with mock.patch.object(microsoft, "profil_depuis_code",
                               side_effect=microsoft.ErreurConnexion('<script>alert("x")</script>')):
            r = await self.client.get(f"/boutique/connexion/retour?state={etat}&code=code-microsoft-12345",
                                      headers={"Cookie": f"{sec.COOKIE_OAUTH}={cookie}"})
        texte = await r.text()
        self.assertNotIn("<script>alert", texte)
        self.assertIn("&lt;script&gt;", texte)


class FauxMicrosoft:
    """Faux serveurs Microsoft / Xbox / Mojang pour tester toute la chaîne sans Internet."""

    def __init__(self):
        self.xsts = (200, {"Token": "xsts", "DisplayClaims": {"xui": [{"uhs": "uhs123"}]}})
        self.minecraft = (200, {"access_token": "mc"})
        self.profil = (200, {"id": "11111111222233334444555555555555", "name": "Kenji_"})
        self.recu = {}

    def application(self):
        async def jeton(r):
            self.recu["jeton"] = dict(await r.post())
            return web.json_response({"access_token": "ms"})

        async def xbl(r):
            self.recu["xbl"] = await r.json()
            return web.json_response({"Token": "xbl", "DisplayClaims": {"xui": [{"uhs": "uhs123"}]}})

        async def xsts(r):
            return web.json_response(self.xsts[1], status=self.xsts[0])

        async def mc(r):
            self.recu["minecraft"] = await r.json()
            return web.json_response(self.minecraft[1], status=self.minecraft[0])

        async def profil(r):
            self.recu["auth"] = r.headers.get("Authorization")
            return web.json_response(self.profil[1], status=self.profil[0])
        app = web.Application()
        app.router.add_post("/jeton", jeton)
        app.router.add_post("/xbl", xbl)
        app.router.add_post("/xsts", xsts)
        app.router.add_post("/mc", mc)
        app.router.add_get("/profil", profil)
        return app


class TestChaineMicrosoft(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.faux = FauxMicrosoft()
        self.serveur = TestServer(self.faux.application())
        await self.serveur.start_server()
        base = str(self.serveur.make_url(""))
        self.urls = mock.patch.dict(microsoft.URLS, {"jeton": base + "/jeton", "xbl": base + "/xbl", "xsts": base + "/xsts",
                                                     "minecraft": base + "/mc", "profil": base + "/profil"})
        self.urls.start()

    async def asyncTearDown(self):
        self.urls.stop()
        await self.serveur.close()

    async def test_chaine_complete(self):
        uuid, nom = await microsoft.profil_depuis_code("code-microsoft-12345", "v" * 64)
        self.assertEqual((uuid, nom), (UUID_A, "Kenji_"))
        self.assertEqual(self.faux.recu["jeton"]["code_verifier"], "v" * 64)  # PKCE bien transmis
        self.assertEqual(self.faux.recu["minecraft"]["identityToken"], "XBL3.0 x=uhs123;xsts")
        self.assertEqual(self.faux.recu["auth"], "Bearer mc")

    async def test_erreurs_traduites(self):
        cas = {
            "pas de profil Xbox": ("xsts", (401, {"XErr": 2148916233}), "profil Xbox"),
            "compte mineur": ("xsts", (401, {"XErr": 2148916238}), "famille"),
            "application non validée par Mojang": ("minecraft", (403, {}), "validation de Mojang"),
            "Minecraft non acheté": ("profil", (404, {}), "ne possède pas Minecraft"),
            "pseudo piégé": ("profil", (200, {"id": "11111111222233334444555555555555", "name": "a\nop a"}), "inattendu"),
            "uuid piégé": ("profil", (200, {"id": "../../x", "name": "Kenji_"}), "inattendu"),
        }
        for nom, (etape, reponse, attendu) in cas.items():
            with self.subTest(nom):
                ancien = getattr(self.faux, etape)
                setattr(self.faux, etape, reponse)
                try:
                    with self.assertRaises(microsoft.ErreurConnexion) as e:
                        await microsoft.profil_depuis_code("code-microsoft-12345", "v" * 64)
                    self.assertIn(attendu, str(e.exception))
                finally:
                    setattr(self.faux, etape, ancien)

    async def test_code_absurde_refuse_sans_appel(self):
        for code in ("", "x", "a" * 5000, None):
            with self.subTest(code=code):
                with self.assertRaises(microsoft.ErreurConnexion):
                    await microsoft.profil_depuis_code(code, "v" * 64)
        self.assertNotIn("jeton", self.faux.recu)


class TestCommande(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.stripe = mock.patch("stripe.checkout.Session.create",
                                 return_value=SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_test_1", id=None))
        self.cree = self.stripe.start()
        self.compteur = 0

        def fausse_session(**kw):
            self.compteur += 1
            unique = f"{time.time_ns()}_{self.compteur}"
            return SimpleNamespace(url=f"https://checkout.stripe.com/c/pay/cs_test_{unique}", id=f"cs_test_{unique}")
        self.cree.side_effect = fausse_session
        sec.Limiteur.autoriser = TestCommande._autoriser_original

    _autoriser_original = sec.Limiteur.autoriser

    async def asyncTearDown(self):
        self.stripe.stop()
        await super().asyncTearDown()

    async def commander(self, corps, **kw):
        return await self.client.post("/boutique/api/commande", data=json.dumps(corps),
                                      headers=entetes_post(session=kw.get("session", cookie_session()),
                                                           origine=kw.get("origine", ORIGINE)))

    async def test_commande_normale_prix_de_la_base(self):
        r = await self.commander({"lignes": [{"id": "grade-jade", "quantite": 1, "prix_centimes": 1}], "accepte_conditions": True})
        self.assertEqual(r.status, 200)
        self.assertTrue((await r.json())["url"].startswith("https://checkout.stripe.com/"))
        params = self.cree.call_args.kwargs
        self.assertEqual(params["line_items"][0]["price_data"]["unit_amount"], 1999)
        self.assertEqual(params["api_key"], config.STRIPE_SECRET_KEY)
        self.assertTrue(params["success_url"].startswith(f"{ORIGINE}/boutique/merci?commande="))
        self.assertEqual(params["metadata"]["uuid"], UUID_A)

    async def test_protections(self):
        bon = {"lignes": [{"id": "don-2", "quantite": 1}], "accepte_conditions": True}
        r = await self.client.post("/boutique/api/commande", data=json.dumps(bon), headers=entetes_post())
        self.assertEqual(r.status, 401, "sans session")
        h = entetes_post(session=cookie_session())
        del h["X-Boutique"]
        r = await self.client.post("/boutique/api/commande", data=json.dumps(bon), headers=h)
        self.assertEqual(r.status, 403, "sans en-tête anti-CSRF")
        r = await self.commander(bon, origine="https://site-pirate.example")
        self.assertEqual(r.status, 403, "autre origine")
        r = await self.commander({"lignes": bon["lignes"]})
        self.assertEqual(r.status, 400, "conditions non acceptées")
        r = await self.commander({"lignes": bon["lignes"], "accepte_conditions": "true"})
        self.assertEqual(r.status, 400, "conditions acceptées avec un texte au lieu d'un vrai oui")
        r = await self.commander({"lignes": [{"id": "grade-sakura", "quantite": 5}], "accepte_conditions": True})
        self.assertEqual(r.status, 400, "quantité interdite")
        r = await self.client.post("/boutique/api/commande", data="pas du json", headers=entetes_post(session=cookie_session()))
        self.assertEqual(r.status, 400, "corps illisible")
        self.cree.assert_not_called()

    async def test_limite_de_commandes(self):
        bon = {"lignes": [{"id": "don-2", "quantite": 1}], "accepte_conditions": True}
        session = cookie_session(uuid=UUID_B, nom="Hana")
        codes = [(await self.commander(bon, session=session)).status for _ in range(12)]
        self.assertEqual(codes[:10], [200] * 10)
        self.assertEqual(codes[10:], [429, 429])

    async def test_commande_d_un_autre_invisible(self):
        r = await self.commander({"lignes": [{"id": "don-5", "quantite": 1}], "accepte_conditions": True})
        cid = urlsplit((await r.json())["url"]).path and self.cree.call_args.kwargs["client_reference_id"]
        r = await self.client.get(f"/boutique/api/commandes/{cid}", headers={"Cookie": f"{sec.COOKIE_SESSION}={cookie_session()}"})
        self.assertEqual(r.status, 200)
        r = await self.client.get(f"/boutique/api/commandes/{cid}",
                                  headers={"Cookie": f"{sec.COOKIE_SESSION}={cookie_session(uuid=UUID_B, nom='Hana')}"})
        self.assertEqual(r.status, 404)

    async def test_reponse_stripe_douteuse_refusee(self):
        self.cree.side_effect = None
        self.cree.return_value = SimpleNamespace(url="https://pirate.example/pay", id="cs_x")
        r = await self.commander({"lignes": [{"id": "don-2", "quantite": 1}], "accepte_conditions": True})
        self.assertEqual(r.status, 503)


class TestWebhook(Base):
    def nouvelle_commande(self, total=499, session_id="cs_test_wh"):
        cid = catalogue.creer_commande(UUID_A, "Kenji_", [{"id": "grade-sakura", "nom": "Grade Sakura",
                                                           "prix_centimes": total, "quantite": 1}], total)
        db.maj("boutique_commandes", "id", cid, {"stripe_session": f"{session_id}_{cid[:6]}"})
        return cid, f"{session_id}_{cid[:6]}"

    def evenement(self, cid, sid, eid=None, type_="checkout.session.completed", **objet):
        obj = {"id": sid, "client_reference_id": cid, "payment_status": "paid", "amount_total": 499, "currency": "eur",
               "metadata": {"commande": cid}, "payment_intent": f"pi_{cid[:6]}"}
        obj.update(objet)
        return json.dumps({"id": eid or f"evt_{cid[:8]}_{time.time_ns()}", "type": type_, "data": {"object": obj}}).encode()

    async def envoyer(self, corps, signature=None):
        return await self.client.post("/boutique/stripe/webhook", data=corps,
                                      headers={"Stripe-Signature": signature or signature_stripe(corps)})

    def statut(self, cid):
        return db.un("SELECT statut FROM boutique_commandes WHERE id = ?", cid)["statut"]

    async def test_paiement_valide(self):
        cid, sid = self.nouvelle_commande()
        r = await self.envoyer(self.evenement(cid, sid))
        self.assertEqual(r.status, 200)
        self.assertEqual(self.statut(cid), "payee")

    async def test_signatures_refusees(self):
        cid, sid = self.nouvelle_commande()
        corps = self.evenement(cid, sid)
        cas = {"sans signature": "", "mauvais secret": signature_stripe(corps, secret="whsec_pirate"),
               "trop ancienne (rejeu)": signature_stripe(corps, horodatage=time.time() - 3600),
               "contenu modifié après signature": signature_stripe(corps.replace(b"499", b"999"))}
        for nom, sig in cas.items():
            with self.subTest(nom):
                r = await self.client.post("/boutique/stripe/webhook", data=corps if nom != "contenu modifié après signature"
                                           else corps, headers={"Stripe-Signature": sig} if sig else {})
                self.assertEqual(r.status, 400)
        self.assertEqual(self.statut(cid), "en_attente")

    async def test_evenement_rejoue_traite_une_fois(self):
        cid, sid = self.nouvelle_commande()
        corps = self.evenement(cid, sid, eid="evt_unique_rejeu")
        await self.envoyer(corps)
        db.maj("boutique_commandes", "id", cid, {"statut": "livree"})
        r = await self.envoyer(corps)
        self.assertEqual((await r.json())["message"], "déjà traité")
        self.assertEqual(self.statut(cid), "livree")  # pas de seconde livraison

    async def test_anomalies_non_livrees(self):
        for nom, objet in {"montant différent": {"amount_total": 1}, "devise différente": {"currency": "usd"},
                           "métadonnées d'une autre commande": {"metadata": {"commande": "autre"}}}.items():
            with self.subTest(nom):
                cid, sid = self.nouvelle_commande()
                await self.envoyer(self.evenement(cid, sid, **objet))
                self.assertEqual(self.statut(cid), "anomalie")

    async def test_session_d_une_autre_commande_ignoree(self):
        cid, sid = self.nouvelle_commande()
        await self.envoyer(self.evenement(cid, "cs_test_pirate"))
        self.assertEqual(self.statut(cid), "en_attente")

    async def test_paiement_differe_puis_reussi(self):
        cid, sid = self.nouvelle_commande()
        await self.envoyer(self.evenement(cid, sid, payment_status="unpaid"))
        self.assertEqual(self.statut(cid), "attente_paiement")
        await self.envoyer(self.evenement(cid, sid, type_="checkout.session.async_payment_succeeded"))
        self.assertEqual(self.statut(cid), "payee")

    async def test_expiration_et_remboursement(self):
        cid, sid = self.nouvelle_commande()
        await self.envoyer(self.evenement(cid, sid, type_="checkout.session.expired", payment_status="unpaid"))
        self.assertEqual(self.statut(cid), "expiree")
        cid, sid = self.nouvelle_commande()
        await self.envoyer(self.evenement(cid, sid))
        rembourse = json.dumps({"id": f"evt_r_{cid}", "type": "charge.refunded",
                                "data": {"object": {"payment_intent": f"pi_{cid[:6]}", "refunded": True}}}).encode()
        await self.envoyer(rembourse)
        self.assertEqual(self.statut(cid), "remboursee")


class TestLivraison(unittest.IsolatedAsyncioTestCase):
    def test_injections_refusees(self):
        cas = {"pseudo multiligne": ("say {joueur}", "a\nop a", UUID_A, 1),
               "modèle multiligne": ("say a\nop {joueur}", "Kenji_", UUID_A, 1),
               "uuid invalide": ("say {uuid}", "Kenji_", "x", 1),
               "quantité négative": ("say {quantite}", "Kenji_", UUID_A, -1),
               "quantité texte": ("say {quantite}", "Kenji_", UUID_A, "1"),
               "commande vide": ("   ", "Kenji_", UUID_A, 1)}
        for nom, args in cas.items():
            with self.subTest(nom):
                with self.assertRaises(livraison.CommandeInvalide):
                    livraison.preparer(*args)

    def test_substitution_stricte(self):
        self.assertEqual(livraison.preparer("/give {joueur} diamond {quantite}", "Kenji_", UUID_A, 3), "give Kenji_ diamond 3")
        # aucun accès aux attributs Python, contrairement à str.format
        self.assertEqual(livraison.preparer("say {joueur.__class__} {0}", "Kenji_", UUID_A, 1), "say {joueur.__class__} {0}")
        self.assertIn('{"text"', livraison.preparer(catalogue.MERCI, "Kenji_", UUID_A, 1))

    async def test_reprise_sans_doublon(self):
        db.executer("UPDATE boutique_produits SET commandes = ? WHERE id = 'grade-lanterne'",
                    json.dumps(["say un {joueur}", "say deux {joueur}", "say trois {joueur}"]))
        cid = catalogue.creer_commande(UUID_A, "Kenji_", [{"id": "grade-lanterne", "nom": "x", "prix_centimes": 999,
                                                           "quantite": 1}], 999)
        db.maj("boutique_commandes", "id", cid, {"statut": "payee"})
        envoyees = []

        async def rcon_capricieux(texte):
            if texte.startswith("say deux") and "deux" not in [e.split()[1] for e in envoyees] and len(envoyees) == 1 \
                    and not getattr(rcon_capricieux, "deja", False):
                rcon_capricieux.deja = True
                raise livraison.minecraft.RconErreur("serveur éteint")
            envoyees.append(texte)
        commande = db.un("SELECT * FROM boutique_commandes WHERE id = ?", cid)
        with self.assertRaises(livraison.minecraft.RconErreur):
            await livraison.livrer(commande, envoyer=rcon_capricieux)
        commande = db.un("SELECT * FROM boutique_commandes WHERE id = ?", cid)
        self.assertTrue(await livraison.livrer(commande, envoyer=rcon_capricieux))
        self.assertEqual(envoyees, ["say un Kenji_", "say deux Kenji_", "say trois Kenji_"])  # « un » jamais rejoué
        self.assertEqual(db.un("SELECT statut FROM boutique_commandes WHERE id = ?", cid)["statut"], "livree")


if __name__ == "__main__":
    unittest.main()
