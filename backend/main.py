import asyncio
import logging
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, FileResponse, RedirectResponse
from starlette.routing import Route, WebSocketRoute, Mount
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import auth
from . import clock
from . import heartbeat
from .paper_broker import PaperBrokerClient

from . import config, store, failure_log, factsheet, broker_reconcile
from .models import CreateOrderRequest, StrategyConfig, ValidationError, PortfolioSafeguards, as_dict
from .tradejini_client import TradejiniClient, TradejiniAuthError
from .stream_manager import StreamManager
from .nxtradstream import GREEKS
from .order_manager import OrderManager
from .script_master import ScriptMaster
from .program_manager import ProgramManager
from .indicators import IndicatorService
from .signal_engine import SignalEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tradejini.main")

client = TradejiniClient()
manager: OrderManager | None = None
paper_manager: OrderManager | None = None
stream: StreamManager | None = None
script_master: ScriptMaster | None = None
programs: ProgramManager | None = None
indicators: IndicatorService | None = None
signal_engine: SignalEngine | None = None
_ws_clients: set[WebSocket] = set()

SCRIP_MASTER_REFRESH_INTERVAL_SECONDS = 6 * 3600  # Tradejini's own data only changes once/day (BOD process);
                                                    # this is just a safety net for an app left running for days

HEARTBEAT_CHECK_INTERVAL_SECONDS = 60  # internet-reachability checks have real overhead/cost -- no need
                                         # for second-by-second freshness on this specific signal
TICK_STALE_AFTER_SECONDS = 90  # ~6x the Program tick interval (15s) -- comfortably longer than any single
                                 # slow tick, short enough to actually mean something if crossed
_heartbeat_state: dict = {"zone": heartbeat.GREEN, "internet_up": True, "entities": [], "checked_at": None}


@asynccontextmanager
async def lifespan(app):
    global manager, paper_manager, stream, script_master, programs, indicators, signal_engine

    # Deliberately the ONE thing in this app that's allowed to refuse to
    # start at all -- unlike the Tradejini connection below (transient,
    # retries forever, never blocks the UI), missing app-login credentials
    # is a configuration error, not a transient condition, and "the app
    # requires login" was an explicit requirement -- running unprotected
    # because .env was left incomplete would be the wrong failure mode.
    if not auth.credentials_configured():
        raise RuntimeError(
            "APP_LOGIN_USERNAME and APP_LOGIN_PASSWORD must both be set in .env before this app will "
            "start -- see .env.example. This is the login for the app itself (separate from your "
            "Tradejini credentials), and it's required so the app doesn't run wide open."
        )

    loop = asyncio.get_event_loop()
    stream = StreamManager(loop)
    manager = OrderManager(client, stream, owner="live")
    paper_client = PaperBrokerClient(stream_manager=stream)
    paper_manager = OrderManager(paper_client, stream, subscribe_all_active=True, owner="paper")
    paper_client.order_manager = paper_manager  # resolves the circular reference -- see PaperBrokerClient.__init__
    script_master = ScriptMaster(client)
    indicators = IndicatorService(client)
    programs = ProgramManager(manager, paper_manager, script_master)
    signal_engine = SignalEngine(programs, script_master)

    await manager.load_from_disk()
    await paper_manager.load_from_disk()
    await programs.load_from_disk()

    log.info("Connecting to Tradejini at %s ...", config.REST_BASE_URL)
    if not await _try_login_and_start_stream():
        # Startup login/stream failing (bad credentials, no internet, DNS
        # issues, etc.) must NEVER take the whole app down with it -- the
        # dashboard/strategy/order pages are still useful to look at, and
        # this keeps retrying in the background so a transient problem
        # (network blip, broker maintenance) fixes itself without a manual
        # restart once it clears.
        asyncio.create_task(_login_retry_loop())

    # IMPORTANT, load-bearing invariant: every one of these background
    # loops is independent of app-login/session state, BY DESIGN --
    # trading (order reconciliation, trailing, Program cycles, safeguards)
    # must keep running exactly as configured whether or not anyone is
    # currently logged into the web UI, since app login exists only to
    # gate VIEWING the dashboard, never to gate whether trading happens.
    # This works because these are plain asyncio tasks, not HTTP requests
    # -- they never pass through AuthMiddleware, which only wraps the
    # ASGI request/response cycle for actual incoming connections. If any
    # of this is ever refactored to route through the app's own HTTP API
    # instead of calling manager/programs methods directly in-process,
    # that refactor would silently reintroduce the exact coupling this
    # comment exists to warn against -- don't do that.
    asyncio.create_task(manager.reconcile_loop())
    asyncio.create_task(paper_manager.reconcile_loop())  # paper orders need reconciliation too -- same
                                                            # cadence, same code path, just against
                                                            # PaperBrokerClient's simulated fills instead
    asyncio.create_task(_stream_consumer_loop())
    asyncio.create_task(_broadcast_loop())
    asyncio.create_task(programs.tick_loop())
    asyncio.create_task(_script_master_refresh_loop())
    asyncio.create_task(_heartbeat_loop())
    asyncio.create_task(stream.periodic_resubscribe_loop())  # self-healing safety net for the live-price
                                                                # subscription -- see its docstring
    asyncio.create_task(_archive_eod_data_loop())

    yield

    await client.close()

