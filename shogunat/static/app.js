// Panneau du Shogunat : une page par onglet, données lues sur /api/…
"use strict";

const page = document.getElementById("page");

// --- utilitaires --------------------------------------------------------------------

// Échappe tout texte venant de Discord, des joueurs ou du serveur avant de l'insérer en HTML
function e(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Lien sûr pour href/src : seulement http(s), jamais javascript: ou data:
function lien(v) {
  return /^https?:\/\//i.test(String(v || "")) ? e(v) : "#";
}

async function api(chemin, options = {}) {
  const init = { method: options.method || "GET", headers: { "X-Shogunat": "1" } };
  if (options.corps !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.corps);
  }
  const r = await fetch("/api/" + chemin, init);
  if (r.status === 401) { afficherConnexion(); throw new Error("non connecté"); }
  const donnees = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(donnees.erreur || `erreur ${r.status}`);
  return donnees;
}

function toast(message, erreur = false) {
  const t = document.getElementById("toast");
  t.textContent = message;
  t.className = erreur ? "erreur" : "";
  t.hidden = false;
  clearTimeout(toast.minuteur);
  toast.minuteur = setTimeout(() => { t.hidden = true; }, erreur ? 6000 : 3000);
}

// Exécute une action d'un bouton : le désactive pendant la requête et affiche le résultat
async function action(bouton, fn, succes) {
  if (bouton) bouton.disabled = true;
  try {
    const r = await fn();
    if (succes) toast(succes);
    return r;
  } catch (err) {
    toast(err.message, true);
  } finally {
    if (bouton) bouton.disabled = false;
  }
}

