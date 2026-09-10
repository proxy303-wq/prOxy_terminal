import hmac
import hashlib

from athena_crypto.exchange.signing import (
    body_string, generate_signature, query_string, sign_request,
)


def test_query_string_serialisation():
    q = {"symbol": "BTC USD", "n": 3}
    assert query_string(q) == "?symbol=BTC+USD&n=3"
    assert query_string(None) == ""
    assert query_string({}) == ""


def test_body_compact():
    assert body_string({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'
    assert body_string(None) == ""


def test_signature_matches_hmac():
    secret = "supersecret"
    message = "GET1788989000/v2/wallet/balances"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    assert generate_signature(secret, message) == expected


def test_sign_request_components():
    sig, ts = sign_request("sec", "GET", "/v2/orders", query={"product_id": 27}, payload=None)
    expect_ts = ts  # deterministic within call
    msg = "GET" + expect_ts + "/v2/orders" + "?product_id=27" + ""
    assert sig == generate_signature("sec", msg)


def test_post_body_included():
    sig, ts = sign_request("sec", "POST", "/v2/orders", payload={"size": 2, "side": "buy"})
    msg = "POST" + ts + "/v2/orders" + "" + body_string({"size": 2, "side": "buy"})
    assert sig == generate_signature("sec", msg)
