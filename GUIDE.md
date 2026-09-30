# Guide — ce qu'il faut faire

Toutes les commandes se tapent dans PowerShell, testées sur cette machine.

> **La conclusion de toute l'analyse : travaillez uniquement sur CSFloat.**
> Steam coûte ~70 % plus cher à l'achat, ne permet pas de choisir les floats, et
> son porte-monnaie n'est pas retirable. Les commandes Steam existent encore
> dans l'outil, mais vous n'en avez pas besoin. Elles sont en annexe.

---

## Une seule fois : installer

```powershell
cd "c:\dossier temporaire pour contourne O le maudit\Bureau temporaire\projets vscode\tradeupfinder"
python -m pip install -e .
python -m scripts.build_db
```

> `python -m pip`, **pas** `pip` : sur cette machine `pip` appartient à Python 3.9
> et refuse d'installer.

Votre clé CSFloat est déjà enregistrée dans le fichier `.env`. Rien à faire.

Vérifiez que tout marche :

```powershell
python -m tradeupfinder.cli db
```

Vous devez voir `Base : 94 collections, 1451 skins`.

---

## L'application

Une seule commande, puis tout se fait à la souris :

```powershell
python -m tradeupfinder.web
```

Votre navigateur s'ouvre sur l'onglet **Calculer**, qui montre les contrats
rentables trouvés par le dernier balayage pour la rareté choisie. Rien n'est
recalculé à l'ouverture : un balayage complet dure plus d'une heure, il se fait
la nuit (`python -m tradeupfinder.cli schedule` le planifie sur cette machine,
via `scripts\balayage_nuit.cmd`). Seuls les contrats au-dessus du point mort sont
affichés — les autres sont perdants, il n'y a rien à en faire.

En haut, un bandeau résume la situation : la profitabilité de chaque contrat
(barre hachurée quand le tirage peut faire perdre), les chances de gagner du
meilleur, leur nombre et le meilleur gain.

Chaque contrat tient sur une carte : profitabilité, coût, revente nette, gain,
chances de gagner, puis **« Comment acheter les 10 entrées »**, qui compare les
voies d'approvisionnement (annonces CSFloat, ordre d'achat Steam, prix affiché
Steam). Les 10 annonces avec leurs boutons d'achat, les prix d'ordre et les
sorties possibles sont repliés en dessous.

`Ctrl+C` dans le terminal arrête le serveur.

> L'application n'écoute que sur `127.0.0.1` : elle détient votre clé API, elle
> n'a rien à faire sur le réseau. Personne d'autre ne peut y accéder.

### Relancer un balayage

Le bouton **« Relancer maintenant »** cote toutes les collections de la rareté,
une par une, et enregistre chaque plan au journal. Les contrats rentables
s'affichent au fil de l'eau, classés par profitabilité, et le cadran du bandeau
suit l'avancement. **Arrêter** interrompt le balayage.

Le coût en requêtes CSFloat est indiqué avant de lancer. Ordres de grandeur :

| Rareté | Collections | Requêtes | Durée |
|---|---|---|---|
| Consumer | 38 | ~980 | ~2 h |
| Mil-Spec | 88 | ~2 400 | ~4 h |
| Toutes raretés | 315 | ~8 000 | ~15 h |

**Le quota CSFloat vous arrêtera avant la fin.** C'est prévu : le balayage se
met en pause, affiche « reprise dans N secondes », et repart tout seul. La
collection en cours n'est pas perdue, elle est retentée.

### Trois onglets

**Calculer** — les contrats rentables du dernier balayage, et la relance.

**Mon inventaire** — les contrats réalisables avec les skins que vous possédez
déjà. Les entrées y sont valorisées à ce qu'elles rapporteraient **revendues** :
fondre un skin, c'est renoncer à le vendre. Ce que vous l'avez payé n'entre pas
dans le calcul — c'est déjà dépensé quoi que vous décidiez. Conséquence à
accepter : des contrats qui semblaient gratuits apparaissent perdants. Ils
l'étaient déjà, le coût était seulement invisible.

**Mes contrats** — le suivi de ce que vous avez réellement acheté, et en bas la
liste **Plans calculés** : tout plan y est conservé automatiquement, puisqu'il a
coûté des requêtes CSFloat. **Voir** le rouvre (un bouton ramène ensuite à la
liste), **Suivre** en fait un contrat.