function date(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

function ilYa(ts) {
  if (!ts) return "jamais";
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 60) return "à l'instant";
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`;
  return date(ts);
}

function formulaire(form) {
  const d = {};
  new FormData(form).forEach((v, k) => { d[k] = v; });
  form.querySelectorAll("input[type=checkbox]").forEach(c => { d[c.name] = c.checked; });
  return d;
}

// Couleurs des clans : appliquées en JS (le CSP du panneau interdit les attributs style="")
function couleurs() {
  page.querySelectorAll("[data-couleur]").forEach(el => { el.style.background = el.dataset.couleur; });
}

// --- pages ----------------------------------------------------------------------------

const PAGES = {};

PAGES.tableau = async () => {
  const t = await api("tableau");
  const st = t.statut;
  page.innerHTML = `
    <div class="entete"><h1>Tableau de bord</h1><span class="discret petit">${e(t.adresse)}</span></div>
    ${t.config_manquante.length ? `<div class="alerte">
      <strong>Réglages à compléter dans le fichier .env :</strong>
      <ul>${t.config_manquante.map(p => `<li>${e(p)}</li>`).join("")}</ul></div><br>` : ""}
    <div class="grille">
      <div class="carte pile">
        <div class="ligne"><h2>Serveur Minecraft</h2>
          <span class="badge ${st.en_ligne ? "ok" : "non"}">${st.en_ligne ? "en ligne" : "hors ligne"}</span></div>
        <div class="chiffre">${st.en_ligne ? `${st.joueurs}<span class="discret petit"> / ${st.max} joueurs</span>` : "—"}</div>
        <div class="discret petit">${st.en_ligne ? `${e(st.version)} · ${st.latence} ms` : "Éteint, en veille ou injoignable."}</div>
        ${st.noms && st.noms.length ? `<div>${st.noms.map(n => `<span class="badge">${e(n)}</span>`).join(" ")}</div>` : ""}
      </div>
      <div class="carte pile">
        <div class="ligne"><h2>Bot Discord</h2>
          <span class="badge ${t.bot.connecte ? "ok" : "non"}">${t.bot.connecte ? "connecté" : "déconnecté"}</span></div>
        <div>${e(t.bot.nom || "—")}</div>
        <div class="discret petit">${t.bot.serveur ? "Serveur : " + e(t.bot.serveur) : "Pas encore sur le serveur Discord"}</div>
      </div>
      <a class="carte pile" href="#tickets"><h2>Tickets ouverts</h2><div class="chiffre">${t.compteurs.tickets_ouverts}</div></a>
      <a class="carte pile" href="#suggestions"><h2>Suggestions en attente</h2><div class="chiffre">${t.compteurs.suggestions_en_attente}</div></a>
      <a class="carte pile" href="#annonces"><h2>Annonces programmées</h2><div class="chiffre">${t.compteurs.annonces_prevues}</div></a>
      <a class="carte pile" href="#classements"><h2>Joueurs classés</h2><div class="chiffre">${t.compteurs.joueurs_suivis}</div>
        <div class="discret petit">Stats lues ${ilYa(t.stats_maj_le)}</div></a>
    </div>`;
  badges(t.compteurs);
};

PAGES.annonces = async () => {
  const [liste, clans] = await Promise.all([api("annonces"), api("clans")]);
  page.innerHTML = `
    <div class="entete"><h1>Annonces</h1></div>
    <form id="f-annonce" class="carte pile">
      <h2>Nouvelle annonce</h2>
      <label>Titre<input name="titre" maxlength="256" required></label>
      <label>Message<textarea name="contenu" maxlength="4000" required placeholder="Le texte de l'annonce (Markdown Discord accepté)"></textarea></label>
      <div class="champs">
        <label>Publiée par<select name="clan">
          <option value="">La mascotte principale, dans #annonces</option>
          ${clans.clans.map(c => `<option value="${e(c.slug)}">${e(c.mascotte_nom || c.nom)}, dans le salon ${e(c.nom)}</option>`).join("")}
        </select></label>
        <label>Mention<select name="mention">
          <option value="aucune">Aucune</option><option value="here">@here</option><option value="everyone">@everyone</option>
        </select></label>
        <label>Image (lien https, facultatif)<input name="image_url" type="url" placeholder="https://…"></label>
        <label>Programmer (facultatif)<input name="quand" type="datetime-local"></label>
      </div>
      <label class="case"><input type="checkbox" name="en_jeu"> Afficher aussi dans le chat Minecraft</label>
      <div><button class="principal">Publier</button></div>
    </form>
    <br>
    <div class="carte tableau">
      ${liste.length ? `<table><thead><tr><th>Annonce</th><th>Par</th><th>État</th><th></th></tr></thead><tbody>
        ${liste.map(a => `<tr>
          <td><strong>${e(a.titre)}</strong><div class="discret petit">${e(a.contenu.slice(0, 140))}${a.contenu.length > 140 ? "…" : ""}</div></td>
          <td class="petit">${e(a.auteur || "")}${a.clan ? `<div class="discret">clan ${e(a.clan)}</div>` : ""}${a.en_jeu ? `<div class="discret">+ en jeu</div>` : ""}</td>
          <td class="petit">${etatAnnonce(a)}</td>
          <td>${!a.envoyee_le ? `<button class="petit danger" data-supprimer="${a.id}">Annuler</button>` : ""}
              ${a.erreur ? `<button class="petit" data-relancer="${a.id}">Renvoyer</button>` : ""}</td>
        </tr>`).join("")}</tbody></table>` : `<div class="vide">Aucune annonce pour l'instant.</div>`}
    </div>`;

  document.getElementById("f-annonce").addEventListener("submit", async ev => {
    ev.preventDefault();
    const d = formulaire(ev.target);
    d.prevue_le = d.quand ? Math.floor(new Date(d.quand).getTime() / 1000) : null;
    delete d.quand;
    const ok = await action(ev.submitter, () => api("annonces", { method: "POST", corps: d }),
      d.prevue_le ? "Annonce programmée" : "Annonce envoyée au bot (publication sous 20 s)");
    if (ok) PAGES.annonces();
  });
  page.querySelectorAll("[data-supprimer]").forEach(b => b.addEventListener("click", async () => {
    if (await action(b, () => api("annonces/" + b.dataset.supprimer, { method: "DELETE" }), "Annonce annulée")) PAGES.annonces();
  }));
  page.querySelectorAll("[data-relancer]").forEach(b => b.addEventListener("click", async () => {
    if (await action(b, () => api(`annonces/${b.dataset.relancer}/relancer`, { method: "POST" }), "Nouvel essai sous 20 s")) PAGES.annonces();
  }));
};

function etatAnnonce(a) {
  if (a.erreur) return `<span class="badge non">problème</span><div class="discret">${e(a.erreur)}</div>`;
  if (a.envoyee_le) return `<span class="badge ok">envoyée</span><div class="discret">${date(a.envoyee_le)}</div>`;
  if (a.prevue_le) return `<span class="badge attente">programmée</span><div class="discret">${date(a.prevue_le)}</div>`;
  return `<span class="badge attente">en cours d'envoi</span>`;
}

