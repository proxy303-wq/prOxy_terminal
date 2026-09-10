"""Unit tests for athena2.dhan_rules - Dhan parity rules for paper/live."""
from athena2.config import Athena2Config
from athena2.contracts import OrderState
from athena2.dhan_rules import (DHAN_STATUS_MAP, DhanCharges, NIFTY_FREEZE_QTY,
                                PaperOrder, PRODUCT_MARGIN, charges_from_config,
                                dhan_symbol, map_status, margin_for_order,
                                order_payload, round_tick, simulate_fill,
                                slice_plan)


def test_status_map_covers_dhan_states():
    assert map_status("TRADED") == OrderState.FILLED
    assert map_status("PART_TRADED") == OrderState.PARTIALLY_FILLED
    assert map_status("PENDING") == OrderState.SUBMITTED
    assert map_status("TRANSIT") == OrderState.SUBMITTED
    assert map_status("REJECTED") == OrderState.REJECTED
    assert map_status("CANCELLED") == OrderState.CANCELLED
    for s in ("TRANSIT", "PENDING", "CLOSED", "TRIGGERED", "REJECTED",
              "CANCELLED", "PART_TRADED", "TRADED"):
        assert s in DHAN_STATUS_MAP


def test_tick_rounding_and_slicing():
    assert round_tick(101.23) == 101.25
    assert round_tick(101.22) == 101.20
    assert slice_plan(75) == [75]
    assert slice_plan(1800) == [1800]
    assert slice_plan(3600) == [1800, 1800]
    assert slice_plan(2000) == [1800, 200]
    assert slice_plan(0) == []


def test_order_payload_matches_dhan_contract():
    p = order_payload("1000000003", "12345", "NIFTY 15SEP26 23400 CE", "SELL", 75,
                      order_type="LIMIT", price=101.234, tag="ATHENA2")
    for k in ("dhanClientId", "correlationId", "transactionType", "exchangeSegment",
              "productType", "orderType", "validity", "tradingSymbol", "securityId",
              "quantity", "disclosedQuantity", "price", "triggerPrice",
              "afterMarketOrder", "amoTime"):
        assert k in p, k
    assert p["exchangeSegment"] == "NSE_FNO"
    assert p["productType"] == PRODUCT_MARGIN      # carry forward, not INTRADAY
    assert p["transactionType"] == "SELL"
    assert p["price"] == 101.25                    # tick rounded
    assert p["quantity"] == 75 and p["afterMarketOrder"] is False


def test_charges_match_dhan_schedule_hand_calc():
    ch = DhanCharges()
    units = 75            # one NIFTY lot
    premium = 100.0       # 100 pts premium -> turnover Rs 7,500
    sell = ch.order_charges_rs(premium, units, "SELL")
    assert sell["brokerage"] == 20.0
    assert sell["stt"] == round(7500 * 0.001, 4)          # 0.1% of premium
    assert abs(sell["exchange_txn"] - 7500 * 0.0003503) < 1e-3
    assert sell["sebi"] == round(7500 * 0.000001, 4)
    assert sell["gst"] == round((20.0 + sell["exchange_txn"] + sell["sebi"]
                                 + sell["ipft"]) * 0.18, 4)
    assert sell["stamp"] == 0.0                            # stamp only on buy
    buy = ch.order_charges_rs(premium, units, "BUY")
    assert buy["stt"] == 0.0 and buy["stamp"] > 0
    rt = ch.round_trip_rs(100.0, 50.0, units)
    assert rt > sell["total"]
    cfg = Athena2Config()
    assert charges_from_config(cfg).brokerage_per_order_rs == cfg.costs.brokerage_per_order_rs


def test_margin_fallback_is_config_estimate():
    cfg = Athena2Config()
    m = margin_for_order("12345", "SELL", 75, 100.0, cfg=cfg, client=None)
    assert m["source"] == "config_estimate"
    assert m["total"] == cfg.risk.margin_short_option_per_lot_rs
    m3 = margin_for_order("12345", "SELL", 225, 100.0, cfg=cfg, client=None)
    assert m3["total"] == 3 * cfg.risk.margin_short_option_per_lot_rs


def test_simulate_fill_market_and_limit():
    # MARKET sell fills at the bid
    o = PaperOrder(security_id="1", trading_symbol="X", side="SELL", qty=75,
                   order_type="MARKET")
    simulate_fill(o, bid=101.0, ask=103.0)
    assert o.status == "TRADED" and o.filled_qty == 75 and o.avg_price == 101.0
    assert o.state == OrderState.FILLED
    # LIMIT sell at 102 fills only when the bid reaches it
    o2 = PaperOrder(security_id="1", trading_symbol="X", side="SELL", qty=75,
                    order_type="LIMIT", price=102.0)
    simulate_fill(o2, bid=101.0, ask=103.0)
    assert o2.status == "PENDING" and o2.state == OrderState.SUBMITTED
    simulate_fill(o2, bid=102.5, ask=104.0)
    assert o2.status == "TRADED" and o2.avg_price == 102.5
    # LIMIT buy fills at the ask when ask <= limit
    o3 = PaperOrder(security_id="1", trading_symbol="X", side="BUY", qty=75,
                    order_type="LIMIT", price=104.0)
    simulate_fill(o3, bid=101.0, ask=103.5)
    assert o3.status == "TRADED" and o3.avg_price == 103.5
    # limit without price is rejected (no silent market order)
    o4 = PaperOrder(security_id="1", trading_symbol="X", side="SELL", qty=75,
                    order_type="LIMIT", price=0.0)
    simulate_fill(o4, bid=101.0, ask=103.0)
    assert o4.status == "REJECTED"


def test_dhan_symbol_format():
    s = dhan_symbol("2026-09-15", 23400, "CALL")
    assert s.startswith("NIFTY 15SEP26") and s.endswith("23400 CE")
    p = dhan_symbol("2026-09-22", 23100.0, "PUT")
    assert p.endswith("23100 PE")
