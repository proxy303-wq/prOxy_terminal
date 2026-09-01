# Commodity Notes — MCX evening-session futures mining digest

Actionable rules mined 2026-09-01 from *Mastering the Commodities Markets*
(Taylor, Pearson 2013; source `tools/_books/commodities_mastering.txt`, 342 pp).
Page cites are **printed-book pages**. Focus: intraday/short-term futures in
the Indian evening window (≈17:00–23:30 IST) on MCX-style products (crude,
natural gas, gold, silver, copper, zinc, etc.). The book is a global/UK
market-structure and hedging reference — **not** a day-trading book; Section 7
states exactly what is and isn't usable.

## 1. Market structure & product mechanics

- **Futures = standard contract, expiry = "delivery"**: legally binding
  agreement to make/take delivery of a standard quantity at a fixed date, cash
  or physical settlement (p. 36); the spec fixes quantity, grade, delivery
  location, settlement (pp. 33–34). **Forward price = spot + cost of carry**
  (financing + storage + transport/insurance; agri adds spoilage, p. 13):
  copper example — $8,150 spot + $67.97 carry = $8,217.97 fair forward; a
  theoretical breakeven, not a forecast (p. 17).
- **Contango vs backwardation**: futures above expected spot = contango
  (price decays to spot at expiry); below = backwardation (price rises to
  spot). Contango incentivises stock building, backwardation drawdown (p. 18).
  Backwardation = **convenience yield** (shortage), widening as prices rise
  and narrowing as they fall (p. 19). Curves can be kinked — contango at the
  front, backwardated further out (pp. 19, 76).
- **Convergence & basis**: futures converge to spot at expiry; arbitrage
  keeps cash/futures in line (pp. 19–20). **Basis** = futures − spot now; it
  drifts, is "incredibly difficult to hedge", and only convergence at expiry
  is certain — traders speculate on basis widening/narrowing (pp. 20–21).
- **Roll mechanics**: index rolls move 20% of the front-month basket per day
  over the 5th–9th business day (pp. 23–24). In contango the roll bleeds
  returns — agri longs lost ~15%/yr to carry + roll while spot rose
  (pp. 276–278). **Scalper implication: trade the front month but be
  roll-aware near expiry.**
- **Margins**: initial margin (SPAN-style model), marked-to-market twice
  daily, variation margin paid daily; maintenance ≈ 75% of initial (pp. 37–38).
  **Margins rise with volatility** — CME jacked gold initial margin +22% in
  one day (Aug 2011); unfunded traders were liquidated (pp. 37–38).
- **Settlement style drives tradability**: cash-settled, index-linked
  contracts "lend themselves to trading rather than hedging" (ICE coal vs
  API2/Argus, p. 124). LME metals are majority **financially settled** on the
  same prompt date (p. 227). ICE Brent delivers physically (BOFE, p. 72);
  WTI at Cushing (p. 72).
- **Daily price limits are not universal**: ICE Rotterdam coal specifies *no*
  max price flux and *no* position limits (p. 125); NYMEX has position limits
  (pp. 41, 125). Check the spec — never assume a hard band.
- **Seasonality is a curve input** (p. 17) and a demand driver: gold demand
  peaks before Diwali/Indian wedding season, Christmas, Chinese New Year
  (p. 191); natural-gas Winter = 1 Oct–31 Mar (pp. 95, 99); US gasoline (RBOB)
  "highly volatile in respect to seasonality and weather events such as
  hurricanes" (p. 72).

## 2. Session & timing rules

- **Hours actually quoted in the book**: ICE Brent trades ~01:00–23:00 London
  (p. 36); CME/NYMEX Globex ~Sun–Fri 18:00–17:15 ET with a daily break
  (pp. 40–41); LME derivatives trade 24h but "the most liquid time is during
  the London afternoon (11:45–17:00)" (p. 227); gas "market close" 16:30 GMT
  with a 10-min index-fixing window (p. 105).
- **IST translation (derived arithmetic, not in book)**: London afternoon
  11:45–17:00 = **16:15–21:30 IST** — the LME liquidity peak sits inside the
  MCX evening window; NY Globex's active US-morning stretch ≈**17:00–23:30
  IST** in winter.
- **London is the centre of OTC gold/silver** (loco-London basis, T+2
  settlement) — precious-metals price discovery is London-led (pp. 178–180).