PAGES.classements = async () => {
  const c = await api("classements");
  rendreClassements(c);
};

function rendreClassements(c) {
  page.innerHTML = `
    <div class="entete"><h1>Classements</h1>
      <div class="ligne"><span class="discret petit">Données du serveur ${ilYa(c.maj_le)}</span>
      <button class="principal" id="b-rafraichir">Actualiser</button></div></div>
    <p class="discret petit">Le bot relit les stats toutes les 2 minutes et met à jour #classements toutes les 10 minutes.
      « Actualiser » le fait tout de suite.</p>
    <div class="grille">
      ${Object.values(c.tops).map(t => `<div class="carte"><h2>${e(t.titre)}</h2>
        ${t.lignes.length ? `<ol class="top">${t.lignes.map(l => `<li>${l.couleur ? `<span class="pastille" data-couleur="${e(l.couleur)}"></span>` : ""}<strong>${e(l.nom)}</strong> <span class="discret">— ${e(l.valeur)}</span></li>`).join("")}</ol>`
          : `<div class="vide">Pas encore de données</div>`}</div>`).join("")}
    </div>`;
  couleurs();
  document.getElementById("b-rafraichir").addEventListener("click", async ev => {
    const r = await action(ev.target, () => api("classements/rafraichir", { method: "POST" }), "Classements à jour");
    if (r) rendreClassements(r);
  });
}

PAGES.tickets = async () => {
  const tickets = await api("tickets");
  const ouverts = tickets.filter(t => t.statut === "ouvert");
  const fermes = tickets.filter(t => t.statut !== "ouvert");
  const ligne = t => `<tr>
      <td>n°${t.id}</td>
      <td><strong>${e(t.sujet)}</strong><div class="discret petit">${e(t.categorie)}</div></td>
      <td>${e(t.user_nom)}</td>
      <td class="petit">${date(t.ouvert_le)}${t.ferme_le ? `<div class="discret">fermé ${date(t.ferme_le)} par ${e(t.ferme_par)}</div>` : ""}</td>
      <td class="ligne">${t.lien ? `<a class="bouton petit" href="${lien(t.lien)}" target="_blank" rel="noopener">Ouvrir</a>` : ""}
        ${t.statut === "ouvert" ? `<button class="petit" data-fermer="${t.id}">Fermer</button>` : ""}</td></tr>`;
  const table = l => l.length ? `<div class="tableau"><table><thead><tr><th>N°</th><th>Sujet</th><th>Joueur</th><th>Date</th><th></th></tr></thead>
      <tbody>${l.map(ligne).join("")}</tbody></table></div>` : `<div class="vide">Aucun ticket.</div>`;
  page.innerHTML = `
    <div class="entete"><h1>Tickets</h1></div>
    <div class="carte pile"><h2>Ouverts (${ouverts.length})</h2>${table(ouverts)}</div><br>
    <div class="carte pile"><h2>Fermés</h2>${table(fermes)}</div>`;
  page.querySelectorAll("[data-fermer]").forEach(b => b.addEventListener("click", async () => {
    if (await action(b, () => api(`tickets/${b.dataset.fermer}/fermer`, { method: "POST" }), "Ticket fermé")) PAGES.tickets();
  }));
};

