from athena_crypto.exchange.models import Product, Ticker, Candle, OrderBook, Order


def test_product_parse():
    p = Product.from_payload({"symbol": "BTCUSD", "id": "27", "contract_type": "perpetual_futures",
                              "tick_size": "0.5", "contract_value": "0.001",
                              "contract_unit_currency": "BTC", "settling_asset": {"symbol": "USD"}})
    assert p.symbol == "BTCUSD" and p.product_id == 27
    assert p.contract_value == 0.001
    assert p.notional(100, 78000) == 7800.0
    qty = p.contracts_for_risk(10.0, 100.0)
    assert abs(qty - 100.0) < 1e-9


def test_ticker_parse():
    t = Ticker.from_payload({"symbol": "BTCUSD", "mark_price": "78100", "bid": "78099",
                             "ask": "78101", "funding_rate": "0.0001", "open_interest": "10"})
    assert t.mark_price == 78100.0 and t.funding_rate == 0.0001


def test_candle_parse():
    c = Candle.from_payload({"time": 1700000000, "open": "1", "high": "2", "low": "0.5",
                             "close": "1.5", "volume": "99"}, "BTCUSD")
    assert c.symbol == "BTCUSD" and c.volume == 99.0


def test_get_positions_requires_identifier():
    """Delta India rejects unparameterised /v2/positions - the client must insist."""
    from athena_crypto.exchange.delta_rest import DeltaRestClient
    c = DeltaRestClient(api_key="k", api_secret="s")
    try:
        c.get_positions()
    except ValueError as exc:
        assert "product_id" in str(exc)
    else:
        raise AssertionError("expected ValueError for missing identifier")


def test_orderbook_levels():
    ob = OrderBook(symbol="X")
    assert ob.mid == 0.0 and ob.spread == 0.0