async def _archive_eod_data_loop():
    """Runs the EOD historical data pipeline at 15:35 IST daily."""
    from datetime import datetime, time, timedelta
    while True:
        try:
            now = clock.now()
            target = datetime.combine(now.date(), time(15, 35)).replace(tzinfo=clock.IST)
            if now > target:
                target += timedelta(days=1)
            
            await asyncio.sleep((target - now).total_seconds())
            
            from .data_archiver import archive_eod_data
            await archive_eod_data(client, script_master)
        except Exception:
            log.exception("EOD Data Archive failed")
            await asyncio.sleep(300)


async def _heartbeat_loop():
    """Periodically checks internet reachability (independent of Tradejini
    specifically -- see heartbeat.check_internet_reachable's docstring for
    why) and every configured entity's connection status, then computes
    the overall zone. Built generically (a list of entities, not "the
    Tradejini connection" hardcoded) so a second broker or an exchange
    integration slots in later as one more list entry -- but today that
    list only ever has exactly one thing in it, which is the honest
    current state of this app, not a fleet."""
    while True:
        try:
            internet_up = await heartbeat.check_internet_reachable()
            entities = [
                heartbeat.EntityStatus(
                    entity_type="broker", name="Tradejini",
                    connected=client.is_logged_in and bool(stream and stream.is_connected()),
                ),
            ]
            zone = heartbeat.compute_zone(internet_up=internet_up, entities=entities)
            _heartbeat_state["zone"] = zone
            _heartbeat_state["internet_up"] = internet_up
            _heartbeat_state["entities"] = [
                {"entity_type": e.entity_type, "name": e.name, "connected": e.connected} for e in entities
            ]
            _heartbeat_state["checked_at"] = clock.now_iso()
        except Exception:
            log.exception("Heartbeat check failed -- will retry.")
        await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL_SECONDS)


async def _script_master_refresh_loop():
    """Runs once shortly after a successful login, then again every few
    hours as a safety net -- see SCRIP_MASTER_REFRESH_INTERVAL_SECONDS.
    Never crashes the app; a failed refresh just means Program cycle-starts
    keep using whatever's cached (or decline to start, with a clear log
    line, if nothing has ever loaded yet) until the next attempt."""
    backfilled_once = False
    while True:
        if client.is_logged_in:
            try:
                await script_master.refresh()
                if not backfilled_once:
                    symbol_ids = list(script_master._indices.keys())
                    if "IDX_-15_NSE" not in symbol_ids:
                        symbol_ids.append("IDX_-15_NSE")
                    asyncio.create_task(indicators.backfill_daily_signals(symbol_ids, days_back=30))
                    backfilled_once = True
            except Exception:
                log.exception("Scrip master refresh failed -- will retry.")
        await asyncio.sleep(SCRIP_MASTER_REFRESH_INTERVAL_SECONDS if script_master.is_loaded() else 60)