PAGES.faq = async () => {
  const questions = await api("faq");
  const carte = q => `<form class="carte pile" data-id="${q ? q.id : ""}">
      <div class="champs"><label>Question<input name="question" maxlength="256" required value="${e(q ? q.question : "")}"></label>
        <label>Ordre d'affichage<input name="ordre" type="number" value="${q ? q.ordre : questions.length}"></label></div>
      <label>Réponse<textarea name="reponse" maxlength="1024" required>${e(q ? q.reponse : "")}</textarea></label>
      <div class="ligne"><button class="${q ? "" : "principal"}">${q ? "Enregistrer" : "Ajouter la question"}</button>
        ${q ? `<button type="button" class="danger" data-supprimer>Supprimer</button>` : ""}</div></form>`;
  page.innerHTML = `
    <div class="entete"><h1>FAQ</h1><button class="principal" id="b-publier">Publier dans #faq</button></div>
    <p class="discret petit">Les joueurs la consultent avec <code>/faq</code> (à jour immédiatement).
      Le salon #faq, lui, est mis à jour quand tu cliques sur « Publier ».</p>
    <div class="pile">${questions.map(carte).join("")}<h2>Nouvelle question</h2>${carte(null)}</div>`;
  page.querySelectorAll("form").forEach(f => {
    f.addEventListener("submit", async ev => {
      ev.preventDefault();
      const id = f.dataset.id;
      const d = formulaire(f);
      d.ordre = Number(d.ordre) || 0;
      if (await action(ev.submitter, () => api(id ? "faq/" + id : "faq", { method: id ? "PUT" : "POST", corps: d }), "FAQ enregistrée")) PAGES.faq();
    });
    const sup = f.querySelector("[data-supprimer]");
    if (sup) sup.addEventListener("click", async () => {
      if (!confirm("Supprimer cette question ?")) return;
      if (await action(sup, () => api("faq/" + f.dataset.id, { method: "DELETE" }), "Question supprimée")) PAGES.faq();
    });
  });
  document.getElementById("b-publier").addEventListener("click", ev =>
    action(ev.target, () => api("faq/publier", { method: "POST" }), "FAQ publiée dans #faq"));
};

const STATUTS = { en_attente: ["⏳ En attente", "attente"], acceptee: ["✅ Acceptée", "ok"], refusee: ["❌ Refusée", "non"], faite: ["🎉 Ajoutée", "ok"] };

PAGES.suggestions = async () => {
  const liste = await api("suggestions");
  page.innerHTML = `
    <div class="entete"><h1>Suggestions</h1></div>
    <p class="discret petit">Proposées par les joueurs avec <code>/suggestion</code>. Ta décision met à jour le message dans #suggestions
      et prévient l'auteur en message privé.</p>
    <div class="pile">${liste.length ? liste.map(s => `<div class="carte pile" data-id="${s.id}">
        <div class="ligne"><span class="badge">${s.type === "mod" ? "🧩 Mod" : "✨ Fonctionnalité"}</span>
          <h2>${e(s.titre)}</h2><span class="badge ${STATUTS[s.statut][1]}">${STATUTS[s.statut][0]}</span>
          <span class="discret petit">👍 ${s.pour} · 👎 ${s.contre}</span></div>
        <div>${e(s.description)}</div>
        ${s.lien ? `<div class="petit">Lien : <a href="${lien(s.lien)}" target="_blank" rel="noopener noreferrer">${e(s.lien)}</a></div>` : ""}
        <div class="discret petit">Par ${e(s.user_nom)} · ${date(s.creee_le)}
          ${s.lien_discord ? ` · <a href="${lien(s.lien_discord)}" target="_blank" rel="noopener">voir sur Discord</a>` : ""}</div>
        <label>Réponse du staff (facultatif, visible publiquement)<input name="note" maxlength="1000" value="${e(s.note_admin || "")}"></label>
        <div class="ligne">
          <button class="petit" data-statut="acceptee">✅ Accepter</button>
          <button class="petit" data-statut="refusee">❌ Refuser</button>
          <button class="petit" data-statut="faite">🎉 Ajoutée au serveur</button>
          ${s.statut !== "en_attente" ? `<button class="petit" data-statut="en_attente">Remettre en attente</button>` : ""}
        </div></div>`).join("") : `<div class="carte vide">Aucune suggestion pour l'instant.</div>`}</div>`;
  page.querySelectorAll("[data-statut]").forEach(b => b.addEventListener("click", async () => {
    const carte = b.closest("[data-id]");
    const corps = { statut: b.dataset.statut, note: carte.querySelector("[name=note]").value };
    if (await action(b, () => api(`suggestions/${carte.dataset.id}/statut`, { method: "POST", corps }), "Suggestion mise à jour")) PAGES.suggestions();
  }));
};

