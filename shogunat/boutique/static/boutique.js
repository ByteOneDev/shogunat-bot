// Boutique du Shogunat — catalogue, panier, connexion et paiement.
// Sécurité : tout le texte venant du serveur est inséré avec textContent (jamais innerHTML) ;
// le panier ne contient que des identifiants et des quantités : le serveur recalcule les prix.
"use strict";

const euros = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" });
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const IMAGE = /^[A-Za-z0-9_-]{1,60}\.(webp|png|jpe?g|gif)$/;
const etat = { produits: new Map(), panier: new Map(), joueur: null, catalogue: null };

// --- petits outils ------------------------------------------------------------------------------

function el(tag, attributs = {}, ...enfants) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attributs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "texte") n.textContent = v;
    else if (k === "classe") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const e of enfants) if (e !== null && e !== undefined && e !== false) n.append(e);
  return n;
}

async function api(chemin, options = {}) {
  const init = { method: options.methode || (options.corps !== undefined ? "POST" : "GET"), headers: {}, credentials: "same-origin" };
  if (options.corps !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.headers["X-Boutique"] = "1";
    init.body = JSON.stringify(options.corps);
  } else if (init.method !== "GET") {
    init.headers["X-Boutique"] = "1";
  }
  let r;
  try { r = await fetch("/boutique/" + chemin, init); }
  catch (e) { throw new Error("Connexion au serveur impossible. Vérifie ta connexion et réessaie."); }
  const donnees = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(donnees.erreur || "Erreur inattendue. Réessaie.");
  return donnees;
}

function notifier(message, erreur = false) {
  const n = document.getElementById("notif");
  n.textContent = message;
  n.classList.toggle("erreur", erreur);
  n.classList.add("visible");
  clearTimeout(notifier.minuteur);
  notifier.minuteur = setTimeout(() => n.classList.remove("visible"), erreur ? 6000 : 3200);
}

function lireStockage() {
  try {
    const brut = JSON.parse(localStorage.getItem("shogunat-panier") || "[]");
    return Array.isArray(brut) ? brut : [];
  } catch (e) { return []; }
}

function sauver() {
  try { localStorage.setItem("shogunat-panier", JSON.stringify([...etat.panier].map(([id, quantite]) => ({ id, quantite })))); }
  catch (e) { /* stockage indisponible (navigation privée) : le panier vit juste le temps de la page */ }
}

// --- catalogue ---------------------------------------------------------------------------------

function visuel(p) {
  const v = el("div", { classe: "visuel" });
  if (p.image && IMAGE.test(p.image)) {
    v.append(el("img", { src: "/boutique/static/img/produits/" + encodeURIComponent(p.image), alt: "", loading: "lazy" }));
  } else {
    v.append(el("div", { classe: "visuel-anneau", "aria-hidden": "true" }),
             el("div", { classe: "visuel-kanji", texte: p.kanji || "✿", "aria-hidden": "true" }));
  }
  return v;
}

function carte(p, i) {
  const bouton = el("button", { classe: "bouton bouton-principal", type: "button", "data-id": p.id, onclick: () => ajouter(p.id, bouton) });
  const c = el("article", { classe: "carte", "data-categorie": p.categorie },
    visuel(p),
    el("div", { classe: "carte-corps" },
      el("h3", { texte: p.nom }),
      el("p", { classe: "accroche", texte: p.accroche }),
      el("ul", { classe: "avantages" }, ...(p.avantages || []).map(a => el("li", { texte: a }))),
      el("div", { classe: "carte-pied" }, el("span", { classe: "prix", texte: euros.format(p.prix_centimes / 100) }), bouton)));
  c.style.setProperty("--i", String(i));
  return c;
}

function majBoutonsCartes() {
  document.querySelectorAll(".carte button[data-id]").forEach(b => {
    const p = etat.produits.get(b.dataset.id);
    const plein = p && (etat.panier.get(p.id) || 0) >= p.quantite_max;
    b.textContent = plein ? (p.quantite_max === 1 ? "Dans le panier ✓" : "Maximum atteint") : "Ajouter au panier";
    b.disabled = Boolean(plein);
  });
}

function afficherCatalogue() {
  for (const rayon of ["grades", "cosmetiques", "soutien"]) {
    const grille = document.querySelector(`.grille[data-rayon="${rayon}"]`);
    grille.replaceChildren(...[...etat.produits.values()].filter(p => p.categorie === rayon).map(carte));
  }
  majBoutonsCartes();
}

// --- panier ------------------------------------------------------------------------------------