async def _try_login_and_start_stream() -> bool:
    """Returns True on success. Never raises -- every failure mode (bad
    credentials, no internet, DNS resolution failure, broker downtime, or
    anything else) is caught and logged, never allowed to crash the app."""
    try:
        await client.login()
        stream.start(client.auth_token)
        log.info("Login OK, stream starting.")
        return True
    except TradejiniAuthError as e:
        log.error("Login failed (credentials): %s -- check TRADEJINI_API_KEY / "
                   "TRADEJINI_PASSWORD / TRADEJINI_TOTP_SECRET in your .env.", e)
    except Exception as e:
        # covers httpx/httpcore connection errors (no internet, DNS
        # resolution failure -- e.g. "getaddrinfo failed" -- proxy issues,
        # broker-side outages) and anything else unanticipated
        log.error("Login failed (network/unexpected): %s -- check your internet connection, "
                   "that TRADEJINI_HOST in .env is a bare hostname with no 'https://' prefix "
                   "(currently resolving to %s), and that the broker isn't down.",
                   e, config.REST_BASE_URL)
    return False


async def _login_retry_loop():
    delay = 30
    while True:
        await asyncio.sleep(delay)
        log.info("Retrying login...")
        if await _try_login_and_start_stream():
            return


async def _stream_consumer_loop():
    while True:
        item = await stream.queue.get()
        if item["kind"] == "tick":
            data = item["data"]
            if "evntType" in data:
                manager.nudge_reconcile()
            elif data.get("msgType") == GREEKS:
                # one-shot IV/Greeks snapshots for entry_signals.py's optional gate -- see
                # OrderManager.fetch_greeks_snapshot. Previously silently dropped here (no "ltp"
                # key on a greeks packet, so it fell through every branch and was discarded).
                if indicators:
                    indicators.handle_greeks_tick(data)
                await manager.handle_greeks_tick(data)
                await paper_manager.handle_greeks_tick(data)
            elif "ltp" in data:
                if indicators:
                    indicators.handle_l1_tick(data)
                if signal_engine:
                    signal_engine.handle_l1_tick(data)
                await manager.handle_l1_tick(data)
                await paper_manager.handle_l1_tick(data)  # paper fills are driven entirely by ticks (no
                                                             # real broker event to nudge on) -- this keeps
                                                             # its price cache fresh for the next reconcile pass
        elif item["kind"] == "connected":
            log.info("Stream connected.")
        elif item["kind"] == "closed":
            log.warning("Stream closed: %s", item.get("reason"))
        elif item["kind"] == "error":
            log.warning("Stream error: %s", item.get("reason"))


async def _broadcast_loop():
    """Periodically pushes the current order list to any connected dashboard
    tabs, so the UI updates live without the user hitting refresh."""
    while True:
        await asyncio.sleep(2)
        if not _ws_clients or manager is None:
            continue
        payload = {"type": "orders", "orders": manager.list_orders() + paper_manager.list_orders()}
        dead = set()
        for ws in _ws_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        _ws_clients.difference_update(dead)


# --------------------------------------------------------------- REST API

def _manager_for_order(order_id: str):
    """Regular OMS orders (manually placed) are always live -- only
    Program legs can ever be paper. Since order_ids don't encode which
    manager owns them, this just checks both; used by every
    read/write endpoint below that isn't already scoped to one manager
    on purpose (like api_create_order, which is Regular OMS only)."""
    if manager.get_order(order_id):
        return manager
    if paper_manager.get_order(order_id):
        return paper_manager
    return None


async def api_list_orders(request):
    return JSONResponse(manager.list_orders() + paper_manager.list_orders())


async def api_get_order(request):
    om = _manager_for_order(request.path_params["order_id"])
    if not om:
        return JSONResponse({"detail": "order not found"}, status_code=404)
    return JSONResponse(om.get_order(request.path_params["order_id"]))