PAGES.mascottes = async () => {
  const { clans, mascotte } = await api("clans");
  const img = url => url ? `<img class="avatar" src="${lien(url)}" alt="">` : `<div class="avatar"></div>`;
  page.innerHTML = `
    <div class="entete"><h1>Mascottes &amp; clans</h1></div>
    <form id="f-mascotte" class="carte pile">
      <div class="ligne">${img(mascotte.avatar_url)}<div><h2>Mascotte principale</h2>
        <div class="discret petit">Nom et avatar du bot sur le serveur Discord.</div></div></div>
      <div class="champs"><label>Nom<input name="nom" maxlength="32" value="${e(mascotte.nom)}"></label>
        <label>Image (lien https vers un PNG/JPG)<input name="avatar_url" type="url" value="${e(mascotte.avatar_url)}"></label></div>
      <p class="discret petit">Discord limite le changement d'avatar à 2 fois par heure.</p>
      <div><button class="principal">Appliquer</button></div>
    </form>
    <br><h2>Clans</h2>
    <p class="discret petit">Dans le salon de chaque clan, le bot parle sous le nom et l'avatar de la mascotte du clan.
      Les salons sont créés dans l'onglet Structure Discord.</p>
    <div class="grille">${clans.map(c => `<form class="carte pile" data-slug="${e(c.slug)}">
        <div class="ligne">${img(c.mascotte_avatar)}<div><h2><span class="pastille" data-couleur="${e(c.couleur)}"></span>${e(c.nom)} ${e(c.kanji || "")}</h2>
          <span class="badge ${c.pret ? "ok" : "non"}">${c.pret ? "salon prêt" : "salon à créer"}</span></div></div>
        <div class="discret petit">Membres : ${c.membres.length ? c.membres.map(e).join(", ") : "aucun"}</div>
        <label>Nom de la mascotte<input name="mascotte_nom" maxlength="80" value="${e(c.mascotte_nom || "")}"></label>
        <label>Avatar (lien https)<input name="mascotte_avatar" type="url" value="${e(c.mascotte_avatar || "")}"></label>
        <div><button>Enregistrer</button></div>
        <label>Faire parler la mascotte<textarea name="contenu" maxlength="2000" placeholder="Message publié dans le salon du clan"></textarea></label>
        <div><button type="button" data-parler ${c.pret ? "" : "disabled"}>Envoyer</button></div>
      </form>`).join("")}</div>`;
  couleurs();
  document.getElementById("f-mascotte").addEventListener("submit", async ev => {
    ev.preventDefault();
    const r = await action(ev.submitter, () => api("mascotte", { method: "PUT", corps: formulaire(ev.target) }));
    if (r) { toast(r.resultats.join(" · ") || "Enregistré"); PAGES.mascottes(); }
  });
  page.querySelectorAll("form[data-slug]").forEach(f => {
    f.addEventListener("submit", async ev => {
      ev.preventDefault();
      const d = formulaire(f);
      if (await action(ev.submitter, () => api("clans/" + f.dataset.slug, { method: "PUT", corps: { mascotte_nom: d.mascotte_nom, mascotte_avatar: d.mascotte_avatar } }), "Mascotte enregistrée")) PAGES.mascottes();
    });
    f.querySelector("[data-parler]").addEventListener("click", async ev => {
      const zone = f.querySelector("[name=contenu]");
      if (await action(ev.target, () => api(`clans/${f.dataset.slug}/message`, { method: "POST", corps: { contenu: zone.value } }), "Message envoyé")) zone.value = "";
    });
  });
};

