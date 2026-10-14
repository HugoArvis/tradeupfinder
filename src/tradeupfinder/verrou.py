"""Un seul balayage a la fois, tous lanceurs confondus.

Deux balayages simultanes ont chacun leur instance CSFloat, donc leur
limiteur : le debit reel double et le quota saute pour une heure. L'application
refusait deja d'en lancer un second, mais ne voyait pas `sweep`, lance en ligne
de commande ou par le planificateur -- un processus a part. Le risque est
devenu courant avec la planification sous Linux : le balayage demarre seul a
l'allumage du portable, pendant qu'on ouvre l'application.

Le verrou est un fichier, parce que c'est ce que deux processus partagent sans
rien d'autre. Il porte le PID et l'heure de prise : un verrou dont le processus
est mort, ou trop vieux, est abandonne -- un plantage ne doit pas bloquer tous
les balayages suivants.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

#: Emplacement par defaut, a cote du journal.
CHEMIN = Path(__file__).resolve().parents[2] / "data" / "balayage.lock"

#: Au-dela, un verrou est tenu pour abandonne meme si son PID semble vivant :
#: un PID peut etre reattribue, et aucun balayage n'est cense durer autant (le
#: planificateur coupe a 3 h).
DUREE_MAX = 4 * 3600


def _vivant(pid: int) -> bool:
    """Le processus existe-t-il encore ? Sous Windows, on ne sait pas le dire
    sans dependance : on s'en remet alors a l'age du verrou."""
    if sys.platform == "win32":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # il existe, il appartient a un autre utilisateur
    return True


def occupant(chemin: Path | None = None) -> dict | None:
    """Qui tient le verrou, ou None s'il est libre ou abandonne."""
    chemin = chemin or CHEMIN
    try:
        info = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - info.get("depuis", 0) > DUREE_MAX:
        return None
    if not _vivant(int(info.get("pid", 0))):
        return None
    return info


def prendre(qui: str, chemin: Path | None = None) -> dict | None:
    """Prend le verrou. Renvoie None si c'est fait, l'occupant sinon.

    La creation est exclusive : si deux lanceurs demarrent dans la meme
    seconde, un seul l'obtient.
    """
    chemin = chemin or CHEMIN
    chemin.parent.mkdir(parents=True, exist_ok=True)
    info = {"pid": os.getpid(), "qui": qui, "depuis": time.time()}
    for _ in range(2):
        try:
            with open(chemin, "x", encoding="utf-8") as f:
                json.dump(info, f)
            return None
        except FileExistsError:
            tenu = occupant(chemin)
            if tenu is not None:
                return tenu
            # Abandonne : on le retire et on retente une fois.
            try:
                chemin.unlink()
            except FileNotFoundError:
                pass
    return occupant(chemin) or {"qui": "inconnu"}


def liberer(chemin: Path | None = None) -> None:
    """Rend le verrou -- seulement s'il est a nous."""
    chemin = chemin or CHEMIN
    try:
        info = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if info.get("pid") == os.getpid():
        try:
            chemin.unlink()
        except FileNotFoundError:
            pass
