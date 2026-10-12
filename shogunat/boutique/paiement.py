"""Paiement par Stripe Checkout.

Le joueur paie sur la page hébergée par Stripe : aucune donnée de carte ne passe par notre serveur.
Une commande n'est considérée comme payée QUE sur un événement Stripe dont on a vérifié :
la signature (et sa fraîcheur), l'unicité, le montant exact, la devise et la session correspondante.
La page « merci » ne valide jamais rien : elle se contente d'afficher l'état connu du serveur.
"""
import asyncio
import json
import logging
import time

import stripe

from .. import config, db

log = logging.getLogger("shogunat.boutique.paiement")
TOLERANCE_SIGNATURE = 300  # secondes : au-delà, un événement rejoué est refusé
EXPIRATION_SESSION = 1800  # la page de paiement Stripe expire au bout de 30 minutes


class ErreurPaiement(Exception):
    """Message montré au joueur, sans détail technique."""


def mode():
    cle = config.STRIPE_SECRET_KEY
    if cle.startswith(("sk_test_", "rk_test_")):
        return "test"
    if cle.startswith(("sk_live_", "rk_live_")):
        return "reel"
    return None


def configure():
    if not (mode() and config.STRIPE_WEBHOOK_SECRET.startswith("whsec_")):
        return False
    # De vrais paiements exigent une adresse HTTPS (les retours Stripe et les cookies en dépendent)
    return mode() == "test" or config.PUBLIC_URL.startswith("https://")


async def creer_session(cid, joueur, lignes, total):
    """Crée la page de paiement Stripe pour une commande déjà enregistrée. Renvoie son URL."""
    if not configure():
        raise ErreurPaiement("Le paiement n'est pas encore ouvert. Réessaie plus tard.")
    base = config.PUBLIC_URL
    parametres = {
        "mode": "payment",
        "line_items": [{"quantity": l["quantite"], "price_data": {
            "currency": "eur", "unit_amount": l["prix_centimes"],
            "product_data": {"name": l["nom"], "metadata": {"produit": l["id"]}}}} for l in lignes],
        "client_reference_id": cid,
        "metadata": {"commande": cid, "joueur": joueur["nom"], "uuid": joueur["uuid"]},
        "payment_intent_data": {"metadata": {"commande": cid}, "description": f"Shogunat SMP – {joueur['nom']}"},
        "success_url": f"{base}/boutique/merci?commande={cid}",
        "cancel_url": f"{base}/boutique/?paiement=annule",
        "expires_at": int(time.time()) + EXPIRATION_SESSION,
        "locale": "fr",
        "submit_type": "pay",
    }

    def appel():
        return stripe.checkout.Session.create(api_key=config.STRIPE_SECRET_KEY, idempotency_key=f"commande-{cid}",
                                              **parametres)
    try:
        session = await asyncio.to_thread(appel)
    except stripe.StripeError as e:
        log.error("Stripe a refusé la création de la session (%s)", type(e).__name__)
        raise ErreurPaiement("Le paiement n'a pas pu être préparé. Réessaie dans un instant.")
    url, sid = getattr(session, "url", None), getattr(session, "id", None)
    if not (isinstance(url, str) and url.startswith("https://checkout.stripe.com/") and isinstance(sid, str)):
        log.error("Réponse Stripe inattendue pour la commande %s", cid)
        raise ErreurPaiement("Le paiement n'a pas pu être préparé. Réessaie dans un instant.")
    db.maj("boutique_commandes", "id", cid, {"stripe_session": sid})
    return url


def traiter_webhook(corps: bytes, signature: str):
    """Traite un événement Stripe. Renvoie (code HTTP, message, id de commande devenue payée ou None).

    Code 400 : signature invalide (Stripe ne réessaiera pas). Code 200 : traité, ou ignoré volontairement.
    Une erreur inattendue lève une exception : on répond 500 et Stripe réessaiera plus tard.
    """
    try:
        stripe.Webhook.construct_event(corps, signature or "", config.STRIPE_WEBHOOK_SECRET,
                                       tolerance=TOLERANCE_SIGNATURE)
    except (ValueError, stripe.SignatureVerificationError):
        log.warning("Événement Stripe refusé : signature absente, fausse ou trop ancienne")
        return 400, "signature invalide", None
    evt = json.loads(corps)  # signature vérifiée : le contenu vient bien de Stripe
    eid, type_ = evt.get("id"), evt.get("type")
    obj = (evt.get("data") or {}).get("object") or {}
    if not isinstance(eid, str) or not isinstance(type_, str):
        return 400, "événement invalide", None

    c = db.conn()
    payee = None
    with c:  # une seule transaction : si quelque chose échoue, l'événement sera retraité
        if c.execute("INSERT OR IGNORE INTO boutique_evenements (id, type, recu_le) VALUES (?, ?, ?)",
                     (eid, type_, int(time.time()))).rowcount == 0:
            return 200, "déjà traité", None

        if type_.startswith("checkout.session."):
            cid = obj.get("client_reference_id")
            commande = c.execute("SELECT * FROM boutique_commandes WHERE id = ?", (cid,)).fetchone() if cid else None
            if not commande or commande["stripe_session"] != obj.get("id"):
                log.warning("Événement %s pour une commande inconnue ou une autre session", type_)
                return 200, "commande inconnue", None
            statut = commande["statut"]

            if type_ == "checkout.session.expired":
                if statut in ("en_attente", "attente_paiement"):
                    c.execute("UPDATE boutique_commandes SET statut = 'expiree' WHERE id = ?", (cid,))
            elif type_ == "checkout.session.async_payment_failed":
                if statut in ("en_attente", "attente_paiement"):
                    c.execute("UPDATE boutique_commandes SET statut = 'echec' WHERE id = ?", (cid,))
            elif type_ in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
                if obj.get("payment_status") != "paid":
                    # moyen de paiement différé : on attendra async_payment_succeeded
                    if statut == "en_attente":
                        c.execute("UPDATE boutique_commandes SET statut = 'attente_paiement' WHERE id = ?", (cid,))
                elif statut in ("en_attente", "attente_paiement"):
                    anomalies = []
                    if obj.get("amount_total") != commande["total_centimes"]:
                        anomalies.append(f"montant {obj.get('amount_total')} ≠ {commande['total_centimes']}")
                    if obj.get("currency") != commande["devise"]:
                        anomalies.append(f"devise {obj.get('currency')} ≠ {commande['devise']}")
                    if (obj.get("metadata") or {}).get("commande") != cid:
                        anomalies.append("métadonnées incohérentes")
                    if anomalies:
                        log.error("Commande %s : paiement incohérent (%s), non livrée", cid, "; ".join(anomalies))
                        c.execute("UPDATE boutique_commandes SET statut = 'anomalie', erreur = ? WHERE id = ?",
                                  ("; ".join(anomalies), cid))
                    else:
                        c.execute("UPDATE boutique_commandes SET statut = 'payee', payee_le = ?, stripe_paiement = ? "
                                  "WHERE id = ?", (int(time.time()), obj.get("payment_intent"), cid))
                        payee = cid

        elif type_ in ("charge.refunded", "charge.dispute.created"):
            pi = obj.get("payment_intent")
            if isinstance(pi, str):
                if type_ == "charge.dispute.created":
                    c.execute("UPDATE boutique_commandes SET statut = 'litige' WHERE stripe_paiement = ?", (pi,))
                elif obj.get("refunded"):  # remboursement total
                    c.execute("UPDATE boutique_commandes SET statut = 'remboursee' WHERE stripe_paiement = ?", (pi,))
    return 200, "ok", payee
