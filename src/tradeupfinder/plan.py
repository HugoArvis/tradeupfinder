"""Plan d'achat concret a partir des offres REELLES en vente sur CSFloat.

Difference de nature avec `scan` :

  `scan`  travaille sur des prix par palier d'usure et un float SUPPOSE. Il
          repond a "quelle collection merite qu'on s'y interesse ?".
  `plan`  travaille sur les annonces reellement en vente, avec leur float
          exact. Il repond a "voici les 10 objets a acheter, maintenant".

Consequence majeure : le float n'etant plus un tirage mais une donnee, la
marge de securite anti-falaise devient inutile. C'est ce qui rend CSFloat
structurellement meilleur que Steam pour executer un trade-up.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from .db import SkinDatabase
from .ev import InputItem, TradeUpResult, evaluate
from .fees import STEAM, FeeModel
from .generator import InputOption, cheapest_unique_selection, options_from_listings
from .models import Collection, Rarity
from .pricing.base import PriceSource, Quote
from .pricing.csfloat import CSFloat
from .wear import TRADEUP_INPUT_COUNT, wear_breakpoints

log = logging.getLogger(__name__)


class BudgetEpuise(Exception):
    """Le temps imparti est ecoule au milieu d'une collection.

    Distincte d'un echec : le travail n'a pas rate, il n'a pas fini. La
    collection reste donc a faire, et le passage suivant la reprendra --
    `Journal.last_swept` la verra comme jamais calculee.
    """


def horloge() -> float:
    """L'horloge des budgets de temps : elle ne compte pas la mise en veille.

    `time.time()` avance pendant la veille. Mesure le 19 octobre 2026 : un
    passage de 60 minutes lance a 16:36, portable mis en veille, s'est arrete
    au reveil le lendemain matin apres 2 collections -- "termine en 1018 min",
    budget juge depasse alors qu'il avait tourne un quart d'heure.
    `time.monotonic()` s'arrete pendant la veille sous Linux, comme l'horloge
    dont systemd se sert pour ses propres delais. Toute echeance passee a
    `build_plan` se calcule donc avec CETTE fonction, jamais avec time.time().
    """
    import time as _t

    return _t.monotonic()


def _verifie_budget(deadline: float | None) -> None:
    """Leve `BudgetEpuise` si l'echeance (en `horloge()`) est passee.

    Appele aux endroits ou le temps part vraiment : une cotation par objet a
    l'entree, une evaluation par frontiere d'usure. Verifier plus finement
    n'apporterait rien -- entre deux appels reseau il ne se passe rien.
    """
    if deadline is not None and horloge() > deadline:
        raise BudgetEpuise()


class CSFloatPricer:
    """`PriceLookup` alimente par les offres reelles CSFloat.

    A l'interieur d'un meme palier d'usure, le prix demande varie enormement :
    mesure sur l'AK-47 Emerald Pinstripe, une Factory New s'affiche 24-25 USD
    a float 0.005 mais 17-20 a float 0.070. La valorisation retient pourtant
    la moins chere annonce nue du palier, sans prime de bas float : ce sont des
    prix demandes, rien ne dit qu'ils se concluent (`price_at_float`). La
    valeur d'une sortie ne depend donc que de son palier.
    """

    def __init__(self, source: CSFloat, *, sell_fee: float = 0.02,
                 safety_margin: float = 0.05, listings_limit: int = 50,
                 sell_source: PriceSource | None = None,
                 sell_fees: FeeModel | None = None,
                 sell_to_usd: float = 1.0,
                 sell_basis: str = "sales",
                 deadline: float | None = None):
        self.source = source
        self.sell_fee = sell_fee
        self.safety_margin = safety_margin
        self.listings_limit = listings_limit
        self._book: dict[str, list[tuple[float, float]]] = {}  # nom -> [(float, prix)]
        self._stats: dict[str, dict | None] = {}
        # Ou la SORTIE se revend. Par defaut nulle part ailleurs qu'ici : le
        # prix CSFloat moins 2 %. Mais un contrat ne se juge pas sur ce qu'il
        # vaut, il se juge sur ce qu'on ENCAISSE, et les deux marches ne
        # prelevent pas la meme chose. Steam prend ~13 % du prix affiche, avec
        # un plancher de 0.01 PAR FRAIS qui ecrase les petits montants : 66 %
        # sur un objet a 0.03, 28.6 % a 0.07, contre 12-13 % au-dela de 0.25.
        # A ces montants-la, les frais ne rognent pas le gain, ils le mangent.
        self.sell_source = sell_source
        self.sell_fees = sell_fees or STEAM
        # Le marche de revente cote dans SA devise, et les frais s'y appliquent
        # dans SA devise : le plancher de Steam est de 0,01 EUR sur un compte
        # en euros, pas de 0,01 USD converti. A 0,08 l'ecart entre les deux
        # facons de compter atteint plusieurs points -- exactement la ou le
        # plancher decide. On convertit donc APRES les frais, jamais avant.
        self.sell_to_usd = sell_to_usd
        # Sur quel prix de revente raisonner. "sales" retient la mediane des
        # ventes recentes -- ce que le marche NEGOCIE -- plutot que la plus
        # basse annonce en cours, qui n'est qu'une esperance de vendeur. C'est
        # la lecture des guides, et elle coupe dans les deux sens : mesure sur
        # les sorties Bank, le CZ75-Auto Tuxedo (FT) s'affichait 0,28 mais se
        # vendait 0,24. Ici l'achat ne subit aucune contrepartie -- on achete
        # des annonces CSFloat reelles, au prix affiche.
        self.sell_basis = sell_basis
        self._ventes: dict[str, Quote | None] = {}
        # Un marche de revente qui refuse TOUT n'est pas une suite d'echecs
        # isolees, c'est une panne. La distinguer evite de payer 75 s de
        # backoff par nom pour un resultat connu d'avance : une nuit de
        # balayage a fait 9 collections sur 88 en 155 minutes, presque
        # entierement passees a attendre des 429.
        self.echecs_vente = 0
        # Nombre de sorties valorisees par REPLI sur CSFloat, faute de prix
        # Steam. Un plan qui en contient reste exploitable -- mais il est
        # PRUDENT, pas exact, et l'afficher sans le dire serait mentir par
        # omission dans le bon sens.
        self.replis = 0
        # Echeance du passage. `evaluate()` cote les sorties une par une, et
        # une collection a plus de cent sorties : sans ce controle ICI, le
        # budget ne pouvait pas etre tenu, quel que soit le controle en amont.
        self.deadline = deadline

    def _listings(self, name: str) -> list[tuple[float, float]]:
        """Carnet nettoye, utilisable pour valoriser une sortie de trade-up.

        Deux filtres, dans cet ordre :

        1. On ecarte les objets STICKES ou avec breloque. Une sortie de contrat
           sort toujours nue ; la comparer a un exemplaire portant des
           Katowice 2014 revient a valoriser les stickers, pas l'arme. C'est ce
           qui a produit une Five-SeveN Candy Apple estimee 582 USD alors que
           les exemplaires nus se vendaient 85 a 127.

        2. On ecarte ce qui depasse 3x la mediane. Il reste toujours des
           annonces gonflees pour des raisons qu'on ne modelise pas (motif rare,
           numero de serie, vendeur qui teste le marche). Une seule d'entre
           elles suffit a fausser toute la courbe.
        """
        if name in self._book:
            return self._book[name]
        _verifie_budget(self.deadline)

        offres = [
            o for o in self.source.listings(name, limit=self.listings_limit)
            if o.float_value is not None and getattr(o, "plain", True)
        ]
        if offres:
            prix = sorted(o.price for o in offres)
            mediane = prix[len(prix) // 2]
            plafond = 3.0 * mediane
            gardees = [o for o in offres if o.price <= plafond]
            if len(gardees) < len(offres):
                log.info(
                    "%s : %d annonce(s) ecartee(s) au-dessus de %.2f (3x mediane)",
                    name, len(offres) - len(gardees), plafond,
                )
            offres = gardees or offres

        self._book[name] = sorted((o.float_value, o.price) for o in offres)
        return self._book[name]

    def _lowest(self, name: str) -> float | None:
        book = self._listings(name)
        return min((p for _, p in book), default=None)

    def price_at_float(self, name: str, float_value: float) -> float | None:
        """Prix qu'on peut REELLEMENT esperer en revendant, frais exclus.

        On retient la moins chere des annonces nues du palier d'usure -- le
        prix qu'il faudra battre pour vendre dans un delai raisonnable.

        Ce choix ignore DELIBEREMENT la prime de bas float, alors qu'elle
        existe (une Factory New a 0.005 s'affiche 40 % au-dessus d'une a
        0.070). Trois raisons :

        1. Ce sont des prix DEMANDES, et rien ne dit qu'une annonce a prime se
           conclut -- ni en combien de temps.
        2. Cette prime concerne une poignee d'annonces. En la prenant pour
           reference, le modele a value une Five-SeveN Candy Apple 582 USD,
           puis 181, puis 121, la ou les exemplaires nus se vendent 85.
        3. Un outil qui decide de depenses reelles doit se tromper du cote
           prudent. Sous-estimer fait rater une occasion ; surestimer fait
           perdre de l'argent.

        La prime reste captable a l'execution : si votre sortie a un tres bon
        float, vous la vendrez peut-etre plus cher que prevu. Ce sera un bonus,
        pas une hypothese de calcul.
        """
        book = self._listings(name)
        if not book:
            return None
        return min(prix for _, prix in book)

    def _quote_de_vente(self, name: str) -> Quote | None:
        """Cotation du marche de REVENTE, gardee en memoire.

        Elle porte aussi le volume : la liquidite arrive donc sans requete
        supplementaire, la ou l'interroger sur CSFloat coute un appel de plus.
        """
        _verifie_budget(self.deadline)
        if self.revente_en_panne:
            # Ne plus interroger un marche dont on sait qu'il refuse : chaque
            # tentative coute 75 s de backoff (5+10+20+40) pour un echec
            # certain. Sans ce court-circuit, le repli fonctionne mais le
            # balayage reste aussi lent qu'avant -- 37 minutes d'attente par
            # collection, pour un resultat connu des le troisieme nom.
            return None
        if name not in self._ventes:
            try:
                self._ventes[name] = self.sell_source.fetch(name)
                self.echecs_vente = 0
            except Exception:  # noqa: BLE001 - une sortie non cotee vaut None
                self.echecs_vente += 1
                log.warning("Prix de revente indisponible pour %s", name)
                self._ventes[name] = None
        return self._ventes[name]

    @property
    def revente_en_panne(self) -> bool:
        """Le marche de revente refuse-t-il systematiquement ?

        Trois refus d'affilee ne sont plus un hasard. Continuer coute 75 s de
        backoff par nom pour un resultat connu : mieux vaut rendre la main et
        reprendre quand le marche rouvre.
        """
        return self.echecs_vente >= 3

    def _net_ici(self, name: str) -> float | None:
        """Valorisation de REPLI : la sortie revendue sur CSFloat.

        Mesure en direct sur trois sorties Mil-Spec : net Steam 4,01 / 2,09 /
        1,00 EUR contre 3,42 / 1,52 / 0,75 sur CSFloat, soit **17 a 37 % de
        moins**. Le repli est donc conservateur par construction : un contrat
        rentable ainsi valorise l'est FORCEMENT sur Steam. Il ne produit pas
        de faux positifs, seulement des occasions manquees -- c'est le bon
        sens de l'erreur pour un outil qui engage de l'argent reel.

        Sans ce repli, une panne du marche de revente rend tout le balayage
        inexploitable : mesure du 22 septembre, 9 collections sur 88 en 155
        minutes et zero enregistree.
        """
        if self.source is None:
            # Aucun marche d'achat pour se rabattre : la sortie n'a pas de
            # prix, et c'est un fait, pas une panne a masquer.
            return None
        p = self._lowest(name)
        if p is None:
            return None
        self.replis += 1
        return p * (1 - self.sell_fee) * (1 - self.safety_margin)

    def _net_ailleurs(self, name: str) -> float | None:
        q = self._quote_de_vente(name)
        if q is None:
            return self._net_ici(name)
        affiche = (
            q.realised_reference() if self.sell_basis == "sales"
            else q.sell_reference()
        )
        if affiche is None:
            return self._net_ici(name)
        # `net_from_sale` du modele Steam reproduit l'arrondi reel, plancher de
        # 0.01 par frais compris. Un pourcentage moyen ne le remplacerait pas :
        # c'est precisement en bas de l'echelle que l'ecart devient decisif.
        net = self.sell_fees.net_from_sale(affiche)
        return net * self.sell_to_usd * (1 - self.safety_margin)

    def sell_net_at_float(
        self, skin, wear, stattrak: bool, float_value: float
    ) -> float | None:
        # Steam n'affiche pas le float, donc ne le price pas : dans un meme
        # palier, deux exemplaires s'y vendent au meme prix. Quand la revente
        # passe par la, viser un float tres bas ne rapporte rien de plus -- le
        # palier d'usure est tout ce qui compte.
        if self.sell_source is not None:
            return self.sell_net(skin, wear, stattrak)
        p = self.price_at_float(skin.market_hash_name(wear, stattrak), float_value)
        if p is None:
            return None
        return p * (1 - self.sell_fee) * (1 - self.safety_margin)

    def sell_net(self, skin, wear, stattrak=False) -> float | None:
        name = skin.market_hash_name(wear, stattrak)
        if self.sell_source is not None:
            return self._net_ailleurs(name)
        p = self._lowest(name)
        if p is None:
            return None
        return p * (1 - self.sell_fee) * (1 - self.safety_margin)

    def buy_cost(self, skin, wear, stattrak=False) -> float | None:
        return self._lowest(skin.market_hash_name(wear, stattrak))

    def volume(self, skin, wear, stattrak=False) -> int | None:
        stats = self.sales_stats(skin.market_hash_name(wear, stattrak))
        return stats["ventes_jour"] if stats else None

    def _stats_du_marche_de_vente(self, name: str) -> dict | None:
        """Liquidite lue sur le marche ou l'on vendra, pas sur l'autre.

        Les volumes des deux marches n'ont rien a voir : mesure sur des sorties
        Mil-Spec, 98 et 83 ventes par jour sur Steam contre 8 et 10 sur
        CSFloat. Annoncer un delai de revente d'apres le mauvais carnet le
        surestime d'un ordre de grandeur.
        """
        q = self._quote_de_vente(name)
        if q is None or q.volume is None:
            return None
        return {
            "ventes_jour": q.volume,
            "prix_median": q.median_price,
            "ventes_observees": q.volume,
        }

    def sales_stats(self, name: str) -> dict | None:
        """Liquidite reelle : ventes par jour et prix median effectivement paye.

        `/history/{nom}/graph` donne le nombre de ventes par jour, et
        `/history/{nom}/sales` les transactions conclues. C'est ce qui dit
        combien de temps une revente prendra -- l'information qui manquait quand
        un contrat annonce a +0.37 s'est solde par -0.02, faute d'avoir vendu au
        prix du marche.

        Le prix median des ventes n'entre PAS dans la valorisation : il est
        souvent superieur au prix demande le plus bas, donc l'utiliser rendrait
        le modele plus optimiste. Il sert a informer, pas a calculer.
        """
        if self.sell_source is not None:
            return self._stats_du_marche_de_vente(name)
        if name in self._stats:
            return self._stats[name]
        try:
            jours = self.source.daily_sales(name, jours=7)
            ventes = [v.price for v in self.source.sales_history(name) if v.plain]
        except Exception:  # noqa: BLE001 - la liquidite est un complement
            self._stats[name] = None
            return None

        if not jours:
            self._stats[name] = None
            return None
        recents = [j["ventes"] for j in jours]
        self._stats[name] = {
            "ventes_jour": round(sum(recents) / len(recents)),
            "prix_median": (sorted(ventes)[len(ventes) // 2] if ventes else None),
            "ventes_observees": len(ventes),
        }
        return self._stats[name]


@dataclass(frozen=True, slots=True)
class Plan:
    """Un panier concret, achetable en l'etat."""

    result: TradeUpResult
    options: tuple[InputOption, ...]
    collection: Collection
    listings_examined: int
    # La rarete d'ENTREE. Une meme collection donne un plan different par
    # rarete, et rien ne les distinguait : ni le titre du rapport, ni son nom de
    # fichier, ni l'historique. Deux plans pouvaient meme s'ecraser sur disque.
    rarity: Rarity = Rarity.MIL_SPEC

    downgrade_net: float | None = None  # valeur nette si la sortie perd un palier

    @property
    def downgrade_profit(self) -> float | None:
        """Profit si la moyenne franchit la frontiere -- le vrai risque ici."""
        if self.downgrade_net is None:
            return None
        return self.downgrade_net - self.result.cost

    # Nombre de sorties valorisees par repli sur CSFloat faute de prix Steam.
    # Un plan qui en contient est PRUDENT, pas exact : la revente reelle sur
    # Steam rapporterait 17 a 37 % de plus.
    replis: int = 0

    # Cout du MEME panier achete sur le marche de revente (Steam) au lieu du
    # marche d'achat (CSFloat). None si un prix manque.
    #
    # Ce n'est pas une curiosite : un utilisateur qui verifie une entree le
    # fait sur Steam, y trouve un prix plus eleve, et conclut a une erreur du
    # modele. Mesure sur The Dead Hand Collection le 27 septembre -- 1,26 EUR
    # sur CSFloat contre 1,74 sur Steam, soit 38 % d'ecart et 108 % contre
    # 78 % de profitabilite. Les deux chiffres sont justes ; les montrer
    # ensemble evite de croire que l'un remplace l'autre, et dit si un contrat
    # ne tient QUE grace a l'ecart entre les deux marches.
    alt_cost: float | None = None

    # Prix unitaire de chaque entree sur le marche de revente, par nom. Meme
    # cotation que `alt_cost`, conservee au lieu d'etre sommee puis jetee :
    # c'est ce qui permet de donner un PRIX D'ORDRE par objet plutot qu'un
    # budget global. Un ordre d'achat se place sur un objet a un prix.
    alt_prices: dict[str, float] | None = None

    # Cout du MEME panier si les `PROFONDEUR_TESTEE` annonces les moins cheres
    # de chaque objet ont ete achetees avant vous. None si le panier devient
    # impossible a composer.
    #
    # C'est la question qui separe une occasion d'une course. `plan` retient
    # par construction les annonces les MOINS cheres : tout autre acheteur
    # faisant le meme calcul les prend avant vous. Constate -- cinq entrees
    # annoncees a 0,06 valaient 0,08 a 0,19 quinze heures plus tard.
    #
    # Mesure du 28 septembre 2026 : sur The Dead Hand Collection, 942 annonces
    # exploitables et seulement 5 points perdus a profondeur 4 -- le carnet est
    # profond, le contrat n'est pas une course. Sur The Bank Collection en
    # Industrial, ou il fallait du Factory New sous 0,026, il n'existait qu'une
    # poignee d'annonces et le prix passait de 0,19 a 0,48. Le contrat ne
    # differe pas : c'est la PROFONDEUR au float exige qui differe, et rien ne
    # l'affichait.
    deep_cost: float | None = None
    # La contrainte de float du panier : somme maximale des floats normalises
    # des 10 entrees pour que les sorties restent a leur palier. Les paniers
    # suivants s'y tiennent, sans quoi ils changeraient de contrat.
    float_budget: float | None = None
    # Les paniers n. 2, 3... composes avec les annonces restantes. Une annonce
    # est unique : le panier n. 1 ne se refait pas, mais le carnet en contient
    # souvent d'autres. Les acheter le meme jour fait tomber tous les verrous
    # de 7 jours ensemble -- plusieurs contrats pour un seul cycle.
    next_baskets: tuple[tuple[InputOption, ...], ...] = ()
    # Le marche ou la sortie a ete valorisee. Un plan estime sur Steam et un
    # plan estime sur CSFloat ne se comparent pas : le premier compte en
    # monnaie Steam, le second en argent retirable.
    sell_market: str = "CSFloat"

    def basket_profitability(self, panier) -> float | None:
        """Profitabilite d'un panier suivant : meme EV, autre cout."""
        cout = sum(o.unit_cost for o in panier)
        return self.result.ev_net / cout if cout else None

    @property
    def valorisation_de_repli(self) -> bool:
        return self.replis > 0

    @property
    def deep_profitability(self) -> float | None:
        """Profitabilite si l'on n'arrive pas premier sur le carnet."""
        if not self.deep_cost:
            return None
        return self.result.ev_net / self.deep_cost

    @property
    def fragile(self) -> bool:
        """Le contrat repose-t-il sur le fait d'arriver premier ?

        Vrai s'il passe sous le point mort des que quelques annonces sont
        prises, ou si le panier devient carrement impossible a composer.
        """
        if self.deep_cost is None:
            return True
        p = self.deep_profitability
        return p is not None and p < 1.0 <= self.result.profitability

    @property
    def float_subi_compatible(self) -> bool | None:
        """Les sorties gardent-elles leur palier si le float est SUBI ?

        Un ordre d'achat porte sur une USURE, pas sur un float : on ne choisit
        pas la qualite. La question decide si la voie "ordre Steam" est
        ouverte, et elle se tranche sans aucune requete -- on repose les
        entrees au milieu de leur palier et on regarde si les sorties bougent.

        Mesure sur The Arabesque Collection : les annonces retenues avaient des
        floats de 0,124 a 0,148, soit le HAUT du palier Minimal Wear, choisies
        parce que les moins cheres. Un tirage au hasard donne une moyenne PLUS
        BASSE (0,1255) et les trois sorties restent Minimal Wear. Le contrat
        n'exige donc aucun tri.
        """
        if not self.options or not self.result.outcomes:
            return None
        from .wear import output_float, wear_of

        total = 0.0
        for o in self.options:
            s, w = o.skin, o.wear
            bas = max(s.min_float, w.lo)
            haut = min(s.max_float, w.hi)
            if haut <= bas or s.float_span <= 0:
                return None
            total += ((bas + haut) / 2 - s.min_float) / s.float_span
        moyenne = total / len(self.options)

        for sortie in self.result.outcomes:
            skin = getattr(sortie, "skin", None)
            if skin is None:
                return None
            if wear_of(output_float(moyenne, skin)) is not sortie.wear:
                return False
        return True

    def steam_order_budget(self, target_roi: float = 0.20) -> float | None:
        """Ce qu'il faut payer AU TOTAL sur Steam pour viser ce rendement.

        Meme inversion que la commande `orders` : la valeur de la sortie ne
        depend pas de ce qu'on a paye les entrees, donc le budget se deduit de
        l'EV et le prix d'ordre suit.
        """
        if not self.result.ev_net:
            return None
        return self.result.ev_net / (1.0 + target_roi)

    def steam_order_discount(self, target_roi: float = 0.20) -> float | None:
        """Rabais a obtenir sur le prix Steam affiche pour tenir ce rendement."""
        budget = self.steam_order_budget(target_roi)
        if budget is None or not self.alt_cost:
            return None
        return 1.0 - budget / self.alt_cost

    def steam_order_lines(
        self, target_roi: float = 0.20
    ) -> tuple[dict, ...] | None:
        """Le prix d'ordre a placer sur CHAQUE objet, pas seulement le budget.

        Un budget total ne se place pas : on pose un ordre par objet, a un
        prix. Le budget est reparti proportionnellement aux prix du marche --
        le meme rabais partout --, comme le fait `orders.plan_orders`. Repartir
        autrement supposerait de savoir sur quels objets les vendeurs cedent le
        plus, ce qu'aucune donnee ici ne dit.

        Aucun appel reseau : les prix Steam sont deja dans `alt_prices`.
        """
        from .orders import STEAM_MIN_PRICE, arrondi_bas_centime

        budget = self.steam_order_budget(target_roi)
        if budget is None or not self.alt_cost or not self.alt_prices:
            return None
        facteur = budget / self.alt_cost

        lignes = []
        for nom, qte in _quantites(self.options).items():
            marche = self.alt_prices.get(nom)
            if marche is None:
                return None
            # Arrondi vers le BAS au centime : arrondir vers le haut
            # depasserait le budget et mangerait la marge visee.
            ordre = arrondi_bas_centime(marche * facteur)
            lignes.append({
                "name": nom,
                "quantity": qte,
                "market_price": marche,
                "order_price": ordre,
                # Un ordre sous le plancher de Steam ne sera jamais servi :
                # aucun rabais ne peut sauver la ligne.
                "below_floor": ordre < STEAM_MIN_PRICE,
            })
        return tuple(lignes)

    @property
    def alt_profitability(self) -> float | None:
        """Profitabilite si l'on achetait sur le marche de revente."""
        if not self.alt_cost:
            return None
        return self.result.ev_net / self.alt_cost

    exit_value: float | None = None  # produit net d'une revente des entrees
    liquidity: dict = field(default_factory=dict)  # nom de sortie -> stats de vente

    @property
    def slowest_outcome(self) -> tuple[str, dict] | None:
        """La sortie la plus lente a revendre, celle qui dicte votre delai.

        Le prix affiche suppose qu'on vend AU MARCHE. Sur un skin qui s'echange
        trois fois par jour, y parvenir demande de patienter ; brader pour aller
        vite coute environ un tiers de la valeur -- mesure sur un contrat reel
        annonce a +0.37 et solde a -0.02.
        """
        connus = {n: s for n, s in self.liquidity.items() if s}
        if not connus:
            return None
        return min(connus.items(), key=lambda kv: kv[1].get("ventes_jour", 0))

    # --- Ce qui compte vraiment : le pire cas couvre-t-il la mise ? ---

    @property
    def worst_profit(self) -> float | None:
        """Profit de la PIRE sortie possible."""
        if not self.result.outcomes:
            return None
        return min(o.net_value for o in self.result.outcomes) - self.result.cost

    @property
    def best_profit(self) -> float | None:
        if not self.result.outcomes:
            return None
        return max(o.net_value for o in self.result.outcomes) - self.result.cost

    @property
    def all_outcomes_profitable(self) -> bool:
        """Vrai si CHAQUE sortie couvre la mise : on gagne quoi qu'il arrive.

        C'est le bon critere de securite, et il rend caduc le classement par
        nombre de sorties. Un contrat a 3 issues toutes rentables vaut mieux
        qu'un contrat a issue unique dont le gain est marginal : dans le premier
        le tirage ne peut pas vous faire perdre, dans le second une variation de
        prix suffit.

        Le nombre de sorties reste utile AVANT d'avoir les prix -- c'est la
        seule information disponible gratuitement -- mais il ne doit pas servir
        de verdict une fois le plan calcule.
        """
        pire = self.worst_profit
        return pire is not None and pire > 0

    @property
    def exit_loss(self) -> float | None:
        """Cout d'un renoncement : acheter les entrees puis les revendre.

        Repond a la seule objection serieuse contre l'attente de 7 jours : "si
        le contrat n'est plus rentable dans une semaine, je suis coince avec
        des skins achetes". On n'est pas coince. Au bout des 7 jours les objets
        sont libres, et on peut les REVENDRE au lieu de les fusionner.

        Le risque reel n'est donc pas de perdre la mise, mais de perdre le
        frottement d'un aller-retour : les frais de vente et l'ecart entre le
        prix d'achat et celui qu'on obtiendra en revendant.
        """
        if self.exit_value is None:
            return None
        return self.exit_value - self.result.cost

    @property
    def exit_loss_ratio(self) -> float | None:
        perte = self.exit_loss
        if perte is None or not self.result.cost:
            return None
        return perte / self.result.cost

    @property
    def price_drop_tolerance(self) -> float | None:
        """Baisse du prix de sortie supportable avant de perdre de l'argent.

        Le blocage d'echange de 7 jours cree une exposition reelle : le cout
        d'achat est fige aujourd'hui, la revente se fera une semaine plus tard.
        Cette fraction dit combien le marche peut baisser d'ici la avant que le
        contrat ne devienne perdant.
        """
        if self.result.ev_net <= 0:
            return None
        marge = 1.0 - self.result.cost / self.result.ev_net
        # Sur un contrat deja perdant, cette "marge" est negative et n'a aucun
        # sens : il n'y a pas de baisse a encaisser avant de perdre, on perd
        # deja. Afficher "-120 %" laissait croire a un bug de calcul.
        return marge if marge > 0 else None

    @property
    def float_slack(self) -> float:
        """Tolerance en float, sur la SOMME des 10 entrees.

        C'est la grandeur qui parle a l'execution : de combien le float total
        peut deriver (annonce vendue, remplacee par une autre) avant que la
        sortie ne change de palier.
        """
        return self.result.cliff_distance * TRADEUP_INPUT_COUNT

    def shopping_lines(self, rate: float = 1.0) -> list[str]:
        """Les lignes d'achat, prix convertis par `rate` (1 USD en devise)."""
        lignes = []
        for opt in sorted(self.options, key=lambda o: (o.skin.name, o.float_value)):
            lignes.append(
                f"  {opt.name:<46} {opt.unit_cost * rate:>7.2f}"
                f"  float {opt.float_value:.4f}"
            )
        return lignes


