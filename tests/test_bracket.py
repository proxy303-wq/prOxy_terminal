"""Super-order (bracket) payload builder tests (offline, no network)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.dhan_broker import DhanBroker

def main():
    ok = True
    def chk(tag, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS" if cond else "FAIL"), tag)
    # sample mirroring the user's screenshot: NIFTY 29 SEP 24000 CE, entry ~261.45,
    # target 268.10, SL 255.00, 9 lots
    p = DhanBroker.build_bracket_payload(
        client_id="1100220382", side="BUY", instrument="NIFTY 29SEP 24000 CE",
        quantity=585, security_id=12345, trading_symbol="NIFTY-Sep2026-24000-CE",
        entry_price=261.45, target_price=268.10, stop_price=255.00,
        trailing_jump=1.0, order_type="LIMIT", tag="PrOxyV41")
    print("payload:", p)
    chk("transaction BUY", p["transactionType"] == "BUY")
    chk("NSE_FNO", p["exchangeSegment"] == "NSE_FNO")
    chk("qty", p["quantity"] == 585)
    chk("entry price", abs(p["price"] - 261.45) < 1e-9)
    chk("target", abs(p["targetPrice"] - 268.10) < 1e-9)
    chk("stop", abs(p["stopLossPrice"] - 255.00) < 1e-9)
    chk("trailing", abs(p["trailingJump"] - 1.0) < 1e-9)
    chk("corr id", p["correlationId"] == "PrOxyV41")
    p2 = DhanBroker.build_bracket_payload("c", "SELL", "X", 1, 2, "T", 100, 110, 90, order_type="MARKET")
    chk("market entry price 0", p2["price"] == 0.0 and p2["orderType"] == "MARKET")
    p3 = DhanBroker.build_bracket_payload("c", "BUY", "X", 1, 2, "T", 0, 110, 90, trigger_price=101.5)
    chk("trigger present", abs(p3["triggerPrice"] - 101.5) < 1e-9)
    chk("bad order type coerced", DhanBroker.build_bracket_payload("c", "BUY", "X", 1, 2, "T", 0, 1, 2, order_type="STOP_LOSS")["orderType"] == "MARKET")
    print("\nBRACKET TESTS:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
