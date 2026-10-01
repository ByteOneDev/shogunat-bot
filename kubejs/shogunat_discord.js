// =====================================================================
//  Shogunat — Pont avec le bot Discord
//  KubeJS 7 · NeoForge 1.21.1 · à placer dans kubejs/server_scripts/
//
//  /shogunat_discord export   (console / RCON uniquement, niveau 4)
//    renvoie en JSON les statistiques gardées par shogunat_scores.js
//    (kills, morts, temps de jeu, argent, clan) et les clans de shogunat_clans.js.
//    Le bot Discord l'appelle via RCON toutes les 2 minutes : rien n'est modifié.
// =====================================================================

function sdLire(server, cle) {
  try { return JSON.parse(String(server.persistentData.getString(cle)) || '{}') } catch (e) { return {} }
}

ServerEvents.commandRegistry(event => {
  let { commands: Commands } = event
  event.register(Commands.literal('shogunat_discord')
    .requires(src => src.hasPermission(4))
    .then(Commands.literal('export').executes(ctx => {
      let server = ctx.source.server
      // met à jour les joueurs connectés avant l'export (fonction de shogunat_scores.js)
      server.getPlayerList().getPlayers().forEach(p => { try { scCollect(p) } catch (e) { } })

      let clans = sdLire(server, 'shogunat_clans').clans || {}
      let sortie = {
        joueurs: sdLire(server, 'shogunat_stats'),
        clans: {},
        en_ligne: []
      }
      Object.keys(clans).forEach(c => {
        let cl = clans[c]
        sortie.clans[c] = {
          membres: (cl.members || []).map(m => ({ u: m.u, n: m.n || null })),
          chef: cl.chef == null ? null : cl.chef,
          karo: cl.karo || []
        }
      })
      server.getPlayerList().getPlayers().forEach(p => sortie.en_ligne.push(String(p.gameProfile.name)))

      ctx.source.sendSystemMessage(Text.of(JSON.stringify(sortie)))
      return 1
    })))
})