- **What the book does NOT give**: no evening-session playbook, no EIA/USDA
  inventory-release timing, no day-of-week effects — those need external
  research. Its only event guidance is structural (§6): OPEC is a recurring
  driver (p. 67); Middle-East geopolitics moves oil (pp. 64, 77–78); "tail"
  days happen — oil spiked ~5% in one session, 2 Jan 2012 (p. 22). Treat
  scheduled OPEC/Fed/US-data windows as blackouts until the print settles.

## 3. Product selection (what to trade in the evening)

- **Identify trending vs choppy first** — the book's most day-trade-relevant
  instruction: "dangers in choppy rather than trending markets, of being
  whipped in and out… identify early what kind of market you are in"
  (p. 197).
- **Product character**: oil is mature — front-month "flat price" plus a
  curve of calendar spreads, swinging wildly on politics (pp. 76–78); base
  metals are macro-levered — GDP, China/India demand, dollar (pp. 220–223,
  225); precious metals are dollar/geopolitics-driven with a safe-haven bid
  (pp. 6, 189). "Fundamentals seem not to be driving the price as they should
  be" — speculators dominate, so expect volatility detached from
  fundamentals (p. 225).
- **Liquidity is the product filter**: CME #1, ICE Futures Europe #5, **MCX
  #6 worldwide by contracts in 2010** (p. 53). On the LME, trade inside the
  London afternoon; liquidity thins outside it (p. 227). Tailored OTC is a
  liquidity trap — the more customised, the harder to exit (p. 229).
- **Silver vs gold**: silver is the higher-beta metal (gold avg +22%/yr,
  silver +23%/yr, 596% cumulative 2001–11, pp. 187–189) — more range, more
  heat; PGMs are supply-shock-prone, behave differently (p. 187).
- **Practical pick for the window**: 1–2 liquid names only — crude (news
  energy, trends on OPEC/inventory) + one metal (gold or silver, London-led,
  seasonally bid in Indian festival months, p. 191); natural gas = seasonal/
  weather story, US-winter watchlist (pp. 72, 95, 99).

## 4. Trading methodology the book actually recommends

- **Technicals (pp. 195–199 — the only real "how to trade" section)**: watch
  the short-term MAs — **9- and 18-day MAs and MACD (12, 26, 9)**; crossovers
  "give accurate buy and sell signals that pick up all of the major moves"
  (pp. 196–197). MACD = trend + momentum in one: signal/centreline crossovers,
  divergences (pp. 197–198).
- **The 2011 silver case study**: three buys / two sells by MACD caught +85%,
  +27%, +17%, +22% swings; the final tiny +9% gain was the tell the trend was
  dying (p. 198). Lesson: **small winning trades on repeated signals = trend
  ending — scale back, don't chase.**
- **Trend identification is mandatory**: "the trend is your friend" (p. 196),
  but MACD only works in trending markets; in sideways chop it whipsaws
  (pp. 197–198). Classify regime before trusting momentum — same lesson as
  Volman/Miner in `docs/STRATEGY_NOTES.md`. Charts are also self-fulfilling:
  programme/hedge-fund trading makes key levels real (pp. 196–197) — S/R and
  breakouts matter *more*, not less, in commodities.
- **Day vs position trading**: the book has NO intraday playbook — its MA/
  MACD signals are daily-chart swing tools; for a 5-min scalper they are a
  **context filter (daily trend direction), not an entry trigger**.
- **Fundamental overlay for direction**: gold vs the dollar — "dollar down,
  gold up" (p. 189); base metals vs GDP/China data (pp. 220–222); agri vs
  weather extremes (p. 271). Macro driver = bias; charts = timing (p. 195:
  technicians ignore value; fundamentals set the backdrop).

## 5. Risk & money management (commodity-specific)

- **Leverage is brutal**: coal example — a 5.73% price move generated **91%
  ROI** on initial margin; "leverage is a double-edged weapon", adverse moves
  trigger top-up calls or forced liquidation (p. 126). Expect MCX-style
  leverage (~8–15×) to multiply P&L and slippage.
- **Daily settlement forces discipline**: variation margin is crystallised
  daily against the settlement price — you cannot "hold and hope" intraday
  losses away (pp. 38, 59); the margin level itself can change mid-trade in a
  volatility spike (pp. 37–38).