def build_plan(
    db: SkinDatabase,
    collection: Collection,
    rarity: Rarity,
    source: CSFloat,
    *,
    listings_per_skin: int = 30,
    safety_margin: float = 0.05,
    sell_fee: float = 0.02,
    float_margin: float = 0.005,
    sell_source: PriceSource | None = None,
    sell_fees: FeeModel | None = None,
    sell_to_usd: float = 1.0,
    sell_basis: str = "sales",
    deadline: float | None = None,
) -> Plan | None:
    """Construit le meilleur panier realisable avec ce qui est en vente.

    Balaye les paliers d'usure de la sortie (l'EV est constante par morceaux) et,
    pour chacun, cherche les 10 annonces les moins cheres dont la moyenne de
    float tient sous la frontiere.

    `sell_source` designe le marche de REVENTE de la sortie. Sans lui, la
    sortie est valorisee au prix CSFloat moins 2 % -- ce qui suppose qu'on
    revend la ou l'on a achete. Avec lui (typiquement Steam), la sortie est
    cotee sur ce marche et nette de SES frais : c'est la seule facon de dire si
    un contrat rapporte, puisque c'est ce qu'on encaisse qui compte.

    `sell_to_usd` ramene ce produit de vente dans la devise des couts. Les
    frais s'appliquent AVANT la conversion : le plancher de Steam vaut 0,01
    dans la devise du compte, et l'appliquer sur un montant converti le
    deplacerait de quelques points juste la ou il decide.

    `float_margin` protege d'un risque different de celui du scan Steam. Ici les
    floats sont connus : il n'y a pas de tirage. Mais les 10 annonces sont des
    objets publics que n'importe qui peut acheter avant vous. Si l'une part et
    que la remplacante a un float un peu superieur, la moyenne franchit la
    frontiere et la sortie perd un palier -- ce qui, sur The Bank, transforme
    +5.35 en -7.53. La marge achete de la tolerance a la substitution.
    """
    outcomes = collection.outcomes_for_input_rarity(rarity)
    if not outcomes:
        return None

    # --- Recuperer les offres reelles de chaque entree possible ---
    toutes: list[InputOption] = []
    examinees = 0
    for skin in collection.by_rarity(rarity):
        for wear in skin.available_wears():
            _verifie_budget(deadline)
            offres = source.listings(
                skin.market_hash_name(wear), limit=listings_per_skin
            )
            examinees += len(offres)
            toutes.extend(options_from_listings(skin, offres))

    if len(toutes) < TRADEUP_INPUT_COUNT:
        log.info("Seulement %d offres pour %s : insuffisant", len(toutes), collection.name)
        return None

    pricer = CSFloatPricer(source, sell_fee=sell_fee, safety_margin=safety_margin,
                           sell_source=sell_source, sell_fees=sell_fees,
                           sell_to_usd=sell_to_usd, sell_basis=sell_basis,
                           deadline=deadline)
    outcomes_map = {collection.id: outcomes}

    meilleur: Plan | None = None
    frontieres = wear_breakpoints(outcomes)
    for hi in _points_a_tester(outcomes):
        _verifie_budget(deadline)
        budget = (hi - max(float_margin, 1e-9)) * TRADEUP_INPUT_COUNT
        selection = cheapest_unique_selection(toutes, TRADEUP_INPUT_COUNT, budget)
        if selection is None:
            continue

        items = [
            InputItem(skin=o.skin, float_value=o.float_value, unit_cost=o.unit_cost)
            for o in selection
        ]
        atteint = sum(o.normalized for o in selection) / TRADEUP_INPUT_COUNT
        result = evaluate(
            items, outcomes_map, pricer,
            cliff_distance=_distance_a_la_falaise(atteint, frontieres),
        )
        if meilleur is None or result.ev_profit > meilleur.result.ev_profit:
            meilleur = Plan(
                result=result,
                options=tuple(selection),
                collection=collection,
                rarity=rarity,
                listings_examined=examinees,
                float_budget=budget,
                sell_market=nom_marche(sell_source),
                replis=pricer.replis,
                downgrade_net=_net_si_palier_rate(result, pricer),
                exit_value=_valeur_de_revente(selection, pricer, sell_fee),
                liquidity={
                    o.name: pricer.sales_stats(o.name) for o in result.outcomes
                },
            )

    if meilleur is not None:
        from dataclasses import replace

        ailleurs = (_cout_ailleurs(sell_source, meilleur.options, sell_to_usd)
                    if sell_source is not None else None)
        meilleur = replace(
            meilleur,
            alt_cost=ailleurs[0] if ailleurs else None,
            alt_prices=ailleurs[1] if ailleurs else None,
            deep_cost=_cout_en_profondeur(toutes, meilleur),
        )
        meilleur = replace(meilleur, next_baskets=_paniers_suivants(toutes, meilleur))
    return meilleur


