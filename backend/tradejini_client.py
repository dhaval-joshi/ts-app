"""
Thin async wrapper around the Tradejini REST API. Only the endpoints this
app actually uses are wrapped -- see the docs (tradejini-openapi.json) for
the full surface.

Auth: individual-token flow (single account, matches a personal app/API key).
Access tokens last 24h; on any 401 we transparently re-authenticate once and
retry the call.
"""
import asyncio
import logging
import time
from typing import Optional

import httpx
import pyotp

from . import config

log = logging.getLogger("tradejini.rest")


class TradejiniAuthError(Exception):
    pass


class TradejiniApiError(Exception):
    def __init__(self, msg: str, payload: dict | None = None):
        super().__init__(msg)
        self.payload = payload or {}


class TradejiniClient:
    broker_id = "tradejini"  # a plain class-level constant is fine -- there's exactly one broker
                              # today; this exists so order_manager.py/program_manager.py can stamp
                              # broker_id onto orders/Programs via getattr(client, "broker_id", None)
                              # without caring whether it's live Tradejini or (eventually) something
                              # else, and so PaperBrokerClient (which deliberately has none) reads as
                              # None everywhere rather than needing a special case at each call site

    def __init__(self):
        self.api_key = config.TRADEJINI_API_KEY
        self._access_token: Optional[str] = None
        self._token_obtained_at: float = 0.0
        self._auth_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(base_url=config.REST_BASE_URL, timeout=15.0)

    @property
    def auth_token(self) -> str:
        """The '<apikey>:<accessToken>' pair used for the streaming socket too."""
        return f"{self.api_key}:{self._access_token}"

    @property
    def is_logged_in(self) -> bool:
        return self._access_token is not None

    def _auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}:{self._access_token}"}

    async def login(self) -> None:
        """Individual-token flow: api_key + password + freshly generated TOTP."""
        async with self._auth_lock:
            if not config.TRADEJINI_TOTP_SECRET:
                raise TradejiniAuthError(
                    "TRADEJINI_TOTP_SECRET is not set in .env -- cannot auto-login."
                )
            otp = pyotp.TOTP(config.TRADEJINI_TOTP_SECRET).now()
            resp = await self._http.post(
                "/api-gw/oauth/individual-token-v2",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={
                    "password": config.TRADEJINI_PASSWORD,
                    "twoFa": otp,
                    "twoFaTyp": "totp",
                },
            )
            if resp.status_code != 200:
                raise TradejiniAuthError(f"Login failed ({resp.status_code}): {resp.text}")
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_obtained_at = time.time()
            log.info("Tradejini login OK, token valid for %s seconds", data.get("expires_in"))

    async def _request(self, method: str, path: str, *, params=None, data=None, json_body=None, retry=True) -> dict:
        if self._access_token is None:
            await self.login()

        resp = await self._http.request(
            method, path, params=params, data=data, json=json_body, headers=self._auth_header()
        )

        if resp.status_code == 401 and retry:
            log.info("Access token expired/invalid, re-authenticating...")
            await self.login()
            return await self._request(method, path, params=params, data=data, retry=False)

        if resp.status_code == 429:
            raise TradejiniApiError("Rate limited (429) by Tradejini API", {"status": 429})

        try:
            payload = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise TradejiniApiError(f"Non-JSON response from {path}: {resp.text[:300]}")

        if payload.get("s") == "error":
            raise TradejiniApiError(payload.get("msg", "Unknown Tradejini API error"), payload)

        return payload

    # ------------------------------------------------------------ orders --

    async def place_order(self, **fields) -> dict:
        """POST /api/oms/place-order. fields follow PlaceOrderRequest."""
        return await self._request("POST", "/api/oms/place-order", data=_clean(fields))

    # ------------------------------------------------------------- margin --

    async def get_order_margin(self, **fields) -> dict:
        """POST /api/oms/margin -- required/available margin for ONE
        proposed order (form-encoded, matching place_order's own schema).
        Not used directly for the Program pre-check (see get_basket_margin
        below, which checks both legs together) but kept as a real,
        working method in its own right."""
        return await self._request("POST", "/api/oms/margin", data=_clean(fields))

    async def get_basket_margin(self, basket_orders: list) -> dict:
        """POST /api/oms/basket-margin -- required/available margin for
        MULTIPLE proposed orders checked TOGETHER (JSON body, unlike
        every other endpoint this client wraps, which are all form-
        encoded) -- used for a Program's cycle pre-check, since checking
        both legs as one basket is more accurate than checking each in
        isolation (margin engines can net/hedge across a basket in ways
        two independent single-order checks wouldn't reflect)."""
        return await self._request("POST", "/api/oms/basket-margin", json_body={"basketOrders": basket_orders})

    async def modify_order(self, **fields) -> dict:
        """PUT /api/oms/modify-order.
        Currently unused (trailing is entirely local now -- see
        order_manager.py's _maybe_trail), kept as a real, working REST
        wrapper for future use (e.g. the Market-vs-Limit-close Phase 3/4
        item in the README would need this to adjust a resting limit
        order)."""
        return await self._request("PUT", "/api/oms/modify-order", data=_clean(fields))

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request("DELETE", "/api/oms/cancel-order", params={"orderId": order_id})

    async def get_orders(self) -> list[dict]:
        payload = await self._request("GET", "/api/oms/orders")
        return payload.get("d") or []

    async def get_positions(self) -> list[dict]:
        payload = await self._request("GET", "/api/oms/positions")
        return payload.get("d") or []

    async def get_trades(self) -> list[dict]:
        payload = await self._request("GET", "/api/oms/trades")
        return payload.get("d") or []

    # ------------------------------------------------------- script master --

    async def get_scrip_groups(self, version: int) -> dict:
        """GET /api/mkt-data/scrips/symbol-store?version=X -- tells us
        whether the script master data has changed since `version` (their
        Beginning-of-Day process bumps it once a day)."""
        return await self._request("GET", "/api/mkt-data/scrips/symbol-store", params={"version": version})

    async def get_scrip_group_csv(self, group: str) -> str:
        """GET /api/mkt-data/scrips/symbol-store/{group} -- returns raw CSV
        text, not JSON like every other endpoint this client wraps, so it
        can't go through the shared _request() JSON-parsing path."""
        if self._access_token is None:
            await self.login()
        resp = await self._http.get(f"/api/mkt-data/scrips/symbol-store/{group}", headers=self._auth_header())
        if resp.status_code == 401:
            await self.login()
            resp = await self._http.get(f"/api/mkt-data/scrips/symbol-store/{group}", headers=self._auth_header())
        resp.raise_for_status()
        return resp.text

    async def get_interval_chart_data(self, symbol_id: str, interval: str, from_ts: int, to_ts: int) -> list:
        """GET /api/mkt-data/chart/interval-data -- fetches historical chart data."""
        payload = await self._request("GET", "/api/mkt-data/chart/interval-data", params={
            "id": symbol_id, "interval": interval, "from": from_ts, "to": to_ts
        })
        return payload.get("d") or []

    async def close(self):
        await self._http.aclose()


