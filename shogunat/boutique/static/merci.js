// Page « merci » : affiche l'état de la commande tel que le serveur le connaît (elle ne valide jamais rien elle-même).
"use strict";

const euros = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" });
const ETATS = {
  en_attente: ["Confirmation du paiement…", "On attend la confirmation de Stripe. Cela prend en général quelques secondes.", "En attente", "attente"],
  attente_paiement: ["Paiement en cours", "Ton moyen de paiement demande un peu plus de temps. Tu recevras ton achat dès sa confirmation.", "En cours", "attente"],
  payee: ["Paiement reçu, merci !", "Ton achat est en route vers le serveur. Si le serveur dort, il sera livré à son réveil.", "Payé", "attente"],
  livree: ["Livré dans l'archipel ⛩", "Ton achat est activé en jeu. Merci de faire vivre le Shogunat !", "Livré", "ok"],
  expiree: ["Paiement expiré", "La page de paiement a expiré : rien n'a été débité. Tu peux recommencer depuis la boutique.", "Expiré", "erreur"],
  echec: ["Paiement refusé", "Ta banque a refusé le paiement : rien n'a été débité.", "Refusé", "erreur"],
  anomalie: ["Vérification nécessaire", "Le paiement doit être vérifié par le staff. Ouvre un ticket dans #doléances sur Discord.", "À vérifier", "erreur"],
  remboursee: ["Commande remboursée", "Cette commande a été remboursée.", "Remboursé", "erreur"],
  litige: ["Paiement contesté", "Ce paiement fait l'objet d'une contestation bancaire.", "Litige", "erreur"],
};
const FINAUX = new Set(["livree", "expiree", "echec", "anomalie", "remboursee", "litige"]);
const id = new URLSearchParams(location.search).get("commande") || "";
let essais = 0;
let minuteur = null;

function texte(idElement, valeur) { document.getElementById(idElement).textContent = valeur; }

function afficher(c) {
  const [titre, detail, libelle, classe] = ETATS[c.statut] || ETATS.en_attente;
  texte("titre", titre);
  texte("detail", detail);
  const pastille = document.getElementById("pastille");
  pastille.textContent = libelle;
  pastille.className = `pastille ${classe}`;
  document.getElementById("mascotte").src = classe === "erreur" ? "/boutique/static/img/sakura-triste.webp" : "/boutique/static/img/sakura.webp";
  const recap = document.getElementById("recap");
  recap.replaceChildren(...c.lignes.map(l => {
    const ligne = document.createElement("div");
    const nom = document.createElement("span"); nom.textContent = l.quantite > 1 ? `${l.nom} × ${l.quantite}` : l.nom;
    const prix = document.createElement("span"); prix.textContent = euros.format(l.prix_centimes * l.quantite / 100);
    ligne.append(nom, prix);
    return ligne;
  }));
  const total = document.createElement("div");
  total.className = "total-recap";
  const a = document.createElement("span"); a.textContent = "Total";
  const b = document.createElement("span"); b.textContent = euros.format(c.total_centimes / 100);
  total.append(a, b);
  recap.append(total);
  if (c.statut !== "en_attente") {
    try { localStorage.removeItem("shogunat-panier"); } catch (e) { /* stockage indisponible */ }
  }
}

async function suivre() {
  if (!/^[A-Za-z0-9_-]{20,40}$/.test(id)) { texte("titre", "Commande introuvable"); texte("detail", "Ce lien ne correspond à aucune commande."); return; }
  try {
    const r = await fetch(`/boutique/api/commandes/${id}`, { credentials: "same-origin" });
    if (r.status === 401) { texte("titre", "Connecte-toi pour voir ta commande"); texte("detail", "Retourne à la boutique et connecte-toi avec ton compte Microsoft."); return; }
    if (!r.ok) { texte("titre", "Commande introuvable"); texte("detail", "Ce lien ne correspond à aucune de tes commandes."); return; }
    const c = await r.json();
    afficher(c);
    if (FINAUX.has(c.statut)) return;
  } catch (e) { /* réseau capricieux : on réessaie */ }
  essais += 1;
  if (essais < 60) minuteur = setTimeout(suivre, essais < 10 ? 2000 : 5000);
  else texte("detail", "La confirmation tarde. Pas d'inquiétude : ton achat sera livré dès qu'elle arrive. Tu peux fermer cette page.");
}

async function apercu() {
  try {
    const cat = await (await fetch("/boutique/api/catalogue")).json();
    if (!cat.apercu) return;
    const bouton = document.getElementById("simuler");
    bouton.hidden = false;
    bouton.addEventListener("click", async () => {
      await fetch(`/boutique/api/dev/payer/${id}`, { method: "POST", headers: { "X-Boutique": "1" }, credentials: "same-origin" });
      bouton.hidden = true;
      clearTimeout(minuteur);
      essais = 0;
      suivre();
    });
  } catch (e) { /* hors aperçu : rien à faire */ }
}

suivre();
apercu();
