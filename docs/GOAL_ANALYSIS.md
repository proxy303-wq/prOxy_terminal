# GOAL ANALYSIS — "3×5pt winners/day @6 lots → ₹50k/month" vs the tape (05-Sep)

Goal restated: ~3 trades/day x ~4-5 premium pts x 6 lots (NIFTY), ~₹850
costs/day, 20 days, 15 green / 5 red => ~₹50k/month net.
(Arithmetic note: 4 pts x 6 lots x 65 = ₹1,560/trade, not ₹1,950 - the
example needs ~5 pts or 7.5 lots to hit ₹1,950; and measured costs at 6
lots are ~₹2,290/day, not ₹850.)

## Measured (NIFTY 2026 test window - the best 8 months of the 2y tape)
* avg capture ~1.6 net pts/unit mid, ~0.65-1.2 realistic fills
* per-lot net/day (mid) mean ₹270 (median ₹165); 2.6 trades/day; 65% green days
* realistic months at 6 lots: ₹11.7k (0.50% fills) - ₹21.7k (0.25% fills)

## Lever (a): spread selection - LARGELY CLOSED by market structure
Real chain snapshot (28-Aug, 102 liquid strikes): ZERO liquid strikes
<=0.25% one-sided; only ~11-14% <=0.5%; the deep-ITM delta-band legs run
~0.5-1.0%+ one-sided (PE median ~3% that day).  => planning base is the
exec-0.50% numbers; exec-0.25% is a stretch that requires trading only the
tightest near-ATM rows (strike-policy change, fewer qualifying, delta ~0.5).

## Lever (b): size / base capital - REAL, quantified (NIFTY risk 5pt x 65 = 325/lot)
| NIFTY basis | risk budget (0.5%) | max lots | month at 0.50% fills | at 0.25% fills |
|---|---|---|---|---|
| 0.5x (2.05L, current) | 1,025 | 3 | 5.8k | 10.9k |
| 0.75x (3.08L) | 1,538 | 4 | 7.8k | 14.5k |
| 1.0x (4.1L whole acct) | 2,050 | 6 | 11.7k | 21.7k |
| 8 lots needs 2,600 risk = 0.63% acct | | 8 | 15.6k | 28.9k |
| 9-10 lots need 0.71-0.79% acct (over plan rule) | | 9-10 | 17.5-19.4k | 32.6-36.2k |

Governor (combined open risk <= 0.75% acct = 3,075): NIFTY 6 + BN 2 =
3,510 => BLOCKS overlap; NIFTY 8 + BN 2 = 4,160 => blocks; so growing
NIFTY past 4-6 lots requires shrinking BN or raising the cap.  NIFTY alone
max inside the cap = 9 lots (2,925).

## Where 50k stands (NIFTY-only, realistic fills 0.5%)
* 6 lots: ~35% of goal | 8 lots: ~45% | max-in-governor 9 lots: ~50-55% of goal
* Even at the unreachable 0.25% fills, 10 lots ~= ₹36k = 72% of goal.
* The remaining gap to ₹50k must come from: (i) relaxing risk/governor
  (user decision, changes tail exposure), (ii) genuinely better fills than
  the measured chain (needs live spread monitoring on candidate strikes,
  BN spreads unmeasured - add capture Monday), or (iii) per-trade
  selection quality - which the DIE scorecard says is NOT measurable yet.
* Caveat: these are the best 8 months; a full-year expectation is lower.

## Open measurements needed
* BN real-chain spreads on the traded band (capture Monday) - BN is only
  additive if its fills are <=0.25%.
* Per-strike spread archive over several days (spreads widen in stress;
  one 28-Aug snapshot may overstate).
