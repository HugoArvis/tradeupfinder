"""Reglages communs a tous les tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def verrou_isole(tmp_path, monkeypatch):
    """Le verrou de balayage vit dans un dossier temporaire pendant les tests.

    Sans cela, un test de balayage prendrait le VRAI `data/balayage.lock` : il
    se heurterait a un balayage en cours sur la machine, ou bloquerait le
    suivant.
    """
    from tradeupfinder import verrou

    monkeypatch.setattr(verrou, "CHEMIN", tmp_path / "balayage.lock")
    return verrou.CHEMIN
