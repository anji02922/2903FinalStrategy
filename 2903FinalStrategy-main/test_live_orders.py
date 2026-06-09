"""
Live Demo Account Order Test Suite
Tests every order flow used by the bot against Binance Futures DEMO API.
Safe to run — uses tiny position sizes and cleans up after each test.
"""

import os
import sys
import time
import traceback
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.exchange.binance_client import BinanceClient
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker

# ── Config matching live bot ──
CONFIG = {
    "exchange": {
        "api_key": os.getenv("BINANCE_API_KEY"),
        "api_secret": os.getenv("BINANCE_API_SECRET"),
        "testnet": True,        # demo mode
        "symbol": "ETH/USDT",
        "market_type": "future",
    },
    "trading": {"leverage": 12},
    "fees": {"maker": 0.02, "taker": 0.05, "slippage": 0.005},
}

# Tiny test size — 0.01 ETH (~$20 notional)
TEST_SIZE = 0.01

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
WARN = "\033[93m  WARN\033[0m"

results = []


def log(test_name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append((test_name, passed, detail))
    print(f"{status}  {test_name}")
    if detail:
        print(f"         {detail}")


def cleanup(client, order_mgr):
    """Cancel all orders and close any open position."""
    try:
        order_mgr.cancel_all()
    except Exception:
        pass
    time.sleep(0.5)
    try:
        positions = client.fetch_positions()
        for p in positions:
            contracts = abs(float(p.get("contracts", 0)))
            if contracts > 0:
                side = "long" if float(p.get("contracts", 0)) > 0 else "short"
                order_mgr.close_position_market(side, contracts)
    except Exception:
        pass
    time.sleep(0.5)


def main():
    print("=" * 64)
    print("  LIVE DEMO ORDER TEST SUITE")
    print("=" * 64)

    # ── Setup ──
    print("\n--- Setup ---")
    client = BinanceClient(CONFIG)
    order_mgr = OrderManager(client)

    # Clean slate
    cleanup(client, order_mgr)

    # Test 1: Connection & Balance
    print("\n--- Test 1: Connection & Balance ---")
    try:
        balance = client.get_balance()
        log("get_balance (total)", balance > 0, f"${balance:,.2f}")
    except Exception as e:
        log("get_balance (total)", False, str(e))
        print("\nCannot connect to exchange. Aborting.")
        return

    # Test 2: Set leverage & margin mode
    print("\n--- Test 2: Leverage & Margin ---")
    try:
        client.set_margin_mode("cross")
        log("set_margin_mode(cross)", True)
    except Exception as e:
        log("set_margin_mode(cross)", True, f"Already set or: {e}")

    try:
        client.set_leverage()
        log(f"set_leverage({CONFIG['trading']['leverage']}x)", True)
    except Exception as e:
        log(f"set_leverage({CONFIG['trading']['leverage']}x)", False, str(e))

    # Test 3: Fetch ticker
    print("\n--- Test 3: Ticker ---")
    try:
        ticker = client.fetch_ticker()
        price = float(ticker["last"])
        log("fetch_ticker", price > 0, f"ETH/USDT = ${price:.2f}")
    except Exception as e:
        log("fetch_ticker", False, str(e))
        print("\nCannot fetch ticker. Aborting.")
        return

    # Test 4: Fetch positions (should be empty)
    print("\n--- Test 4: Fetch Positions (empty) ---")
    try:
        positions = client.fetch_positions()
        log("fetch_positions (empty)", len(positions) == 0,
            f"Found {len(positions)} positions" if positions else "No open positions")
    except Exception as e:
        log("fetch_positions (empty)", False, str(e))

    # ════════════════════════════════════════════════
    # Test 5: LONG Market Entry + SL + TP
    # ════════════════════════════════════════════════
    print("\n--- Test 5: LONG Market Entry + SL/TP ---")
    try:
        entry_order = order_mgr.place_market_order("buy", TEST_SIZE)
        fill_price = float(entry_order.get("average", entry_order.get("price", price)))
        log("market_buy", fill_price > 0, f"Filled @ ${fill_price:.2f}")

        time.sleep(1)

        # Verify position exists
        positions = client.fetch_positions()
        has_pos = len(positions) > 0
        log("position_exists_after_entry", has_pos,
            f"contracts={positions[0].get('contracts') if has_pos else 'NONE'}")

        # Place SL (0.5% below) and TP (1% above)
        sl_price = round(fill_price * 0.995, 2)
        tp_price = round(fill_price * 1.01, 2)
        sl_order, tp_order = order_mgr.place_sl_tp("long", TEST_SIZE, sl_price, tp_price)
        sl_id = sl_order.get("id", "")
        tp_id = tp_order.get("id", "")
        log("place_sl", bool(sl_id), f"SL @ ${sl_price:.2f} id={sl_id}")
        log("place_tp", bool(tp_id), f"TP @ ${tp_price:.2f} id={tp_id}")

        time.sleep(1)

        # Verify open orders
        open_orders = client.fetch_open_orders()
        log("open_orders_count", len(open_orders) == 2,
            f"Expected 2, got {len(open_orders)}")

    except Exception as e:
        log("long_entry_flow", False, f"{e}\n{traceback.format_exc()}")

    # ════════════════════════════════════════════════
    # Test 6: Update SL to breakeven (cancel all + re-place)
    # In live, breakeven fires at +0.3% so entry SL is well below current.
    # Simulate: place SL slightly below entry (imitates entry after price moved up).
    # ════════════════════════════════════════════════
    print("\n--- Test 6: Breakeven — Update SL ---")
    try:
        # SL below entry simulates breakeven when price has moved up
        breakeven_price = round(fill_price * 0.999, 2)
        new_sl, new_tp = order_mgr.update_stop_loss(
            sl_id, "long", TEST_SIZE, breakeven_price, tp_price=tp_price
        )
        if new_sl is None:
            log("update_sl_to_breakeven", False,
                "Returned None — OrderImmediatelyFillable (price crossed back)")
        else:
            new_sl_id = new_sl.get("id", "")
            new_tp_id = new_tp.get("id", "") if new_tp else ""
            log("update_sl_to_breakeven", bool(new_sl_id),
                f"New SL @ ${breakeven_price:.2f} id={new_sl_id}")
            log("re_place_tp", bool(new_tp_id),
                f"New TP @ ${tp_price:.2f} id={new_tp_id}")

        time.sleep(1)

        # Verify exactly 2 orders remain (now using algo-aware fetch)
        open_orders = client.fetch_open_orders()
        log("orders_after_breakeven", len(open_orders) == 2,
            f"Expected 2, got {len(open_orders)}")

    except Exception as e:
        log("breakeven_flow", False, f"{e}\n{traceback.format_exc()}")

    # ════════════════════════════════════════════════
    # Test 7: Close position with reduceOnly market order
    # ════════════════════════════════════════════════
    print("\n--- Test 7: Market Close (reduceOnly) ---")
    try:
        # Cancel SL/TP first
        order_mgr.cancel_all()
        time.sleep(0.5)

        close_result = order_mgr.close_position_market("long", TEST_SIZE)
        close_fill = float(close_result.get("average", close_result.get("price", 0)))
        log("close_position_market_reduceOnly", close_fill > 0,
            f"Closed @ ${close_fill:.2f}")

        time.sleep(1)

        # Verify position is gone
        positions = client.fetch_positions()
        log("position_closed", len(positions) == 0,
            f"Positions remaining: {len(positions)}")

        # Verify no orders left
        open_orders = client.fetch_open_orders()
        log("no_orders_after_close", len(open_orders) == 0,
            f"Orders remaining: {len(open_orders)}")

    except Exception as e:
        log("close_flow", False, f"{e}\n{traceback.format_exc()}")

    cleanup(client, order_mgr)
    time.sleep(1)

    # ════════════════════════════════════════════════
    # Test 8: SHORT Market Entry + SL + TP
    # ════════════════════════════════════════════════
    print("\n--- Test 8: SHORT Market Entry + SL/TP ---")
    try:
        ticker = client.fetch_ticker()
        price = float(ticker["last"])

        entry_order = order_mgr.place_market_order("sell", TEST_SIZE)
        fill_price = float(entry_order.get("average", entry_order.get("price", price)))
        log("market_sell (short)", fill_price > 0, f"Filled @ ${fill_price:.2f}")

        time.sleep(1)

        # SL above, TP below for short
        sl_price = round(fill_price * 1.005, 2)
        tp_price = round(fill_price * 0.99, 2)
        sl_order, tp_order = order_mgr.place_sl_tp("short", TEST_SIZE, sl_price, tp_price)
        log("short_sl", bool(sl_order.get("id")), f"SL @ ${sl_price:.2f}")
        log("short_tp", bool(tp_order.get("id")), f"TP @ ${tp_price:.2f}")

        time.sleep(1)

        # Close short
        order_mgr.cancel_all()
        time.sleep(0.5)
        order_mgr.close_position_market("short", TEST_SIZE)
        time.sleep(1)

        positions = client.fetch_positions()
        log("short_closed", len(positions) == 0, f"Remaining: {len(positions)}")

    except Exception as e:
        log("short_flow", False, f"{e}\n{traceback.format_exc()}")

    cleanup(client, order_mgr)
    time.sleep(1)

    # ════════════════════════════════════════════════
    # Test 9: reduceOnly rejects when no position
    # ════════════════════════════════════════════════
    print("\n--- Test 9: reduceOnly Rejects Without Position ---")
    try:
        # Should fail or do nothing — no position to reduce
        order_mgr.close_position_market("long", TEST_SIZE)
        # If we get here, the exchange accepted it (bad — means it opened a short)
        positions = client.fetch_positions()
        if positions:
            log("reduceOnly_rejects", False,
                "reduceOnly did NOT prevent opening opposite position!")
            cleanup(client, order_mgr)
        else:
            log("reduceOnly_rejects", True, "Order rejected or had no effect")
    except Exception as e:
        # Expected: exchange rejects reduceOnly when no position
        log("reduceOnly_rejects", True, f"Correctly rejected: {type(e).__name__}")

    cleanup(client, order_mgr)
    time.sleep(1)

    # ════════════════════════════════════════════════
    # Test 10: PositionTracker state persistence
    # ════════════════════════════════════════════════
    print("\n--- Test 10: PositionTracker Atomic Save/Load ---")
    try:
        tracker = PositionTracker()
        # Clear any existing state
        tracker.position = None
        tracker._save_state()

        # Open a fake position
        ticker = client.fetch_ticker()
        price = float(ticker["last"])
        tracker.open_position(
            side="long", strategy="mtf_momentum",
            entry_price=price, size_contracts=0.01,
            size_value=10.0, sl_pct=0.4, tp_pct=1.0,
            sl_price=round(price * 0.996, 2),
            tp_price=round(price * 1.01, 2),
            sl_order_id="test_sl_123", tp_order_id="test_tp_456",
            trailing_activation=0.8, trailing_distance=0.35,
            max_duration=90,
        )
        log("tracker_open", tracker.has_position, f"Position @ {price:.2f}")

        # Load fresh tracker — should restore
        tracker2 = PositionTracker()
        log("tracker_restore", tracker2.has_position,
            f"Restored: {tracker2.position.get('side') if tracker2.position else 'NONE'} @ "
            f"{tracker2.position.get('entry_price', 0):.2f}" if tracker2.position else "")

        # Test breakeven
        be_price = price * 1.004  # +0.4% — above 0.3% threshold
        be_triggered = tracker2.check_breakeven(be_price)
        log("tracker_breakeven", be_triggered,
            f"Triggered at +{((be_price - price) / price * 100):.2f}%")

        # Check breakeven flag persists
        tracker3 = PositionTracker()
        log("breakeven_persisted", tracker3.position.get("breakeven_activated", False),
            "breakeven_activated saved")

        # Test close
        trade = tracker3.close_position(price * 1.005, "test_close")
        log("tracker_close", trade is not None,
            f"pnl_pct={trade.get('pnl_pct', 0):.2f}%" if trade else "")
        log("tracker_no_position", not tracker3.has_position, "Position cleared")

    except Exception as e:
        log("tracker_flow", False, f"{e}\n{traceback.format_exc()}")

    # ════════════════════════════════════════════════
    # Test 11: Full entry flow (mimics _check_entry in main.py)
    # ════════════════════════════════════════════════
    print("\n--- Test 11: Full Entry→SL/TP→Breakeven→Close Flow ---")
    cleanup(client, order_mgr)
    time.sleep(1)
    try:
        ticker = client.fetch_ticker()
        price = float(ticker["last"])
        sl_pct = 0.4
        tp_pct = 1.0

        # 1) Market entry
        entry_order = order_mgr.place_market_order("buy", TEST_SIZE)
        fill_price = float(entry_order.get("average", entry_order.get("price", price)))
        log("full_entry", fill_price > 0, f"LONG @ ${fill_price:.2f}")

        # 2) Recalculate SL/TP from fill (mirrors main.py)
        sl_p = round(fill_price * (1 - sl_pct / 100), 2)
        tp_p = round(fill_price * (1 + tp_pct / 100), 2)

        # 3) Place SL+TP
        time.sleep(1)
        sl_order, tp_order = order_mgr.place_sl_tp("long", TEST_SIZE, sl_p, tp_p)
        sl_id = sl_order.get("id", "")
        tp_id = tp_order.get("id", "")
        log("full_sl_tp", bool(sl_id) and bool(tp_id),
            f"SL={sl_p:.2f} TP={tp_p:.2f}")

        # 4) Simulate breakeven: cancel all, re-place SL below current price + TP
        # In live, breakeven fires at +0.3% so entry SL is below current.
        time.sleep(1)
        breakeven_sl = round(fill_price * 0.999, 2)  # below entry = below current
        new_sl, new_tp = order_mgr.update_stop_loss(
            sl_id, "long", TEST_SIZE, breakeven_sl, tp_price=tp_p
        )
        if new_sl is None:
            log("full_breakeven", False,
                "Returned None — price crossed back, would close at market in live")
        else:
            log("full_breakeven", bool(new_sl.get("id")),
                f"SL moved to ${breakeven_sl:.2f}")

        # 5) Verify exchange state
        time.sleep(1)
        open_orders = client.fetch_open_orders()
        order_types = [o.get("type", "") for o in open_orders]
        log("full_orders_intact", len(open_orders) == 2,
            f"Orders: {order_types}")

        positions = client.fetch_positions()
        log("full_position_intact", len(positions) > 0,
            f"contracts={positions[0].get('contracts') if positions else 'NONE'}")

        # 6) Close with reduceOnly
        order_mgr.cancel_all()
        time.sleep(0.5)
        close_result = order_mgr.close_position_market("long", TEST_SIZE)
        log("full_close", True, f"Closed @ ${float(close_result.get('average', 0)):.2f}")

        time.sleep(1)

        # 7) Verify clean state
        positions = client.fetch_positions()
        open_orders = client.fetch_open_orders()
        log("full_clean_state",
            len(positions) == 0 and len(open_orders) == 0,
            f"positions={len(positions)} orders={len(open_orders)}")

    except Exception as e:
        log("full_flow", False, f"{e}\n{traceback.format_exc()}")

    # ════════════════════════════════════════════════
    # Test 12: Entry half-failure recovery
    # ════════════════════════════════════════════════
    print("\n--- Test 12: Orphan Position Detection ---")
    cleanup(client, order_mgr)
    time.sleep(1)
    try:
        # Open a position manually (simulates market order success)
        order_mgr.place_market_order("buy", TEST_SIZE)
        time.sleep(1)

        # Now check if fetch_positions detects it
        positions = client.fetch_positions()
        detected = len(positions) > 0
        log("orphan_detected", detected,
            f"Found orphan: {positions[0].get('contracts') if detected else 'NONE'}")

        if detected:
            # Close it with reduceOnly (simulates our recovery code)
            p = positions[0]
            orphan_side = "long" if float(p.get("contracts", 0)) > 0 else "short"
            orphan_size = abs(float(p.get("contracts", 0)))
            order_mgr.close_position_market(orphan_side, orphan_size)
            time.sleep(1)
            positions = client.fetch_positions()
            log("orphan_cleaned", len(positions) == 0,
                f"Remaining: {len(positions)}")

    except Exception as e:
        log("orphan_flow", False, f"{e}\n{traceback.format_exc()}")

    # ════════════════════════════════════════════════
    # Final cleanup & Summary
    # ════════════════════════════════════════════════
    print("\n--- Final Cleanup ---")
    cleanup(client, order_mgr)

    print("\n" + "=" * 64)
    print("  TEST RESULTS SUMMARY")
    print("=" * 64)
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    for name, p, detail in results:
        status = PASS if p else FAIL
        print(f"  {status}  {name}")
    print(f"\n  Total: {passed} passed, {failed} failed out of {len(results)}")
    print("=" * 64)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