def nom_marche(sell_source: PriceSource | None) -> str:
    """Le marche de revente d'un plan, tel que le payload et la page le nomment.

    Sans source de revente, `CSFloatPricer` valorise la sortie sur CSFloat
    lui-meme : c'est le marche d'achat.
    """
    if sell_source is None:
        return "CSFloat"
    return {"steam": "Steam", "csfloat": "CSFloat"}.get(
        sell_source.name, sell_source.name)


#: Paniers au plus, le premier compris. Au-dela, l'information ne change aucune
#: decision : dix contrats en parallele immobilisent deja dix paniers sous
#: verrou pendant 7 jours.
PANIERS_MAX = 10


def _paniers_suivants(toutes, plan) -> tuple[tuple[InputOption, ...], ...]:
    """Les paniers n. 2, 3... composes avec les annonces que le n. 1 laisse.

    Aucun appel reseau : toutes les annonces lues sont deja en memoire, le
    panier n. 1 n'en garde que dix. Chaque panier se tient a la MEME
    contrainte de float, donc aux memes sorties et a la meme EV -- seul son
    cout change -- puis retire ses annonces du lot. On s'arrete au premier
    panier impossible ou sous le point mort : les suivants coutent plus cher.

    L'EV reprise est celle du n. 1. Un panier suivant peut viser plus bas en
    float et sortir mieux, jamais moins bien : l'erreur est du cote prudent.
    """
    if plan.float_budget is None:
        return ()

    def cle(o):
        return o.listing_id or id(o)

    prises = {cle(o) for o in plan.options}
    restantes = [o for o in toutes if cle(o) not in prises]
    paniers = []
    while len(paniers) < PANIERS_MAX - 1:
        selection = cheapest_unique_selection(
            restantes, TRADEUP_INPUT_COUNT, plan.float_budget
        )
        if selection is None:
            break
        prof = plan.basket_profitability(selection)
        if prof is None or prof < 1.0:
            break
        paniers.append(tuple(selection))
        prises = {cle(o) for o in selection}
        restantes = [o for o in restantes if cle(o) not in prises]
    return tuple(paniers)