- **Tail risk is real**: 95% CI ≈ ±2.5% daily, but tail days (political
  shock, OPEC surprise) blow far past it (p. 22) — a fixed % stop that works
  on a normal day can gap through on an event day; size for the gap
  (→ Miner/Tharp sizing, `docs/STRATEGY_NOTES.md` §§2, 6).
- **Futures pros/cons as framed**: pros — price transparency, low initial
  margin, small tickets; cons — "liquidity can be poor; … inflexible
  maturities", and "trading futures in a volatile market carries a high risk"
  with "tight internal controls" required (p. 194).
- **Clearing/counterparty**: exchange clearing removes counterparty risk — but
  broker default is real (MF Global, pp. 44–45, 65–66) — broker diligence
  matters.

## 6. Evening-session / news-driven moves

- The book's honest content: **"traders react to real-time news every day"**
  while physical supply takes years to adjust — news, not fundamentals, is the
  short-horizon price driver (p. 224); commodities can rise even when the
  economy stalls (p. 6).
- **Inventories/curves are the news lens**: curve shape is driven by
  inventories (p. 17) and is a leading indicator — backwardation widens on
  rising prices (p. 19). The front-month vs deferred spread (e.g. Apr/May) is
  the fastest read on whether a move is supply-driven (p. 76). But forward
  curves are *not* forecasts — never trade a curve shape as a prediction
  (p. 230).
- **Event handling (synthesised from pp. 18–22, 67, 77)**: after a shock the
  curve flips shape (kinked contango/backwardation) and basis moves hard — a
  scalper should (a) stand aside through the print, (b) trade the *post-print*
  re-establishment of front vs deferred, (c) treat a curve-shape flip as a
  regime change, not a dip to fade.

## 7. Honest scope assessment

The book is a 2012/2013 institutional market-structure reference written for
hedgers and long-term investors — the bulk (Chs 3–12) is supply/demand
fundamentals, pricing benchmarks and hedging instruments (swaps, caps, EFPs).
**It is NOT a day-trading book**: no intraday entries/exits, no stop rules, no
session-timing playbook, no EIA/USDA event guidance, no Indian-market specifics
beyond listing MCX as a top-6 exchange (p. 53). Directly usable for an evening
futures scalper: curve/basis mechanics (pp. 17–21, 229–230), margin/leverage
reality (pp. 37–38, 124–126), liquidity-hour facts (pp. 36, 40–41, 227), and
the precious-metals technical-analysis section (pp. 195–199). The rest is
context, not rules.

## Implementation status (2026-09-01)

- ✅ **Adopt now, no code needed:**
  - Regime filter before momentum entries — "identify what kind of market you
    are in" (p. 197): reuse the existing choppiness gate; do not take
    MACD-style signals in flat regimes.
  - **Daily-trend context**: 9/18-day MA or MACD(12,26,9) on the daily chart
    as a one-way filter for the evening session (pp. 196–198) — mirrors the
    Miner HTF gate (`MOMENTUM_FILTER_ENABLED` family in `proxy/config.py`).
  - Curve-state feature: front-month − next-month spread sign = contango/
    backwardation regime (pp. 18–19, 76) — expose in the engine; later a
    filter for longs in steep contango.
- 📊 **A/B candidates (`tools/strat_ab.py`):** evening-window blackout around
  event prints (OPEC, US CPI/FOMC, EIA inventory) — cf. the lunch-doldrums
  filter that shipped in `docs/STRATEGY_NOTES.md`; "small-win streak" taper —
  after ≥3 consecutive small winners, cut size or stop (silver 2011 case,
  p. 198, trend-death tell).
- ⏳ **On the list (needs external data, not in book):** EIA weekly crude
  inventory timing (~20:00/22:30 IST), USDA report calendar, COMEX/NYMEX
  settlement times, MCX contract specs (price bands, expiry, margins), and
  IST-mapped volatility-by-hour for crude/gold/NG.
- ⚠️ **Risk defaults for the MCX evening desk (derived from pp. 37–38,
  125–126): margin-aware sizing** — cap notional leverage ≤ ~10× (book: 5.7%
  move = 91% ROI); daily loss halt well under the forced-liquidation reality;
  treat intraday margin-change announcements as immediate risk events.
