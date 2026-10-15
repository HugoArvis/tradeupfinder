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
# tenait le verrou -- ni l'un ni l'autre n'est un echec. 75 = pas de reseau :
# echec temporaire, systemd relance le service plus tard.

cd "$(dirname "$0")/.." || exit 1

# Au reveil d'un portable, le Wi-Fi peut mettre plusieurs minutes a revenir.
# Mesure le 15 octobre 2026 : passage parti a 10:00, reseau absent, `sweep`
# echoue sur le taux de change -- et la journee etait perdue. On attend donc
# CSFloat jusqu'a 5 minutes ; sans lui, on sort en echec temporaire plutot que
# de lancer un balayage qui echouerait.
n=0
until getent hosts csfloat.com >/dev/null 2>&1; do
    n=$((n + 1))
    if [ "$n" -ge 30 ]; then
        echo "[$(date '+%d/%m/%Y %H:%M:%S')] pas de reseau, nouvel essai plus tard (code 75)" >> data/balayage.log
        exit 75
    fi
    sleep 10
done
PYTHON="${PYTHON:-python3}"

"$PYTHON" -m tradeupfinder.cli sweep --rarity "${RARETE:-mil-spec}" --rate 8 \
    --max-minutes "${MAX_MINUTES:-60}" >> data/balayage.log 2>&1
code=$?

echo "[$(date '+%d/%m/%Y %H:%M:%S')] code $code" >> data/balayage.log
exit $code