#: Nombre d'annonces les moins cheres supposees prises avant nous, par objet.
#: Trois : assez pour distinguer un carnet profond d'une course, assez peu pour
#: rester un scenario plausible a l'echelle de quelques heures.
PROFONDEUR_TESTEE = 3


def _cout_en_profondeur(toutes, plan) -> float | None:
    """Cout du meme panier sans les `PROFONDEUR_TESTEE` moins cheres par objet.

    Aucun appel reseau : les annonces sont deja en memoire. On refait seulement
    la selection, sous la MEME contrainte de float -- comparer a budget de
    float different ne dirait rien.
    """
    budget = sum(o.normalized for o in plan.options)

    par_nom = {}
    for o in toutes:
        par_nom.setdefault(o.name, []).append(o)

    restantes = []
    for lot in par_nom.values():
        lot.sort(key=lambda o: o.unit_cost)
        restantes.extend(lot[PROFONDEUR_TESTEE:])

    selection = cheapest_unique_selection(restantes, TRADEUP_INPUT_COUNT, budget)
    if selection is None:
        return None
    return sum(o.unit_cost for o in selection)


def _quantites(options) -> Counter[str]:
    """Le nombre d'exemplaires de chaque objet du panier, dans l'ordre du panier.

    Partage par le cout sur l'autre marche et par les lignes d'ordre : deux
    regroupements ecrits a part finiraient par compter differemment, et les
    ordres ne correspondraient plus au cout affiche.
    """
    return Counter(o.name for o in options)