function ajouter(id, bouton) {
  const p = etat.produits.get(id);
  if (!p) return;
  const q = (etat.panier.get(id) || 0) + 1;
  if (q > p.quantite_max) return;
  etat.panier.set(id, q);
  sauver();
  majPanier();
  if (bouton) { bouton.classList.remove("ajoute"); void bouton.offsetWidth; bouton.classList.add("ajoute"); }
  const compteur = document.getElementById("compteur");
  compteur.classList.remove("rebond"); void compteur.offsetWidth; compteur.classList.add("rebond");
  notifier(`« ${p.nom} » ajouté au panier`);
}

function changer(id, delta) {
  const p = etat.produits.get(id);
  const q = (etat.panier.get(id) || 0) + delta;
  if (!p || q <= 0) etat.panier.delete(id);
  else etat.panier.set(id, Math.min(q, p.quantite_max));
  sauver();
  majPanier();
}

function majPanier() {
  let total = 0, articles = 0;
  const lignes = [];
  for (const [id, q] of etat.panier) {
    const p = etat.produits.get(id);
    if (!p) continue;
    total += p.prix_centimes * q;
    articles += q;
    lignes.push(el("div", { classe: "ligne" },
      el("div", { classe: "ligne-kanji", texte: p.kanji || "✿", "aria-hidden": "true" }),
      el("div", {}, el("strong", { texte: p.nom }), el("small", { texte: euros.format(p.prix_centimes / 100) + (q > 1 ? ` × ${q}` : "") })),
      el("div", { classe: "quantite" },
        el("button", { type: "button", "aria-label": `Retirer un ${p.nom}`, texte: q > 1 ? "−" : "×", onclick: () => changer(id, -1) }),
        p.quantite_max > 1 ? el("span", { texte: String(q) }) : null,
        p.quantite_max > 1 ? el("button", { type: "button", "aria-label": `Ajouter un ${p.nom}`, texte: "+", disabled: q >= p.quantite_max, onclick: () => changer(id, 1) }) : null)));
  }
  const conteneur = document.getElementById("panier-lignes");
  if (lignes.length) conteneur.replaceChildren(...lignes);
  else conteneur.replaceChildren(el("div", { classe: "panier-vide" },
    el("img", { src: "/boutique/static/img/sakura-triste.webp", alt: "", width: "96", height: "96" }),
    el("p", { texte: "Ton panier est vide. Sakura attend tes offrandes…" })));
  document.getElementById("panier-pied").hidden = !lignes.length;
  document.getElementById("total").textContent = euros.format(total / 100);
  const compteur = document.getElementById("compteur");
  compteur.textContent = String(articles);
  compteur.hidden = !articles;
  majZonePaiement();
  majBoutonsCartes();
}

function majZonePaiement() {
  const zone = document.getElementById("zone-paiement");
  const cat = etat.catalogue || {};
  if (!cat.paiement_ouvert) {
    zone.replaceChildren(el("button", { classe: "bouton", type: "button", disabled: true, texte: "Paiement bientôt ouvert" }));
  } else if (!etat.joueur) {
    zone.replaceChildren(boutonMicrosoft("Se connecter avec Microsoft pour payer"));
  } else {
    zone.replaceChildren(el("button", { classe: "bouton bouton-principal", type: "button", id: "payer", texte: "Payer en sécurité", onclick: payer }));
  }
}

function boutonMicrosoft(texte) {
  if (!(etat.catalogue || {}).connexion_ouverte) {
    return el("button", { classe: "bouton", type: "button", disabled: true, texte: "Connexion bientôt ouverte" });
  }
  return el("a", { classe: "bouton bouton-microsoft", href: "/boutique/connexion" },
    el("span", { classe: "logo-ms", "aria-hidden": "true" }, el("i"), el("i"), el("i"), el("i")), texte);
}

function ouvrirPanier(ouvrir) {
  const panier = document.getElementById("panier");
  const voile = document.getElementById("voile");
  panier.classList.toggle("ouvert", ouvrir);
  panier.setAttribute("aria-hidden", String(!ouvrir));
  if (ouvrir) { voile.hidden = false; requestAnimationFrame(() => voile.classList.add("ouvert")); document.getElementById("fermer-panier").focus(); }
  else { voile.classList.remove("ouvert"); setTimeout(() => { voile.hidden = true; }, 300); }
}

