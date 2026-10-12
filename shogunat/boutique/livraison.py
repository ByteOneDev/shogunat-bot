"""Livraison en jeu des commandes payées, par RCON.

Chaque produit a une liste de commandes (réglables plus tard dans le panneau), avec trois variables seulement :
{joueur} (pseudo vérifié), {uuid} (UUID vérifié), {quantite} (entier). Le pseudo et l'UUID viennent de la
connexion Microsoft et sont revalidés ici : impossible d'y glisser une seconde commande.
Chaque commande exécutée est mémorisée : en cas de coupure, on reprend sans rien exécuter deux fois.
"""
import asyncio
import json
import logging
import re

from .. import config, db, minecraft
from . import securite

log = logging.getLogger("shogunat.boutique.livraison")
VARIABLE = re.compile(r"\{(joueur|uuid|quantite)\}")
INTERDITS = re.compile(r"[\r\n\x00]")
TENTATIVES_MAX = 20


class CommandeInvalide(ValueError):
    pass


def preparer(modele, joueur_nom, joueur_uuid, quantite):
    """Remplit un modèle de commande. Refuse tout ce qui pourrait enchaîner une autre commande."""
    if not securite.joueur_valide(joueur_uuid, joueur_nom):
        raise CommandeInvalide("joueur invalide")
    if not isinstance(modele, str) or not isinstance(quantite, int) or isinstance(quantite, bool) or not 1 <= quantite <= 99:
        raise CommandeInvalide("modèle ou quantité invalide")
    valeurs = {"joueur": joueur_nom, "uuid": joueur_uuid, "quantite": str(quantite)}
    commande = VARIABLE.sub(lambda m: valeurs[m.group(1)], modele).strip().lstrip("/")
    if not commande or len(commande) > 1000 or INTERDITS.search(commande):
        raise CommandeInvalide("commande vide, trop longue ou multiligne")
    return commande


def etapes(commande):
    """Liste ordonnée des commandes à exécuter pour une commande payée : [(clé unique, texte)]."""
    resultat = []
    for i, ligne in enumerate(json.loads(commande["lignes"])):
        p = db.un("SELECT commandes FROM boutique_produits WHERE id = ?", ligne["id"])
        modeles = json.loads(p["commandes"]) if p else []
        for j, modele in enumerate(modeles):
            texte = preparer(modele, commande["joueur_nom"], commande["joueur_uuid"], ligne["quantite"])
            resultat.append((f"{i}:{ligne['id']}:{j}", texte))
    return resultat


async def livrer(commande, envoyer=None):
    """Exécute les étapes restantes. Renvoie True si la commande est entièrement livrée."""
    envoyer = envoyer or minecraft.rcon
    faites = set(json.loads(commande["etapes_faites"] or "[]"))
    try:
        a_faire = etapes(commande)
    except (CommandeInvalide, ValueError) as e:
        # inutile de réessayer : il faut corriger le modèle dans le panneau, puis relancer la livraison
        db.maj("boutique_commandes", "id", commande["id"], {"erreur": f"modèle de livraison invalide : {e}",
                                                            "tentatives": TENTATIVES_MAX})
        log.error("Commande %s : modèle de livraison invalide (%s)", commande["id"], e)
        return False
    for cle, texte in a_faire:
        if cle in faites:
            continue
        await envoyer(texte)
        faites.add(cle)
        db.maj("boutique_commandes", "id", commande["id"], {"etapes_faites": json.dumps(sorted(faites))})
    db.maj("boutique_commandes", "id", commande["id"], {"statut": "livree", "livree_le": db.maintenant(), "erreur": None})
    return True


class Livreur:
    """Boucle de fond : livre les commandes payées, réessaie si le serveur Minecraft est éteint."""

    def __init__(self, bot=None, intervalle=30):
        self.bot, self.intervalle = bot, intervalle
        self.reveil = asyncio.Event()
        self.tache = None
        self.rcon_absent_signale = False

    def reveiller(self):
        self.reveil.set()

    async def journal(self, texte):
        if self.bot and self.bot.is_ready():
            try:
                await self.bot.journal(texte)
            except Exception:  # noqa: BLE001 — le journal Discord ne doit jamais bloquer une livraison
                log.warning("Message au journal Discord impossible")

    async def tour(self):
        if not (config.RCON_PORT and config.RCON_PASSWORD):
            if not self.rcon_absent_signale:
                log.warning("RCON non configuré : les commandes payées attendent d'être livrées.")
                self.rcon_absent_signale = True
            return
        for commande in db.tous("SELECT * FROM boutique_commandes WHERE statut = 'payee' AND tentatives < ? "
                                "ORDER BY payee_le LIMIT 20", TENTATIVES_MAX):
            try:
                if await livrer(commande):
                    await self.journal(f"🛒 Achat livré en jeu à **{commande['joueur_nom']}** "
                                       f"({commande['total_centimes'] / 100:.2f} €)")
            except minecraft.RconErreur as e:
                tentatives = commande["tentatives"] + 1
                db.maj("boutique_commandes", "id", commande["id"], {"tentatives": tentatives, "erreur": str(e)[:300]})
                if tentatives == TENTATIVES_MAX:
                    await self.journal(f"⚠️ Achat de **{commande['joueur_nom']}** non livré après {TENTATIVES_MAX} "
                                       "essais : à livrer à la main (panneau, commandes boutique).")
                break  # serveur injoignable : inutile d'insister sur les suivantes

    async def boucle(self):
        while True:
            try:
                await self.tour()
            except Exception:  # noqa: BLE001
                log.exception("Erreur dans la boucle de livraison")
            try:
                await asyncio.wait_for(self.reveil.wait(), timeout=self.intervalle)
            except asyncio.TimeoutError:
                pass
            self.reveil.clear()

    async def demarrer(self, _app=None):
        self.tache = asyncio.create_task(self.boucle())

    async def arreter(self, _app=None):
        if self.tache:
            self.tache.cancel()
