"""Un seul balayage a la fois, que le lanceur soit l'application ou `sweep`."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

from tradeupfinder import verrou


def _ecrit(chemin, **info):
    chemin.write_text(json.dumps(info), encoding="utf-8")


def test_le_verrou_libre_se_prend_et_se_rend(verrou_isole):
    assert verrou.prendre("sweep") is None
    assert verrou.occupant()["qui"] == "sweep"
    verrou.liberer()
    assert not verrou_isole.exists()


def test_un_verrou_tenu_par_un_processus_vivant_refuse(verrou_isole):
    # Le processus parent du test est vivant, et ce n'est pas nous.
    _ecrit(verrou_isole, pid=os.getppid(), qui="application", depuis=time.time())
    tenu = verrou.prendre("sweep")
    assert tenu is not None and tenu["qui"] == "application"


@pytest.mark.skipif(sys.platform == "win32", reason="PID non verifie sous Windows")
def test_un_verrou_dont_le_processus_est_mort_est_repris(verrou_isole):
    """Un plantage ne doit pas bloquer tous les balayages suivants."""
    _ecrit(verrou_isole, pid=2 ** 22 + 12345, qui="sweep", depuis=time.time())
    assert verrou.prendre("application") is None


def test_un_verrou_trop_vieux_est_repris(verrou_isole):
    _ecrit(verrou_isole, pid=os.getppid(), qui="sweep",
           depuis=time.time() - verrou.DUREE_MAX - 1)
    assert verrou.prendre("application") is None


def test_on_ne_libere_pas_le_verrou_d_un_autre(verrou_isole):
    _ecrit(verrou_isole, pid=os.getppid(), qui="sweep", depuis=time.time())
    verrou.liberer()
    assert verrou_isole.exists()