def _clean(fields: dict) -> dict:
    """Drop None values, and send quantity fields as plain integers, never
    as floats-with-a-trailing-.0.

    Found by diffing this app's requests against real, working curl examples
    captured from the browser: a working request sends "stopQty=65", but
    every quantity in this app is stored as a Python float internally
    (qty: float throughout, for arithmetic convenience elsewhere), and
    Python's default float-to-string conversion turns that same value into
    "65.0" -- a byte-level difference from what a real successful request
    sends. Deliberately scoped to quantity-shaped field names only (not a
    blanket "any whole-number float becomes an int"): the same working curl
    examples show price fields keeping "109.00"-style formatting even when
    the price is a whole number, so only qty fields are normalized here.

    CONFIRMED (not just a hypothesis) by direct testing against the live
    API: a modify-type endpoint returned "Bad request" when quantity was
    sent with a decimal (e.g. "65.0"), succeeding with a plain integer
    ("65") instead -- the same decimal quantity was accepted fine on the
    corresponding place-type endpoint. Modify being measurably stricter
    about request shape than place is worth remembering if a future
    "works on create, fails on modify" report shows up again."""
    cleaned = {}
    for k, v in fields.items():
        if v is None:
            continue
        if k.lower().endswith("qty") and isinstance(v, float) and v.is_integer():
            v = int(v)
        cleaned[k] = v
    return cleaned