### Suivre un contrat

C'est ce qui répond au problème de plusieurs trade-ups menés de front.

Les contrats suivis se rangent en colonnes selon leur avancement : **À
acheter**, **Achats en cours**, **Verrou 7 jours**, **Exécutables**. Le contrat
exécutable est mis en avant sur une carte foncée. Le bandeau du haut montre,
jour par jour sur la semaine à venir, quand chaque contrat en verrou deviendra
exécutable : c'est le jour où revenir.

Cliquez sur un contrat pour l'ouvrir en entier. Quand vous achetez un objet,
cliquez **Acheté** : on vous demande le prix et le float **réels**, qui peuvent
différer du plan si vous avez pris un substitut. **Annuler l'achat** revient en
arrière ; **Marquer réalisé**, **Abandonner** et **Supprimer** agissent sur le
contrat entier. **Annuler** (ou Échap) ferme la fenêtre.

L'application affiche alors :

- **la progression** (combien d'objets sur 10) et le dépensé réel face au prévu ;
- **la date d'exécution** — c'est le *dernier* objet acheté qui commande, son
  verrou de 7 jours expirant en dernier. Acheter étalé coûte des jours ;
- **la dérive de float** — si votre moyenne réelle s'écarte de plus de 0,003 de
  celle du plan, un avertissement apparaît. C'est le signal que la sortie a pu
  changer de palier d'usure, donc que le gain calculé ne tient plus.

Vos achats sont enregistrés dans `data/journal.db`, qui survit aux redémarrages.
Ce fichier contient vos données personnelles : il est exclu de git.

---

## En ligne de commande (si vous préférez)

### 1. Obtenir le panier à acheter

```powershell
python -m tradeupfinder.cli plan "The Bank Collection" --html
```

C'est **la** commande. Elle interroge les annonces réellement en vente sur
CSFloat et ouvre dans votre navigateur une page avec les 10 objets précis à
acheter — **chacun avec un bouton menant directement à son annonce**.

C'est le point important : sans ces liens, retrouver dix objets un par un par
leur float prend de longues minutes, pendant lesquelles certaines annonces sont
vendues. Votre moyenne de float change alors, et le contrat ne donne plus le
résultat calculé.

La page affiche son âge en haut. **Au-delà de 15 minutes, elle se signale
elle-même comme périmée** : relancez la commande.

Sans `--html`, tout s'affiche dans le terminal comme avant.

### 2. Vérifier juste avant d'acheter

Relancez la **même commande** au moment d'acheter. Les prix et les annonces
bougent d'heure en heure : le panier de ce matin n'est plus celui de ce soir.

C'est tout. Le reste de ce guide explique comment lire le résultat.

---

## Lire le résultat

```
Achat et revente sur CSFloat. Tous les montants sont en USD.

A ACHETER (offres reelles, floats exacts) :
  Desert Eagle | Meteorite (Factory New)      1.50  float 0.0145
  Desert Eagle | Meteorite (Factory New)      1.47  float 0.0296
  Desert Eagle | Meteorite (Minimal Wear)     1.08  float 0.0729
  ... (10 lignes au total)
  TOTAL                                      11.37  moyenne 0.0698

SORTIE :
  AK-47 | Emerald Pinstripe (Factory New)   100.0%  float 0.0698  net 16.02

Cout 11.37 | EV nette 16.02 | profit +4.65 (+40.9%)

TOLERANCE D'EXECUTION : 0.0505 de float sur la somme des 10 entrees.
    reussite : +4.65   |   palier rate : -7.96
```

**Les 10 lignes sont des annonces précises**, pas des types d'objets. Achetez
celles-là, au float indiqué.

**`100.0%`** signifie que la sortie est certaine : vous savez à l'avance que
vous obtiendrez un AK-47 Emerald Pinstripe. Aucun tirage au sort.

**La tolérance d'exécution** est le seul vrai risque. Si une annonce est vendue
avant vous, remplacez-la par une de float **comparable**. Si la somme des 10
floats dépasse la tolérance, la sortie tombe en Minimal Wear et vous perdez
~8 $ au lieu d'en gagner 4,65.

---

## Le déroulé complet, pas à pas

### Étape 1 — Vérifier le moteur dans le jeu (gratuit, à faire une fois)

Avant de dépenser quoi que ce soit. Lancez CS2, ouvrez un contrat d'échange,
placez-y des Mil-Spec de The Bank Collection — **sans valider**.

Le jeu doit annoncer **AK-47 | Emerald Pinstripe à 100 %**.

Si ça ne correspond pas, arrêtez tout et dites-le-moi : ça voudrait dire que le
calcul de l'outil est faux.

### Étape 2 — Obtenir le panier

```powershell
python -m tradeupfinder.cli plan "The Bank Collection"
```

### Étape 3 — Acheter sur CSFloat

Achetez les 10 annonces listées. Filtrez par float sur le site pour retrouver
les bonnes.

### Étape 4 — Faire le contrat dans CS2

Placez les 10 objets dans un contrat d'échange et validez.

> **Attendez 7 jours.** Les objets achetés sur CSFloat arrivent par échange
> entre joueurs : ils sont verrouillés une semaine et **ne peuvent pas** entrer
> dans un contrat pendant ce temps. Le marché Steam, lui, n'impose pas ce
> blocage pour le craft — mais il coûte ~70 % plus cher et ne permet pas de
> choisir les floats, ce qui annule largement l'avantage.
>
> Vous n'êtes pas engagé pour autant : au bout des 7 jours, si le contrat n'est
> plus rentable, revendez les entrées au lieu de les fusionner. L'application
> chiffre ce que coûte ce renoncement (de l'ordre de 7 % de la mise, contre
> plusieurs fois plus à gagner).

### Étape 5 — Revendre sur CSFloat

Mettez l'AK-47 en vente. **Notez combien de temps la vente prend** : c'est la
donnée qui manque pour savoir combien de fois répéter l'opération.

---

## Combien de fois répéter ?

**Je ne peux pas encore vous le dire**, et c'est honnête de le préciser.

La sortie étant certaine, il n'y a aucune incertitude statistique : un seul
contrat suffit à réaliser le gain. La limite est le **marché**, pas le hasard.

- Côté achat : au moins 50 Desert Eagle Meteorite en vente. Pas un problème
  immédiat.
- Côté revente : ~50 vendeurs déjà en file sur l'AK-47, et une dizaine de
  ventes par jour. **C'est là que ça coince.**

Faites-en **un seul**, revendez-le, et mesurez le délai de vente. Ce chiffre
vous dira tout.

---

## Si ça ne marche pas

| Message | Cause | Solution |
|---|---|---|
| `No module named tradeupfinder` | mauvais dossier | refaire le `cd` puis l'installation |
| `requires a different Python` | vous avez tapé `pip` | tapez `python -m pip` |
| `Base introuvable` | base non construite | `python -m scripts.build_db` |
| `Cle API CSFloat absente` | `.env` perdu | remettre `CSFLOAT_API_KEY=...` dedans |
| `429 persistant` | quota CSFloat épuisé | **attendre quelques minutes**, ne pas réessayer |
| `Aucun panier realisable` | pas assez d'annonces | réessayer plus tard |

Le `429` est le plus courant si vous enchaînez les commandes. CSFloat limite sur
une fenêtre longue : relancer immédiatement ne fait qu'aggraver. Attendez.

---

## Annexe — explorer d'autres collections

Pour voir quelles collections ont peu de sorties possibles (donc peu de
variance), sans aucune requête réseau :

```powershell
python -m tradeupfinder.cli collections --rarity mil-spec --max-outcomes 2
```

Les 9 collections marquées `<<` ont **une seule sortie possible**. Je les ai
toutes testées sur CSFloat : seule **The Bank** est nettement rentable
(+40,9 %), The Dust 2 l'est marginalement (+16,2 %), les autres sont perdantes.

Pour tester une autre collection :

```powershell
python -m tradeupfinder.cli plan "The Dust 2 Collection"
```

> Allez-y doucement : chaque `plan` consomme une trentaine de requêtes CSFloat
> et le quota est vite atteint.

## Annexe — les commandes Steam

`scan`, `price`, `cache` travaillent sur les prix Steam. Elles ont servi à
explorer le problème, mais Steam n'est pas le marché où exécuter. Vous pouvez
les ignorer. Voir le [README](README.md) si vous y revenez un jour.
