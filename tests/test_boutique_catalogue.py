"""Étape 1 — catalogue : impossible de tricher sur le prix, les quantités ou les produits."""
import unittest

import tests  # noqa: F401  (base temporaire)
from shogunat import db
from shogunat.boutique import catalogue as cat


class TestPanier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cat.initialiser()

    def test_total_recalcule_depuis_la_base(self):
        lignes, total = cat.calculer_panier([{"id": "grade-sakura", "quantite": 1}, {"id": "don-5", "quantite": 3}])
        self.assertEqual(total, 499 + 3 * 500)
        self.assertEqual([l["prix_centimes"] for l in lignes], [499, 500])

    def test_prix_envoye_par_le_navigateur_ignore(self):
        lignes, total = cat.calculer_panier([{"id": "grade-jade", "quantite": 1, "prix_centimes": 1, "prix": 0.01}])
        self.assertEqual(total, 1999)
        self.assertNotIn("prix", lignes[0])

    def test_refus(self):
        cas = {
            "panier vide": [],
            "pas une liste": {"id": "don-2", "quantite": 1},
            "produit inconnu": [{"id": "grade-admin", "quantite": 1}],
            "quantité nulle": [{"id": "don-2", "quantite": 0}],
            "quantité négative": [{"id": "don-2", "quantite": -3}],
            "quantité au-delà du max": [{"id": "grade-sakura", "quantite": 2}],
            "quantité énorme": [{"id": "don-2", "quantite": 10 ** 9}],
            "quantité décimale": [{"id": "don-2", "quantite": 1.5}],
            "quantité en texte": [{"id": "don-2", "quantite": "1"}],
            "quantité booléenne": [{"id": "don-2", "quantite": True}],
            "identifiant non textuel": [{"id": ["don-2"], "quantite": 1}],
            "doublon pour contourner le max": [{"id": "grade-sakura", "quantite": 1}, {"id": "grade-sakura", "quantite": 1}],
            "trop de lignes": [{"id": f"x{i}", "quantite": 1} for i in range(21)],
            "injection SQL": [{"id": "don-2' OR '1'='1", "quantite": 1}],
            "ligne non objet": ["don-2"],
        }
        for nom, panier in cas.items():
            with self.subTest(nom):
                with self.assertRaises(cat.PanierInvalide):
                    cat.calculer_panier(panier)

    def test_produit_desactive_refuse(self):
        db.executer("UPDATE boutique_produits SET actif = 0 WHERE id = 'cos-familier'")
        try:
            with self.assertRaises(cat.PanierInvalide):
                cat.calculer_panier([{"id": "cos-familier", "quantite": 1}])
            self.assertNotIn("cos-familier", [p["id"] for p in cat.catalogue_public()["produits"]])
        finally:
            db.executer("UPDATE boutique_produits SET actif = 1 WHERE id = 'cos-familier'")

    def test_plafond_de_montant(self):
        db.executer("UPDATE boutique_produits SET prix_centimes = 49 WHERE id = 'don-2'")
        try:
            with self.assertRaises(cat.PanierInvalide):  # sous le minimum Stripe de 0,50 €
                cat.calculer_panier([{"id": "don-2", "quantite": 1}])
        finally:
            db.executer("UPDATE boutique_produits SET prix_centimes = 200 WHERE id = 'don-2'")

    def test_catalogue_public_sans_commandes_de_livraison(self):
        for p in cat.catalogue_public()["produits"]:
            self.assertNotIn("commandes", p)
            self.assertNotIn("actif", p)

    def test_commande_visible_seulement_par_son_joueur(self):
        lignes, total = cat.calculer_panier([{"id": "don-2", "quantite": 1}])
        cid = cat.creer_commande("11111111-2222-3333-4444-555555555555", "Kenji_", lignes, total)
        self.assertGreaterEqual(len(cid), 24)  # identifiant aléatoire, non devinable
        self.assertIsNotNone(cat.commande_du_joueur(cid, "11111111-2222-3333-4444-555555555555"))
        self.assertIsNone(cat.commande_du_joueur(cid, "99999999-2222-3333-4444-555555555555"))


class TestBase(unittest.TestCase):
    def test_noms_de_colonnes_controles(self):
        for champs in ({"statut = 'livree', total_centimes": 1}, {"statut; DROP TABLE x": 1}, {"Statut": 1}):
            with self.subTest(champs=champs):
                with self.assertRaises(ValueError):
                    db.maj("boutique_commandes", "id", "x", champs)
        with self.assertRaises(ValueError):
            db.maj("boutique_commandes; --", "id", "x", {"statut": "livree"})


if __name__ == "__main__":
    unittest.main()
