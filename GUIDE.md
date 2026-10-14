# Guide — ce qu'il faut faire

Toutes les commandes se tapent dans PowerShell, testées sur cette machine.

> **La conclusion de toute l'analyse : travaillez uniquement sur CSFloat.**
> Steam coûte ~70 % plus cher à l'achat, ne permet pas de choisir les floats, et
> son porte-monnaie n'est pas retirable. Les commandes Steam existent encore
> dans l'outil, mais vous n'en avez pas besoin. Elles sont en annexe.
>
> **Si vous voulez retirer l'argent, revendez la sortie sur CSFloat aussi.**
> Mesuré sur un contrat réel en octobre 2026 : la même sortie revendue sur
> CSFloat a rapporté **+0,27 €** en argent retirable ; revendue sur Steam puis
> ressortie (Steam → CSFloat ne rend que ~62 %), elle aurait **perdu 1,04 €**.

---

## Une seule fois : installer

```powershell
cd "c:\dossier temporaire pour contourne O le maudit\Bureau temporaire\projets vscode\tradeupfinder"
python -m pip install -e .
python -m scripts.build_db
```

> `python -m pip`, **pas** `pip` : sur cette machine `pip` appartient à Python 3.9
> et refuse d'installer.

Sous Linux (Ubuntu), Python refuse d'installer hors d'un environnement
virtuel. Une fois pour toutes :

```bash
sudo apt install python3-venv nodejs
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Toutes les commandes de ce guide s'écrivent alors avec `.venv/bin/python` au
lieu de `python` (ou après `source .venv/bin/activate`).

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

> **L'ordre d'achat Steam ne marche pas au rabais affiché.** Testé en
> octobre 2026 : deux ordres à −32 % sont restés 14 jours sans une seule
> exécution, sur des objets vendus 60 à 150 fois par jour. Steam sert d'abord
> l'ordre le plus haut ; un ordre n'est servi que quelques centimes sous la
> plus basse annonce. La carte le rappelle sur cette ligne.

**« Ouvrir les 10 annonces »**, sous la liste des annonces, ouvre chacune dans
un onglet : il ne reste qu'à confirmer l'achat sur chaque page (l'API CSFloat
n'a pas de panier). **La première fois, autorisez les fenêtres pop-up pour
`127.0.0.1:8765`** : sans cela le navigateur n'en ouvre qu'une, et la page vous
dit combien ont été bloquées.

**« Répéter maintenant »** liste les paniers n° 2, 3… que les annonces
restantes permettent de composer, sous la même contrainte de float : mêmes
sorties, coût un peu plus élevé à chaque panier. Achetés le même jour, leurs
verrous de 7 jours tombent ensemble — plusieurs contrats pour un seul cycle.
Chacun a ses liens et son bouton d'ouverture.

> **La carte estime la revente sur Steam**, en monnaie Steam. Si vous comptez
> retirer l'argent, préférez le chiffre de `plan` en ligne de commande, qui
> estime la revente sur CSFloat (voir plus bas) : c'est celui qui correspond à
> ce que vous encaisserez.

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

Ces durées supposent que Steam répond. Quand il refuse (erreur 429), chaque
collection perd environ 5 minutes en tentatives : mesuré en octobre 2026,
**10 à 15 minutes par collection**, et jusqu'à 39 pour une grosse collection de
caisse. Les sorties sont alors estimées sur CSFloat, ce que la carte signale
par « estimation prudente ».

**Le quota CSFloat vous arrêtera avant la fin.** C'est prévu : le balayage se
met en pause, affiche « reprise dans N secondes », et repart tout seul. La
collection en cours n'est pas perdue, elle est retentée.

**Un balayage reprend là où le précédent s'est arrêté.** Il commence par les
collections jamais calculées, puis les plus anciennes. Une collection calculée
il y a moins de 6 heures n'est pas recotée : son plan est repris tel quel et
s'affiche avec les autres. Relancer après une coupure ne refait donc pas le
travail déjà fait.

**Recharger la page ne coupe pas le balayage** : il tourne dans le serveur,
et la page le retrouve. **Arrêter le serveur, en revanche, le coupe**
(`Ctrl+C`, terminal fermé) : relancez le serveur, puis « Relancer
maintenant », et la reprise s'applique.

**Un seul balayage à la fois.** Deux en parallèle doubleraient le débit vers
CSFloat et épuiseraient le quota pour environ une heure : si vous cliquez
pendant qu'un balayage tourne, la page le dit et reprend son suivi. Cette
protection ne voit pas un `sweep` lancé en ligne de commande — n'en lancez pas
un en même temps.

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
elle-même comme périmée** : relancez la commande. Le bouton **« Ouvrir les 10
annonces »** les ouvre toutes en onglets (pop-up à autoriser la première fois).

Sans `--html`, tout s'affiche dans le terminal comme avant.

`plan` achète **et revend** sur CSFloat, et affiche les montants dans la
**devise de votre compte CSFloat** (euros chez vous). Si la devise ne peut pas
être lue — quota épuisé, par exemple —, il reste en USD et le signale.

### 2. Vérifier juste avant d'acheter

Relancez la **même commande** au moment d'acheter. Les prix et les annonces
bougent d'heure en heure : le panier de ce matin n'est plus celui de ce soir.

C'est tout. Le reste de ce guide explique comment lire le résultat.

---

## Lire le résultat

```
Achat et revente sur CSFloat. Tous les montants sont en EUR.