async def api_create_order(request):
    body = await request.json()
    try:
        req = CreateOrderRequest.from_dict(body)
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    try:
        # Regular OMS's New Order form has no paper option -- manually
        # placed orders are always live, by design
        order = await manager.create_and_place_order(req)
        return JSONResponse(order)
    except Exception as e:
        log.exception("create order failed")
        return JSONResponse({"detail": str(e)}, status_code=400)


async def api_close_order(request):
    price = None
    try:
        body = await request.json()
        if body and body.get("price") not in (None, ""):
            price = float(body["price"])
    except Exception:
        pass  # no body / not JSON -> plain market close
    om = _manager_for_order(request.path_params["order_id"])
    if not om:
        return JSONResponse({"detail": "order not found"}, status_code=404)
    try:
        order = await om.request_close(request.path_params["order_id"], reason="manual", price=price)
        return JSONResponse(order)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)


async def api_list_archived_orders(request):
    return JSONResponse(manager.list_archived_orders() + paper_manager.list_archived_orders())


async def api_archive_order(request):
    om = _manager_for_order(request.path_params["order_id"])
    if not om:
        return JSONResponse({"detail": "order not found"}, status_code=404)
    try:
        order = om.archive_order(request.path_params["order_id"])
        return JSONResponse(order)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)


async def api_archive_orders_bulk(request):
    body = await request.json()
    order_ids = (body or {}).get("order_ids") or []
    if not isinstance(order_ids, list) or not order_ids:
        return JSONResponse({"detail": "order_ids (non-empty list) is required"}, status_code=400)
    live_ids = [oid for oid in order_ids if manager.get_order(oid)]
    paper_ids = [oid for oid in order_ids if oid not in live_ids and paper_manager.get_order(oid)]
    result = manager.archive_orders_bulk(live_ids) if live_ids else {"archived": [], "failed": {}}
    paper_result = paper_manager.archive_orders_bulk(paper_ids) if paper_ids else {"archived": [], "failed": {}}
    return JSONResponse({
        "archived": result["archived"] + paper_result["archived"],
        "failed": {**result["failed"], **paper_result["failed"]},
    })


async def api_unarchive_order(request):
    om = _manager_for_order(request.path_params["order_id"])
    if not om:
        return JSONResponse({"detail": "order not found"}, status_code=404)
    try:
        order = om.unarchive_order(request.path_params["order_id"])
        return JSONResponse(order)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)


ALLOWED_ADV_ACCENTS = ("indigo", "purple", "violet", "teal", "rose", "amber")


async def api_get_settings(request):
    settings = store.load_app_settings()
    settings.setdefault("advanced_oms_accent", "indigo")
    return JSONResponse(settings)


async def api_save_settings(request):
    body = await request.json()
    accent = body.get("advanced_oms_accent")
    if accent is not None and accent not in ALLOWED_ADV_ACCENTS:
        return JSONResponse({"detail": f"advanced_oms_accent must be one of {ALLOWED_ADV_ACCENTS}"}, status_code=400)
    settings = store.load_app_settings()
    if accent is not None:
        settings["advanced_oms_accent"] = accent
    store.save_app_settings(settings)
    return JSONResponse(settings)


async def api_recent_failures(request):
    q = request.query_params
    limit = int(q.get("limit", 200))
    oms_type = q.get("oms_type")

    # oms_type is a derived (not stored) filter applied AFTER the read below
    # -- if it's in play, read a larger raw window first, or a narrow
    # derived filter could end up looking at hardly any matching entries
    # within the most-recent-`limit` window and return far fewer results
    # than actually exist further back
    read_limit = limit * 10 if oms_type in ("regular", "advanced") else limit

    entries = failure_log.read_recent_failures(
        limit=read_limit,
        category=q.get("category") or None,
        order_id=q.get("order_id") or None,
        program_id=q.get("program_id") or None,
        since=q.get("since") or None,
        until=q.get("until") or None,
    )
    if oms_type in ("regular", "advanced"):
        def _is_advanced(e):
            return bool(e.get("program_id")) or str(e.get("category", "")).startswith("program_")
        entries = [e for e in entries if _is_advanced(e) == (oms_type == "advanced")][:limit]
    return JSONResponse(entries)


