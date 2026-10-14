#!/bin/sh
# Balayage d'une rarete, par morceaux, sous Linux.
#
# Pendant de balayage_nuit.cmd pour un portable qu'on ne laisse pas tourner la
# nuit : le minuteur systemd le lance une fois par jour, ou au demarrage si
# l'heure est passee machine eteinte, et `--max-minutes` borne chaque passage.
# La reprise de `sweep` fait le reste -- jamais calculees d'abord, puis les plus
# anciennes --, si bien que la couverture se complete sur quelques jours.
#
# Codes de sortie : 10 = au moins un contrat rentable, 3 = un autre balayage
# tenait le verrou. Ni l'un ni l'autre n'est un echec.

cd "$(dirname "$0")/.." || exit 1
PYTHON="${PYTHON:-python3}"

"$PYTHON" -m tradeupfinder.cli sweep --rarity "${RARETE:-mil-spec}" --rate 8 \
    --max-minutes "${MAX_MINUTES:-60}" >> data/balayage.log 2>&1
code=$?

echo "[$(date '+%d/%m/%Y %H:%M:%S')] code $code" >> data/balayage.log
exit $code