A ACHETER (offres reelles, floats exacts) :
  Desert Eagle | Meteorite (Factory New)      1.50  float 0.0145
  Desert Eagle | Meteorite (Factory New)      1.47  float 0.0296
  Desert Eagle | Meteorite (Minimal Wear)     1.08  float 0.0729
  ... (10 lignes au total)
  TOTAL                                      11.37  moyenne 0.0698

SORTIE :
  AK-47 | Emerald Pinstripe (Factory New)   100.0%  float 0.0698  net 16.02

Cout 11.37 | EV nette 16.02 | profit +4.65 (+40.9%)

REPETER MAINTENANT : 2 panier(s) de plus avec les annonces restantes, meme contrainte de float.
  panier n. 2 :   11.80  profitabilite 136%
  panier n. 3 :   12.42  profitabilite 129%

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
~8 au lieu d'en gagner 4,65.

**« Répéter maintenant »** : les paniers que les annonces restantes permettent
encore, chacun moins rentable que le précédent. La liste s'arrête au premier
panier sous le point mort. Les annonces lues sont limitées par objet : il peut
en exister davantage, jamais moins.

*(Les montants de cet exemple sont illustratifs.)*

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

Achetez les 10 annonces listées, d'un coup : le bouton **« Ouvrir les 10
annonces »** de la page HTML (ou de la carte dans l'application) les ouvre en
onglets.

Comptez avec les vendeurs : chaque annonce achetée doit encore vous être
envoyée par échange Steam. Mesuré sur un contrat réel, certains n'envoient
jamais, d'autres annulent l'échange après coup. Un remplaçant relance son
propre verrou de 7 jours : le cycle réel a été de **deux semaines**, pas une.
Prenez un remplaçant de float comparable (voir la tolérance d'exécution).

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

Mettez la sortie en vente sur CSFloat, **pas sur Steam** si vous voulez retirer
l'argent. **Notez le prix obtenu et le délai** : c'est ce qui dit si le contrat
tient vraiment.

Premier relevé, octobre 2026 : AUG | Lapis Lazuli (MW) vendue **5,28 €** en
moins d'une journée, environ **8 % sous la moins chère annonce** du moment. Le
contrat, payé 4,90 €, a rapporté **+0,27 €** en argent retirable. Un contrat
précédent avait subi 33 % de décote : au-delà d'environ 18 %, le gain
disparaît.

---

## Combien de fois répéter ?

Le verrou de 7 jours fixe la durée d'**un** cycle, pas le nombre de contrats.
Ce qui fait gagner plus, c'est d'en mener **plusieurs en parallèle**. Trois
voies, inégales :

1. **Les paniers suivants** (« Répéter maintenant ») — faisables tout de suite,
   avec les annonces déjà en vente. Leur nombre dépend de la profondeur du
   carnet au float exigé : 2 paniers de plus seulement sur The Overpass 2024
   Collection en octobre 2026, bien davantage sur une collection où beaucoup
   d'annonces passent le filtre.
2. **Un ordre d'achat CSFloat avec filtre de float** — il se répète sans
   surveiller, au prix du meilleur ordre existant (pas en dessous). Le délai de
   remplissage est **en cours de mesure** (test lancé le 14 octobre 2026).
3. **Un ordre d'achat Steam** — à éviter : au rabais affiché, il n'est pas
   servi (voir plus haut).

La limite suivante est la **revente** : une sortie qui ne se vend que quelques
fois par jour sur CSFloat ne s'écoule pas par dizaines. `plan` indique la
sortie la plus lente et son nombre de ventes quotidiennes.

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
| Une seule annonce s'ouvre sur « Ouvrir les N annonces » | pop-up bloquées | les autoriser pour la page, puis recliquer |
| « Un balayage … tourne déjà » | un balayage est en cours | rien à faire : la page reprend son suivi |
| `Devise du compte illisible : montants affiches en USD` | quota CSFloat épuisé au démarrage | relancer plus tard pour avoir les euros |
| Balayage très lent, « estimation prudente » partout | Steam refuse (429) | normal, il continue sur CSFloat ; ne pas le relancer |

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