def _cout_ailleurs(
    source: PriceSource, options, vers_usd: float
) -> tuple[float, dict[str, float]] | None:
    """Ce que couterait le MEME panier sur le marche de revente.

    Une cotation par nom distinct, pas par exemplaire : sur Steam le prix ne
    depend que du palier d'usure. Si un seul prix manque, on renvoie None
    plutot qu'un total partiel -- un cout incomplet compare a un cout complet
    donnerait un ecart inventé.

    Renvoie le total ET le prix unitaire par nom. Le detail coute zero appel
    de plus -- il est deja en main -- et c'est lui qui permet de deduire un
    PRIX D'ORDRE par objet : un ordre se place sur un objet a un prix, pas sur
    un panier a un total.
    """
    total = 0.0
    unitaires: dict[str, float] = {}
    for nom, qte in _quantites(options).items():
        try:
            q = source.fetch(nom)
        except Exception:  # noqa: BLE001 - marche indisponible
            return None
        if q is None:
            return None
        prix = q.buy_reference()
        if prix is None:
            return None
        unitaires[nom] = prix * vers_usd
        total += qte * unitaires[nom]
    return total, unitaires


def _valeur_de_revente(
    selection: list[InputOption], pricer: CSFloatPricer, sell_fee: float
) -> float | None:
    """Ce qu'on recupererait en revendant les entrees au lieu de les fusionner.

    On se valorise au prix qu'il faudra battre pour vendre -- la moins chere
    annonce nue du palier -- diminue des frais. C'est volontairement pessimiste
    et sans decote supplementaire : il s'agit d'une porte de sortie, pas d'une
    operation qu'on cherche a rendre attirante.
    """
    total = 0.0
    for opt in selection:
        prix = pricer.price_at_float(opt.name, opt.float_value)
        if prix is None:
            return None
        total += prix * (1 - sell_fee)
    return total


