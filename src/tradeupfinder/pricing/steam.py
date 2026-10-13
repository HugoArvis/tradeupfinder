"""Source de prix Steam Community Market (endpoint `priceoverview`).

API non officielle et agressivement rate-limitee : en pratique une vingtaine
d'appels par minute et par IP avant de recevoir des 429 en rafale. Le defaut
retenu ici (15/min) laisse une marge.

Limite structurelle : `priceoverview` ne donne AUCUNE information de float. Il
sert a valoriser un couple (skin, usure) ; pour choisir des entrees a float
precis il faut passer par CSFloat.
"""

from __future__ import annotations

import logging

from .base import PriceSource, Quote, parse_money, parse_volume
from .cache import QuoteCache
from .http import HttpClient, RateLimiter

log = logging.getLogger(__name__)

PRICEOVERVIEW_URL = "https://steamcommunity.com/market/priceoverview/"
CS2_APPID = 730

# Codes de devise Steam les plus courants.
CURRENCIES = {"USD": 1, "GBP": 2, "EUR": 3, "CHF": 4, "RUB": 5, "BRL": 7, "CAD": 20}


class SteamMarket(PriceSource):
    name = "steam"

    def __init__(
        self,
        *,
        currency: str = "EUR",
        cache: QuoteCache | None = None,
        calls_per_minute: int = 15,
        client: HttpClient | None = None,
        ttl_seconds: float = 6 * 3600,
        offline: bool = False,
    ):
        if currency not in CURRENCIES:
            raise ValueError(
                f"Devise non supportee : {currency} (connues : {sorted(CURRENCIES)})"
            )
        if offline and cache is None:
            raise ValueError("Le mode hors ligne exige un cache")
        self.currency = currency
        self.cache = cache
        self.ttl = ttl_seconds
        self.offline = offline
        self.client = client or HttpClient(
            rate_limiter=RateLimiter(max_calls=calls_per_minute, period=60.0),
            headers={"Accept": "application/json, text/javascript, */*"},
        )
        self.stats = {"hits_cache": 0, "appels_api": 0, "introuvables": 0}

    def fetch(self, market_hash_name: str, *, use_cache: bool = True) -> Quote | None:
        if use_cache and self.cache:
            # Hors ligne, on accepte n'importe quel age : mieux vaut un prix
            # perime signale comme tel qu'aucun prix du tout.
            ttl = float("inf") if self.offline else self.ttl
            cached = self.cache.get(
                market_hash_name, self.name, ttl=ttl, currency=self.currency
            )
            if cached is not None:
                self.stats["hits_cache"] += 1
                return cached
        if self.offline:
            self.stats["introuvables"] += 1
            return None

        self.stats["appels_api"] += 1
        data = self.client.get_json(
            PRICEOVERVIEW_URL,
            {
                "appid": CS2_APPID,
                "currency": CURRENCIES[self.currency],
                "market_hash_name": market_hash_name,
            },
        )

        if not isinstance(data, dict) or not data.get("success"):
            # Steam renvoie success=false pour un objet jamais vendu.
            self.stats["introuvables"] += 1
            log.debug("Aucun prix Steam pour %r", market_hash_name)
            return None

        quote = Quote(
            market_hash_name=market_hash_name,
            source=self.name,
            lowest_price=parse_money(data.get("lowest_price")),
            median_price=parse_money(data.get("median_price")),
            volume=parse_volume(data.get("volume")),
            currency=self.currency,
        )
        if quote.is_empty:
            self.stats["introuvables"] += 1
            return None
        if self.cache:
            self.cache.put(quote)
        return quote