async function payer(ev) {
  if (!document.getElementById("accepte").checked) {
    notifier("Coche la case des conditions de vente pour continuer.", true);
    return;
  }
  const bouton = ev.currentTarget;
  bouton.disabled = true;
  bouton.textContent = "Préparation du paiement…";
  try {
    const r = await api("api/commande", { corps: {
      lignes: [...etat.panier].map(([id, quantite]) => ({ id, quantite })), accepte_conditions: true } });
    const url = String(r.url || "");
    // On ne suit que la page de paiement de Stripe (ou, en aperçu local, la page « merci »)
    if (url.startsWith("https://checkout.stripe.com/") || (r.simulation && url.startsWith("/boutique/merci?commande="))) {
      window.location.assign(url);  // le panier est vidé sur la page « merci », pas avant (paiement annulable)
      return;
    }
    throw new Error("Réponse de paiement inattendue.");
  } catch (e) {
    notifier(e.message, true);
    bouton.disabled = false;
    bouton.textContent = "Payer en sécurité";
  }
}

// --- joueur et achats ---------------------------------------------------------------------------

function afficherJoueur() {
  const zone = document.getElementById("zone-joueur");
  const j = etat.joueur;
  if (!j) {
    zone.replaceChildren(boutonMicrosoft("Connexion"));
    document.getElementById("mes-achats").hidden = true;
    return;
  }
  const tete = UUID.test(j.uuid) ? el("img", { src: `https://mc-heads.net/avatar/${j.uuid}/64`, alt: "", width: "32", height: "32" }) : null;
  zone.replaceChildren(tete, el("span", { texte: j.nom }),
    el("button", { classe: "lien-discret", type: "button", texte: "Déconnexion", onclick: deconnexion }));
  chargerAchats();
}

const STATUTS = {
  attente_paiement: ["Paiement en cours", "attente"], payee: ["Payé · livraison en cours", "attente"], livree: ["Livré", "ok"],
  echec: ["Paiement refusé", "erreur"], remboursee: ["Remboursé", "erreur"], litige: ["Litige", "erreur"], anomalie: ["À vérifier par le staff", "erreur"],
};

async function chargerAchats() {
  try {
    const { commandes } = await api("api/mes-commandes");
    const section = document.getElementById("mes-achats");
    section.hidden = !commandes.length;
    document.getElementById("liste-achats").replaceChildren(...commandes.map(c => {
      const [libelle, classe] = STATUTS[c.statut] || [c.statut, "attente"];
      return el("div", { classe: "achat" },
        el("div", {}, el("strong", { texte: c.lignes.map(l => l.quantite > 1 ? `${l.nom} × ${l.quantite}` : l.nom).join(", ") }),
          el("span", { texte: " · " + new Date(c.cree_le * 1000).toLocaleDateString("fr-FR") })),
        el("div", {}, el("span", { texte: euros.format(c.total_centimes / 100) + "  " }), el("span", { classe: `pastille ${classe}`, texte: libelle })));
    }));
  } catch (e) { /* historique facultatif : la boutique reste utilisable */ }
}

async function deconnexion() {
  try { await api("deconnexion", { methode: "POST" }); } catch (e) { /* déjà déconnecté */ }
  etat.joueur = null;
  afficherJoueur();
  majZonePaiement();
  notifier("Tu es déconnecté.");
}

// --- démarrage ---------------------------------------------------------------------------------

async function demarrer() {
  document.getElementById("ouvrir-panier").addEventListener("click", () => ouvrirPanier(true));
  document.getElementById("fermer-panier").addEventListener("click", () => ouvrirPanier(false));
  document.getElementById("voile").addEventListener("click", () => ouvrirPanier(false));
  document.addEventListener("keydown", e => { if (e.key === "Escape") ouvrirPanier(false); });
  try {
    const [catalogue, moi] = await Promise.all([api("api/catalogue"), api("api/moi")]);
    etat.catalogue = catalogue;
    etat.joueur = moi.joueur;
    for (const p of catalogue.produits) etat.produits.set(p.id, p);
    for (const l of lireStockage()) {
      const p = etat.produits.get(l && l.id);
      if (p && Number.isInteger(l.quantite) && l.quantite > 0) etat.panier.set(p.id, Math.min(l.quantite, p.quantite_max));
    }
    document.getElementById("bandeau-test").hidden = !catalogue.mode_test;
    afficherCatalogue();
    afficherJoueur();
    majPanier();
  } catch (e) {
    notifier("La boutique ne répond pas. Réessaie dans un instant.", true);
  }
  const params = new URLSearchParams(location.search);
  if (params.get("connecte") && etat.joueur) { notifier(`Bienvenue, ${etat.joueur.nom} ⛩`); if (etat.panier.size) ouvrirPanier(true); }
  if (params.get("paiement") === "annule") notifier("Paiement annulé : rien n'a été débité.");
  if (params.toString()) history.replaceState(null, "", location.pathname + location.hash);
}

demarrer();