def _distance_a_la_falaise(atteint: float, frontieres: list[float]) -> float:
    """Distance entre la moyenne atteinte et la VRAIE frontiere d'usure au-dessus.

    Pas la limite qu'on a donnee a la selection : le panier le moins cher sous
    une limite peut tomber un palier plus bas, et sa falaise est alors plus
    proche que la limite. Mesurer par rapport a la limite annoncait une
    tolerance fausse -- et rassurante : 0,027 affiche pour 0,001 reel sur un
    plan, mesure sur un faux carnet en octobre 2026.
    """
    au_dessus = [f for f in frontieres if f > atteint + 1e-12]
    return (min(au_dessus) if au_dessus else 1.0) - atteint


def _points_a_tester(outcomes: list) -> list[float]:
    """Moyennes d'entree a tester : les frontieres d'usure des sorties, seules.

    La valeur d'une sortie ne depend que de son palier d'usure -- la moins
    chere annonce nue du palier, prime de bas float ignoree, voir
    `CSFloatPricer.price_at_float`. L'EV est donc constante entre deux
    frontieres, et un point par palier suffit.

    Ajouter un point au float de chaque annonce de sortie, comme on le faisait
    du temps ou la valorisation suivait le float, ne changeait plus aucun
    resultat : mais il fallait pour cela lire les annonces de TOUTES les usures
    de TOUTES les sorties, y compris celles qu'aucun panier n'atteint -- du
    quota CSFloat et des minutes perdus a chaque collection.
    """
    return sorted(set(wear_breakpoints(outcomes)[1:]))


