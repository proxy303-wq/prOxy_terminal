# Volman Audit — price_action.py vs the 5-Minute Time Frame

Source: Bob Volman, *Understanding Price Action: Practical Analysis of the
5-Minute Time Frame* (2014). Mined by subagent from the PDF; page cites are
printed-book pages (PDF index − 9).

Context: the terminal's `proxy/price_action.py` implements four setup types —
`STRUCTURE_BREAKOUT`, `DEAD_ZONE_BREAKOUT`, `PULLBACK_ENTRY`, `LIQUIDITY_SWEEP` —
gated by a setup-strength score ≥ 55 and confidence ≥ 70% (`MIN_SETUP_STRENGTH`,
`MIN_CONFIDENCE_PCT` in `proxy/config.py`). This audit maps Volman's 5-minute
rules onto those setups and lists what to tighten.

## What Volman actually requires on the 5m frame

1. **A tradable "zone" is a defended level PLUS buildup.** A bare S/R level is
   not enough: he wants a cluster of alternating bars fighting at the barrier
   ("pre-breakout tension"), at least ~4 bars of it — "the fatter the buildup,
   the better" (pp. 88, 74). Levels act as magnets and bounces; broken support
   flips to resistance (pp. 8–9). The best zone is a **triple**: a 50–60%
   retracement coinciding with the 25-EMA and a prior S/R test (pp. 78–79,
   126–127).
2. **Breakout = signal bar + entry bar.** A signal bar that closes *against*
   the barrier (in the direction of the intended break), then an entry bar that
   takes the signal bar's high/low out (pp. 80–81). Before any break, check:
   line with dominant pressure, trending vs ranging context, and obstacles/
   magnets to the target — "if we don't find buildup there, the offer is best
   declined" (pp. 13, 121).
3. **Decline these:** false-break traps (no buildup), tease breaks (buildup away
   from the barrier), entries far from the 25-EMA, bars exceeding average span
   ("frantic"), anything against a trending 25-EMA, shorting below a strong
   bullish bar / longing above a strong bearish bar (pp. 13, 22–24, 81, 126, 213).
4. **Pullback = 50–60% retrace of the dominant swing** (40% OK in a very strong
   trend), reaching/preferring to pierce the 25-EMA, hitting a technical test,
   then **stalling/buildup in the turn** — never buy "into the void"; enter on
   the break of the turnaround signal bar (pp. 22, 31, 126–129). The first
   pullback to the EMA in a new swing is strongest; later ones weaken (pp. 127–128).
5. **Stops/targets:** the 1:2 bracket (his 20-pip target / 10-pip stop) is
   exactly the terminal's 1% / 0.5% premium plan (pp. 68, 81); in low
   volatility, shrink to 8–10 pip targets with ~8-pip stops (pp. 398–399).
6. **Filters:** skip the 12:00–14:00 lunch doldrums (pp. 182, 184); shun major
   macro releases (pp. 144–147); skip when bars exceed average span (p. 213).

## Setup-by-setup verdict

| Setup | Volman verdict | Gap vs Volman's rule |
|---|---|---|
| `STRUCTURE_BREAKOUT` | **Fires on the false/tease-break trap** (pp. 13, 22) | Fires on mere perforation of a clustered level (close > level + ATR) with **no buildup requirement** → add a ≥4-bar squeeze/tension check at the level before accepting the break |
| `DEAD_ZONE_BREAKOUT` | Closest to a "proper break" | Correct shape (box + fresh break), but trigger is box-high + ATR with no signal-bar/entry-bar structure → require the signal bar closing in line with the break and an entry bar taking it out |
| `PULLBACK_ENTRY` | Missing the core conditions | Requires only retrace + one bounce pattern bar → add the 50–60% retrace rule, 25-EMA proximity test, and ≥1 stall bar before the bounce bar counts |
| `LIQUIDITY_SWEEP` | **False highs/lows are "no reason to act" alone** (pp. 17–19, 28) | Trades the reclaim bar itself → convert to a two-step event: sweep = watch; trade only the subsequent break of the mini-buildup, stop beyond the false-swing extreme |

## Recommended changes (ranked)

1. **Buildup gate for STRUCTURE_BREAKOUT** — require ≥4 bars of pre-break
   tension (range compression at the level) before a level-perforation counts;
   this kills the biggest source of false breaks.
2. **Signal-bar/entry-bar structure** — entry only on the break of a signal bar
   whose close is in line with the break; never enter on a strong
   counter-direction bar.
3. **25-EMA proximity filter** — decline setups beyond N×ATR from the 25-EMA /
   15m mean; make it a hard gate for pullbacks.
4. **PULLBACK_ENTRY fixes** — 50–60% retrace (40% in strong trend) of the
   dominant swing + EMA touch + stall-bar confirmation.
5. **LIQUIDITY_SWEEP two-step** — sweep alone never enters; trade the
   subsequent mini-buildup break with the stop beyond the false extreme.
6. **Lunch-doldrums filter** — suppress entries 12:00–14:00 IST (maps directly
   to the NIFTY lunch lull and would cut dead trades).

## What this means for the backtest

The honest A/B (July 2026) shows the terminal's setups at strength ≥ 55 produce
a 64% win rate with the lock-profit layer on NIFTY — but Volman's own stats
expect ~1:2 brackets with 2 losses per win to break even. The setup-strength
score is a proxy for his checklist; the recommended changes above are the
concrete tightening. Validate each change with the per-setup stats now reported
in every backtest (`setup_stats` in the JSON report: trades, win rate, avg R,
net P&L per setup type) before keeping it.