PAGES.structure = async () => {
  const s = await api("structure");
  const NOMS = { annonces: "📢 annonces", statut: "🟢 statut", classements: "🏆 classements", faq: "❓ faq",
                 suggestions: "💡 suggestions", tickets: "🎫 tickets", staff: "📋 journal-staff" };
  page.innerHTML = `
    <div class="entete"><h1>Structure Discord</h1></div>
    ${!s.bot_pret ? `<div class="alerte">Le bot n'est pas connecté au serveur Discord. Invite-le avec le lien ci-dessous.</div><br>` : ""}
    <div class="carte pile">
      <h2>1. Permissions du bot</h2>
      ${s.admin ? `<div class="alerte"><strong>Le bot a la permission Administrateur.</strong> Pense à la retirer
        (Paramètres du serveur → Rôles → rôle du bot) quand la structure est créée.</div>` : ""}
      ${!s.bot_pret ? `<div class="discret petit">Vérification impossible tant que le bot n'est pas connecté.</div>` : `
      <div class="ligne"><span>Au quotidien</span>${s.manque_quotidien.length
        ? `<span class="badge non">il manque : ${e(s.manque_quotidien.join(", "))}</span>` : `<span class="badge ok">tout est bon</span>`}</div>
      <div class="ligne"><span>Pour créer la structure</span>${s.manque_structure.length
        ? `<span class="badge attente">il manque : ${e(s.manque_structure.join(", "))}</span>` : `<span class="badge ok">disponible</span>`}</div>`}
      <div class="info petit">Le bot n'a besoin des permissions « Gérer les salons / les rôles / les webhooks » que le temps de créer
        la structure. Donne-les avec le second lien, clique sur « Créer la structure », puis réinvite-le avec le premier lien
        (ou décoche ces permissions sur son rôle).</div>
      <div class="ligne">
        ${s.invitation ? `<a class="bouton" href="${lien(s.invitation)}" target="_blank" rel="noopener">Lien d'invitation (quotidien)</a>` : ""}
        ${s.invitation_structure ? `<a class="bouton" href="${lien(s.invitation_structure)}" target="_blank" rel="noopener">Lien avec les permissions de structure</a>` : ""}
      </div>
    </div><br>
    <div class="carte pile">
      <h2>2. Salons et rôles</h2>
      <p class="discret petit">Crée les catégories Shogunat, Staff et Clans, leurs salons, les rôles des 4 clans et du staff,
        les mascottes de clan, puis publie les messages (statut, classements, FAQ, menu des tickets). Les salons qui existent
        déjà sont réutilisés : tu peux relancer sans risque de doublon.</p>
      <div><button class="principal" id="b-creer" ${s.bot_pret && !s.manque_structure.length ? "" : "disabled"}>Créer / réparer la structure</button></div>
      <div id="rapport"></div>
      <table><tbody>${Object.entries(NOMS).map(([cle, nom]) => `<tr><td>${nom}</td><td>${s.salons[cle] && s.salons[cle].nom
        ? `<span class="badge ok">#${e(s.salons[cle].nom)}</span>` : `<span class="badge">pas encore créé</span>`}</td></tr>`).join("")}</tbody></table>
    </div>`;
  document.getElementById("b-creer").addEventListener("click", async ev => {
    const r = await action(ev.target, () => api("structure/creer", { method: "POST" }), "Structure prête");
    if (r) {
      await PAGES.structure();
      document.getElementById("rapport").innerHTML = `<div class="info"><ul>${r.rapport.map(l => `<li>${e(l)}</li>`).join("")}</ul></div>`;
    }
  });
};

// --- navigation ------------------------------------------------------------------------

function badges(compteurs) {
  const pose = (id, n) => { const b = document.getElementById(id); b.textContent = n; b.hidden = !n; };
  pose("nb-tickets", compteurs.tickets_ouverts);
  pose("nb-suggestions", compteurs.suggestions_en_attente);
}

async function naviguer() {
  const nom = (location.hash || "#tableau").slice(1);
  const rendu = PAGES[nom] || PAGES.tableau;
  document.querySelectorAll(".menu a[href^='#']").forEach(a => a.classList.toggle("actif", a.getAttribute("href") === "#" + nom));
  page.innerHTML = `<div class="vide">Chargement…</div>`;
  try {
    await rendu();
  } catch (err) {
    if (err.message !== "non connecté") page.innerHTML = `<div class="alerte">${e(err.message)}</div>`;
  }
}

function afficherConnexion() {
  document.getElementById("ecran-app").hidden = true;
  document.getElementById("ecran-connexion").hidden = false;
}

async function demarrer() {
  try {
    const moi = await api("moi");
    document.getElementById("moi-nom").textContent = moi.nom;
    if (moi.avatar) { const img = document.getElementById("moi-avatar"); img.src = moi.avatar; img.hidden = false; }
    document.getElementById("ecran-app").hidden = false;
    window.addEventListener("hashchange", naviguer);
    naviguer();
    api("tableau").then(t => badges(t.compteurs)).catch(() => {});
  } catch (err) { /* écran de connexion déjà affiché */ }
}

demarrer();