def _net_si_palier_rate(result: TradeUpResult, pricer: CSFloatPricer) -> float | None:
    """Valeur nette de la sortie si la moyenne franchit la frontiere.

    Sans ce chiffre, un plan affiche "+50 %" sans dire que l'echec coute -70 %.
    L'asymetrie est l'information decisive.
    """
    from .models import WEARS_ORDERED

    total = 0.0
    connu = False
    for outcome in result.outcomes:
        index = WEARS_ORDERED.index(outcome.wear)
        pire = WEARS_ORDERED[min(index + 1, len(WEARS_ORDERED) - 1)]
        if pire not in outcome.skin.available_wears():
            continue
        net = pricer.sell_net(outcome.skin, pire, result.stattrak)
        if net is not None:
            total += outcome.probability * net
            connu = True
    return total if connu else None


def plan_payload(plan: Plan, devise: str, conv=None) -> dict:
    """Serialise un plan pour le journal et pour l'interface.

    Il y en avait DEUX -- un dans `cli.py`, un dans `web.py` -- qui divergeaient
    champ par champ : `order_lines` aurait du etre ajoute aux deux, et un champ
    oublie d'un cote laisse un trou dans l'affichage sans erreur. Un seul
    producteur, une seule liste de champs.

    Le calcul se fait en USD de bout en bout -- CSFloat y cote, la revente
    Steam y est ramenee par `sell_to_usd`. La conversion vers la devise du
    compte n'a lieu QU'ICI, une seule fois : convertir en cours de route
    multiplierait les occasions de melanger les deux devises.
    """
    from .orders import STEAM_MIN_PRICE

    r = plan.result
    c = conv if conv is not None else (lambda v: round(v, 4))

    def opt(valeur):
        """Convertit, ou laisse None -- un None converti vaut 0, qui est faux."""
        return None if valeur is None else c(valeur)

    def arrondi(valeur):
        return None if valeur is None else round(valeur, 4)

    def ligne_ordre(ligne):
        # Le plancher de Steam vaut dans la devise du COMPTE, pas en USD ou le
        # calcul se fait : `below_floor` se rejuge donc apres conversion.
        prix = c(ligne["order_price"])
        return {
            "name": ligne["name"],
            "quantity": ligne["quantity"],
            "market_price": c(ligne["market_price"]),
            "order_price": prix,
            "below_floor": prix < STEAM_MIN_PRICE,
        }

    lignes = plan.steam_order_lines()
    return {
        "collection": plan.collection.name,
        # Une meme collection donne un plan different par rarete : sans elle,
        # deux lignes de l'historique sont indiscernables.
        "rarity": plan.rarity.label,
        "rarity_target": plan.rarity.next_up.label,
        "currency": devise,
        # Les entrees sont achetees sur CSFloat, la revente estimee sur le
        # marche du plan. Sans le dire, on verifie un prix d'entree sur Steam
        # et on conclut a
        # une erreur : mesure sur le M4A4 | Zubastick (WW), 0,07 EUR sur
        # CSFloat contre 0,11 sur Steam. Les deux chiffres sont justes.
        "buy_market": "CSFloat",
        "sell_market": plan.sell_market,
        "cost": c(r.cost),
        "net": c(r.ev_net),
        "profit": c(r.ev_profit),
        "roi": round(r.roi, 4),
        # Convention des guides : 1.0 = point mort, pas le profit.
        "profitability": round(r.profitability, 4),
        "win_probability": round(r.profit_probability, 4),
        "outcomes_count": r.distinct_outcomes,
        "stdev": c(r.stdev),
        "avg_float": round(r.avg_input_float, 5),
        "listings_examined": plan.listings_examined,
        "float_slack": round(plan.float_slack, 5),
        # Le meme panier achete sur Steam : dit si le contrat ne tient QUE
        # grace a l'ecart entre les deux marches.
        "cost_alt": opt(plan.alt_cost),
        "profitability_alt": arrondi(plan.alt_profitability),
        # Le meme panier si trois annonces par objet sont prises avant nous :
        # separe une occasion d'une course.
        "cost_deep": opt(plan.deep_cost),
        "profitability_deep": arrondi(plan.deep_profitability),
        "fragile": plan.fragile,
        # Les paniers suivants, faisables tout de suite avec les annonces
        # restantes : combien de contrats le carnet permet en parallele.
        "next_baskets": [
            {
                "cost": c(sum(o.unit_cost for o in panier)),
                "profitability": arrondi(plan.basket_profitability(panier)),
                "avg_float": round(
                    sum(o.float_value for o in panier) / len(panier), 5),
                "inputs": [
                    {"name": o.name, "float": round(o.float_value, 4),
                     "price": c(o.unit_cost), "url": o.url}
                    for o in sorted(panier,
                                    key=lambda o: (o.skin.name, o.float_value))
                ],
            }
            for panier in plan.next_baskets
        ],
        # La voie ORDRE STEAM : repetable, float subi.
        "float_subi_ok": plan.float_subi_compatible,
        "order_budget": opt(plan.steam_order_budget()),
        "order_discount": arrondi(plan.steam_order_discount()),
        # Le prix d'ordre OBJET PAR OBJET. Un budget total ne se place pas :
        # on pose un ordre par objet, a un prix. Sans ce detail, les ordres
        # devaient vivre dans un onglet separe qui refaisait tout le calcul.
        "order_lines": None if lignes is None else [ligne_ordre(l) for l in lignes],
        # Le plancher lui-meme, pour que la page n'en garde pas une copie qui
        # divergerait de `below_floor` si la constante changeait.
        "order_floor": STEAM_MIN_PRICE,
        "worst_profit": opt(plan.worst_profit),
        "best_profit": opt(plan.best_profit),
        "all_profitable": plan.all_outcomes_profitable,
        "replis": plan.replis,
        "downgrade_profit": opt(plan.downgrade_profit),
        "exit_loss": opt(plan.exit_loss),
        "exit_loss_ratio": arrondi(plan.exit_loss_ratio),
        "price_drop_tolerance": arrondi(plan.price_drop_tolerance),
        "inputs": [
            {"name": o.name, "float": round(o.float_value, 4),
             "price": c(o.unit_cost), "url": o.url}
            for o in sorted(plan.options,
                            key=lambda o: (o.skin.name, o.float_value))
        ],
        "outcomes": [
            {"name": o.name, "probability": round(o.probability, 4),
             "float": round(o.float_value, 4), "net": c(o.net_value)}
            for o in r.outcomes
        ],
    }