async def api_journal_list(request):
    q = request.query_params
    entries = factsheet.list_journal_entries(
        program_id=q.get("program_id") or None,
        limit=int(q.get("limit", 200)),
    )
    return JSONResponse(entries)


async def api_journal_cycle_detail(request):
    fs = factsheet.load_cycle_factsheet(request.path_params["program_id"], request.path_params["cycle_id"])
    if not fs:
        return JSONResponse({"detail": "cycle factsheet not found"}, status_code=404)
    # The corrected (amendments-applied) view is the primary payload -- what actually happened,
    # best known today -- with the raw amendment trail alongside it for transparency, per
    # factsheet.py's "immutable original + visible amendment log" design.
    return JSONResponse({**factsheet.apply_amendments(fs), "amendments": fs.get("amendments", [])})


async def api_journal_order_detail(request):
    fs = factsheet.load_order_factsheet(request.path_params["order_id"])
    if not fs:
        return JSONResponse({"detail": "order factsheet not found"}, status_code=404)
    return JSONResponse({**factsheet.apply_amendments(fs), "amendments": fs.get("amendments", [])})


async def api_run_reconcile(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    dry_run = body.get("dry_run", True) if isinstance(body, dict) else True
    brokers = {"tradejini": client}  # a one-entry dict today; _reconcile_one_broker's own loop is
                                       # already broker-keyed for whenever a second broker exists
    try:
        report = await broker_reconcile.run_reconciliation(order_manager=manager, brokers=brokers, dry_run=bool(dry_run))
    except broker_reconcile.ReconciliationAlreadyRunning:
        return JSONResponse({"detail": "a reconciliation run is already in progress"}, status_code=409)
    return JSONResponse(report)


async def api_list_reconcile_reports(request):
    limit = int(request.query_params.get("limit", 50))
    return JSONResponse(store.list_reconcile_reports(limit=limit))


async def api_get_reconcile_report(request):
    report = store.load_reconcile_report(request.path_params["run_id"])
    if not report:
        return JSONResponse({"detail": "reconciliation report not found"}, status_code=404)
    return JSONResponse(report)


async def api_fetch_price(request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "invalid request body"}, status_code=400)
    stream_symbol = (body or {}).get("stream_symbol", "").strip()
    if not stream_symbol:
        return JSONResponse({"detail": "stream_symbol is required"}, status_code=400)
    if not (stream and stream.is_connected()):
        return JSONResponse({"detail": "Live feed is not connected -- check the app's login/stream status."}, status_code=503)
    ltp = await manager.fetch_live_price(stream_symbol)
    if ltp is None:
        return JSONResponse(
            {"detail": "No price arrived in time -- check the stream symbol is correct (right exchange segment?) and the market is open."},
            status_code=408,
        )
    return JSONResponse({"stream_symbol": stream_symbol, "ltp": ltp})


async def api_list_strategies(request):
    return JSONResponse(store.list_strategies())


async def api_get_strategy(request):
    strat = store.load_strategy_by_name(request.path_params["name"])
    if not strat:
        return JSONResponse({"detail": "strategy not found"}, status_code=404)
    return JSONResponse(strat)


async def api_save_strategy(request):
    body = await request.json()
    try:
        strat = StrategyConfig.from_dict(body)
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    store.save_strategy(strat.strategy_id, as_dict(strat))
    return JSONResponse(as_dict(strat))


async def api_delete_strategy(request):
    existing = store.load_strategy_by_name(request.path_params["name"])
    if existing:
        store.delete_strategy_by_id(existing["strategy_id"])
    return JSONResponse({"ok": True})


# ------------------------------------------------------------- programs --

async def api_list_indices(request):
    """Underlyings available for a Program to trade -- from the live
    Script Master feed, not a static list. Empty until the first successful
    script master refresh after login."""
    if not script_master or not script_master.is_loaded():
        return JSONResponse([])
    return JSONResponse([
        {"index_id": r.id, "disp_name": r.disp_name, "exc_token": r.exc_token}
        for r in script_master.list_indices()
    ])


async def api_list_programs(request):
    return JSONResponse(programs.list_programs())


async def api_get_program(request):
    program = programs.get_program(request.path_params["program_id"])
    if not program:
        return JSONResponse({"detail": "Program not found"}, status_code=404)
    return JSONResponse(program)


async def api_create_program(request):
    body = await request.json()
    try:
        program = programs.create_program(body)
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse(program)


async def api_update_program(request):
    body = await request.json()
    try:
        program = programs.update_program(request.path_params["program_id"], body)
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    return JSONResponse(program)


async def api_delete_program(request):
    try:
        programs.delete_program(request.path_params["program_id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


async def api_stop_program(request):
    try:
        program = programs.stop_program(request.path_params["program_id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    return JSONResponse(program)


async def api_resume_program(request):
    try:
        program = programs.resume_program(request.path_params["program_id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    return JSONResponse(program)


async def api_flatten_program(request):
    try:
        program = await programs.stop_and_flatten_program(request.path_params["program_id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    return JSONResponse(program)


async def api_close_cycle(request):
    try:
        program = await programs.close_cycle(request.path_params["program_id"])
    except ValueError as e:
        # can raise for two different reasons (program not found, or no active cycle) --
        # same "just use 400 uniformly" convention api_delete_program already uses below
        # for the same two-different-ValueError-reasons shape
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse(program)


async def api_archive_program(request):
    try:
        program = programs.archive_program(request.path_params["program_id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    return JSONResponse(program)


async def api_unarchive_program(request):
    try:
        program = programs.unarchive_program(request.path_params["program_id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    return JSONResponse(program)


async def api_manual_entry(request):
    try:
        body = await request.json()
        leg = (body or {}).get("leg")
        if leg not in ("CE", "PE"):
            return JSONResponse({"detail": "leg must be 'CE' or 'PE'"}, status_code=400)
            
        program = await programs.start_manual_single_leg_cycle(
            request.path_params["program_id"], 
            leg=leg,
            indicators=indicators
        )
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    except Exception as e:
        log.exception("manual entry failed")
        return JSONResponse({"detail": str(e)}, status_code=500)
    return JSONResponse(program)




# ---------------------------------------------------------------- risk groups --

async def api_list_risk_groups(request):
    return JSONResponse(programs.list_risk_groups())


async def api_create_risk_group(request):
    body = await request.json()
    try:
        group = programs.create_risk_group(body)
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse(group)


async def api_update_risk_group(request):
    body = await request.json()
    try:
        group = programs.update_risk_group(request.path_params["risk_group_id"], body)
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    return JSONResponse(group)


async def api_delete_risk_group(request):
    try:
        programs.delete_risk_group(request.path_params["risk_group_id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return JSONResponse({"ok": True})


async def api_get_portfolio_safeguards(request):
    return JSONResponse(store.load_portfolio_safeguards())


async def api_save_portfolio_safeguards(request):
    body = await request.json()
    try:
        cfg = PortfolioSafeguards.from_dict(body)
    except (ValidationError, ValueError, TypeError) as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    store.save_portfolio_safeguards(as_dict(cfg))
    return JSONResponse(as_dict(cfg))


async def api_status(request):
    last_tick_at = programs.last_tick_at.isoformat() if programs and programs.last_tick_at else None
    tick_stale = bool(
        programs and programs.last_tick_at
        and (clock.now() - programs.last_tick_at).total_seconds() > TICK_STALE_AFTER_SECONDS
    )
    return JSONResponse(
        {
            "stream_connected": stream.is_connected() if stream else False,
            "orders_tracked": (len(manager.list_orders()) + len(paper_manager.list_orders())) if manager and paper_manager else 0,
            "script_master_loaded": script_master.is_loaded() if script_master else False,
            "programs_tracked": len(programs.list_programs()) if programs else 0,
            "heartbeat": _heartbeat_state,
            "last_tick_at": last_tick_at,
            "tick_stale": tick_stale,
        }
    )


async def ws_endpoint(websocket: WebSocket):
    # AuthMiddleware never runs for WebSocket connections (Starlette's
    # BaseHTTPMiddleware only wraps "http"-scope requests) -- this is the
    # real enforcement point for /ws, checking the same session cookie
    # directly before ever accepting the connection.
    token = websocket.cookies.get(config.SESSION_COOKIE_NAME)
    if not auth.is_valid_session(token):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # no client -> server messages expected, just keep alive
    except WebSocketDisconnect:
        _ws_clients.discard(websocket)


# ------------------------------------------------------------- frontend --

async def index(request):
    return FileResponse(str(config.FRONTEND_DIR / "index.html"))


async def login_page(request):
    return FileResponse(str(config.FRONTEND_DIR / "login.html"))


async def admin_page(request):
    return FileResponse(str(config.FRONTEND_DIR / "admin.html"))


async def api_login(request):
    body = await request.json()
    username = (body or {}).get("username", "")
    password = (body or {}).get("password", "")
    if not auth.credentials_configured():
        # this should never actually be reachable -- see the startup check
        # in lifespan() below, which refuses to run at all if credentials
        # aren't set -- but a clear error here rather than a confusing
        # "always fails" login form is worth the belt-and-suspenders
        return JSONResponse({"detail": "App login isn't configured (APP_LOGIN_USERNAME/PASSWORD missing in .env)"}, status_code=500)
    if not auth.check_credentials(username, password):
        return JSONResponse({"detail": "Incorrect username or password"}, status_code=401)
    token = auth.create_session()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        config.SESSION_COOKIE_NAME, token,
        max_age=config.SESSION_TTL_DAYS * 24 * 3600,
        httponly=True, samesite="lax",
    )
    return resp


async def api_logout(request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    auth.destroy_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(config.SESSION_COOKIE_NAME)
    return resp


async def strategy_page(request):
    # Strategies moved to be a tab inside the Regular OMS section -- this
    # keeps any old bookmark/link to the standalone page working, landing
    # in the right place instead of a 404
    return RedirectResponse("/?section=regular&tab=strategies")


async def order_page(request):
    # New Order moved to be a dialog inside the Regular OMS section -- this
    # keeps any old bookmark/link to the standalone page (including a
    # ?reorder=<id> deep link) working, landing in the right place and
    # opening the dialog pre-filled instead of a 404
    reorder_id = request.query_params.get("reorder")
    qs = f"&reorder={reorder_id}" if reorder_id else ""
    return RedirectResponse(f"/?section=regular{qs}")


async def archive_page(request):
    # Calendar/Archive moved to be tabs on the Dashboard -- this keeps any
    # old bookmark/link to the standalone page working, landing on the
    # right tab instead of a 404
    return RedirectResponse("/?tab=archive")


async def calendar_page(request):
    return RedirectResponse("/?tab=calendar")


async def api_kill_switch(request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not auth.is_valid_session(token):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    
    count_prog = 0
    if programs:
        for p in programs.list_programs():
            status = p.get("runtime", {}).get("status")
            # stop if running or in error
            if status not in ["stopped_by_user", "halted_portfolio_stop"]:
                programs.stop_program(p["config"]["program_id"])
                count_prog += 1
                
    count_ord = 0
    if manager:
        for o in manager.list_orders():
            if o["status"] not in ["closed", "cancelled", "rejected", "failed", "closing", "cancelling"]:
                await manager.close_order(o["order_id"], reason="Kill Switch triggered")
                count_ord += 1
    if paper_manager:
        for o in paper_manager.list_orders():
            if o["status"] not in ["closed", "cancelled", "rejected", "failed", "closing", "cancelling"]:
                await paper_manager.close_order(o["order_id"], reason="Kill Switch triggered")
                count_ord += 1
                
    return JSONResponse({"ok": True, "halted_programs": count_prog, "closing_orders": count_ord})


routes = [
    Route("/", index),
    Route("/login", login_page),
    Route("/admin", admin_page),
    Route("/api/auth/login", api_login, methods=["POST"]),
    Route("/api/auth/logout", api_logout, methods=["POST"]),
    Route("/strategy", strategy_page),
    Route("/order", order_page),
    Route("/archive", archive_page),
    Route("/calendar", calendar_page),
    Route("/api/kill-switch", api_kill_switch, methods=["POST"]),
    Route("/api/orders", api_list_orders, methods=["GET"]),
    Route("/api/orders", api_create_order, methods=["POST"]),
    Route("/api/orders/{order_id}", api_get_order, methods=["GET"]),
    Route("/api/orders/{order_id}/close", api_close_order, methods=["POST"]),
    Route("/api/orders/{order_id}/archive", api_archive_order, methods=["POST"]),
    Route("/api/orders/{order_id}/unarchive", api_unarchive_order, methods=["POST"]),
    Route("/api/orders/archive-bulk", api_archive_orders_bulk, methods=["POST"]),
    Route("/api/orders-archived", api_list_archived_orders, methods=["GET"]),
    Route("/api/settings", api_get_settings, methods=["GET"]),
    Route("/api/settings", api_save_settings, methods=["POST"]),
    Route("/api/failures", api_recent_failures, methods=["GET"]),
    Route("/api/journal", api_journal_list, methods=["GET"]),
    Route("/api/journal/cycle/{program_id}/{cycle_id}", api_journal_cycle_detail, methods=["GET"]),
    Route("/api/journal/order/{order_id}", api_journal_order_detail, methods=["GET"]),
    Route("/api/reconcile", api_run_reconcile, methods=["POST"]),
    Route("/api/reconcile-reports", api_list_reconcile_reports, methods=["GET"]),
    Route("/api/reconcile-reports/{run_id}", api_get_reconcile_report, methods=["GET"]),
    Route("/api/price/fetch", api_fetch_price, methods=["POST"]),
    Route("/api/strategies", api_list_strategies, methods=["GET"]),
    Route("/api/strategies", api_save_strategy, methods=["POST"]),
    Route("/api/strategies/{name}", api_get_strategy, methods=["GET"]),
    Route("/api/strategies/{name}", api_delete_strategy, methods=["DELETE"]),
    Route("/api/indices", api_list_indices, methods=["GET"]),
    Route("/api/programs", api_list_programs, methods=["GET"]),
    Route("/api/programs", api_create_program, methods=["POST"]),
    Route("/api/programs/{program_id}", api_get_program, methods=["GET"]),
    Route("/api/programs/{program_id}", api_update_program, methods=["PUT"]),
    Route("/api/programs/{program_id}", api_delete_program, methods=["DELETE"]),
    Route("/api/programs/{program_id}/stop", api_stop_program, methods=["POST"]),
    Route("/api/programs/{program_id}/resume", api_resume_program, methods=["POST"]),
    Route("/api/programs/{program_id}/flatten", api_flatten_program, methods=["POST"]),
    Route("/api/programs/{program_id}/close-cycle", api_close_cycle, methods=["POST"]),
    Route("/api/programs/{program_id}/archive", api_archive_program, methods=["POST"]),
    Route("/api/programs/{program_id}/unarchive", api_unarchive_program, methods=["POST"]),
    Route("/api/programs/{program_id}/manual-entry", api_manual_entry, methods=["POST"]),

    Route("/api/risk-groups", api_list_risk_groups, methods=["GET"]),
    Route("/api/risk-groups", api_create_risk_group, methods=["POST"]),
    Route("/api/risk-groups/{risk_group_id}", api_update_risk_group, methods=["PUT"]),
    Route("/api/risk-groups/{risk_group_id}", api_delete_risk_group, methods=["DELETE"]),
    Route("/api/portfolio-safeguards", api_get_portfolio_safeguards, methods=["GET"]),
    Route("/api/portfolio-safeguards", api_save_portfolio_safeguards, methods=["POST"]),
    Route("/api/status", api_status, methods=["GET"]),
    WebSocketRoute("/ws", ws_endpoint),
    Mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static"),
]

app = Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(auth.AuthMiddleware)])
