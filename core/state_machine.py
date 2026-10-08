"""The multi-timeframe state machine (PLAN.md → Strategy + Stage 2 step 8). One instance per symbol.

Chain of "levels", each = (higher TF, lower TF), all working the same way:

    FOLLOWING       → pass the parent's direction through
    AT_OPPOSING_OB  → an opposing higher-TF OB got tapped → PAUSED (no trades) until the lower TF CHoCHs
    FLIPPED         → lower-TF CHoCH in the OB's direction → this level now points the other way

The first level follows the top TF's own structure trend. The last level's output = the trade direction.

Entry (v1, M15 only):
    an OB on a POI TF (H4/H1) in the trade direction is TAPPED (its inducement was swept on the way in)
    → an M15 CHoCH in the trade direction, whose reversal started inside that POI
    → wait until one of the M15 blocks that CHoCH created (its CHoCH OB, or a breaker/mitigation block it flipped)
      gets an inducement → limit at that block (bullish: its high), SL beyond the M15 swing the reversal
      started from (+ an optional buffer in M15 ATRs, default 0), TP at the next H1 swing high/low in the trade direction.
    → that starts a CAMPAIGN on the POI. If continuations are on (default off): every later M15 OB in the same direction gets a limit at its edge once it
      has its inducement (continuation entries, same TP). The campaign ends when price takes the TP level,
      the POI breaks, or price takes out the swing the first setup's SL sat behind. Each POI gives at most one setup.

Higher-TF events are seen when that TF's candle closes (feed() in core/candles.py hands out closed candles only).
"""
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from .detectors import Break, BreakKind, OrderBlock, OrderBlockDetector, OrderBlockStatus, SwingDirection
from .detectors.structure import StructureDetector

# (higher TF, lower TF that confirms its flips) — top to bottom. Keys match feed() in core/candles.py.
DEFAULT_LEVELS = [("w1", "d1"), ("d1", "h4"), ("h4", "h1")]
DEFAULT_POI_TFS = ["h4", "h1"]
DEFAULT_ENTRY_TF = "m15"
# TP: "poi_swing" = the swing price ran to from the POI's inducement before coming back to sweep it (the liquidity
#     left behind, e.g. a sell from a breaker targets the low price rallied from). "liquidity" = the next swing on
#     DEFAULT_TARGET_TF. EURUSD/XAUUSD/BTCUSD: H1 swings ~-0.17R/trade, H4 swings worse (~15% win).
# "htf_ob" = the near edge of the nearest OPPOSING higher-TF OB in the trade's way (a buy targets the closest live
#     bearish H1/H4 OB above price): a pullback against the trend usually runs into a higher-TF OB, then turns.
DEFAULT_TARGET_MODE = "liquidity"
DEFAULT_TARGET_OB_TFS = ["h1", "h4"]
DEFAULT_TARGET_TF = "h1"
DEFAULT_SL_BUFFER_ATR = 0.0     # space behind the SL, in entry-TF ATRs (symbol-agnostic). EURUSD: 0 did best
DEFAULT_CONTINUATIONS = False   # continuation entries inside a campaign. EURUSD: they didn't add an edge yet
# "immediate":  limit straight away after the M15 CHoCH, on the block it created that price reaches first
#               (its CHoCH OB, or a breaker/mitigation block the same CHoCH flipped). No M15 inducement needed.
# "inducement": after the M15 CHoCH, wait — the limit goes on whichever of those blocks gets its inducement first.
DEFAULT_ENTRY_MODE = "immediate"
SPREAD_CANDLES = 96             # spread space = average spread of the last day of entry-TF candles (96 x M15)
# Defaults = the FIRST version (best so far: EURUSD ~25% win, +0.16R/trade). The later ideas stay available as
# switches and each gets tested on its own against this baseline before it becomes a default.
DEFAULT_BREAKERS_AND_MITIGATION = False     # breaker / mitigation blocks as POIs and M15 entry blocks
DEFAULT_SINGLE_USE = False      # a tapped zone becomes spent once price leaves past its inducement
DEFAULT_EARLY_TAPS = False      # see H1/H4 taps on M15 candles instead of at the H1/H4 close
DEFAULT_MIN_RR = 0.0            # skip setups whose TP is less than this many times the SL distance (0 = off)
DEFAULT_ENTRY_BLOCKS = "choch"  # "choch": limit at the M15 CHoCH OB. "nearest": the CHoCH OB or a flipped block, nearest first
CONTEXT_POI_TFS = ["d1", "h4", "h1"]   # live POIs saved in each setup's snapshot (for the context chart)
CONTEXT_POIS_PER_TF = 8
ATR_PERIOD = 14


class LevelState(StrEnum):
    FOLLOWING = "FOLLOWING"
    AT_OPPOSING_OB = "AT_OPPOSING_OB"
    FLIPPED = "FLIPPED"


class Level:
    """One step of the chain: follows `bias_in`, pauses at an opposing HTF OB, flips on a lower-TF CHoCH."""

    def __init__(self, htf : str, ltf : str):
        self.htf = htf
        self.ltf = ltf
        self.state = LevelState.FOLLOWING
        self.opposing_ob : OrderBlock | None = None
        self.flipped_to : SwingDirection | None = None

    def update(self, bias_in : SwingDirection | None, htf_obs : list[OrderBlock], ltf_breaks : list[Break]) -> SwingDirection | None:
        """htf_obs = HTF OBs that changed status this tick, ltf_breaks = LTF breaks this tick.
        Returns this level's direction, or None = paused / unknown."""
        if bias_in is None:
            self.reset()
            return None

        if self.state == LevelState.FOLLOWING:
            for ob in htf_obs:
                if ob.status == OrderBlockStatus.TAPPED and ob.direction != bias_in:
                    self.state = LevelState.AT_OPPOSING_OB
                    self.opposing_ob = ob
                    break

        if self.state == LevelState.AT_OPPOSING_OB:
            if self.opposing_ob.status == OrderBlockStatus.INVALID:
                # price ran straight through the reversal zone → the trend carries on
                self.reset()
            elif any(b.kind == BreakKind.CHOCH and b.direction == self.opposing_ob.direction for b in ltf_breaks):
                self.state = LevelState.FLIPPED
                self.flipped_to = self.opposing_ob.direction
                self.opposing_ob = None
                return self.flipped_to
            else:
                return None

        if self.state == LevelState.FLIPPED:
            failed = any(b.kind == BreakKind.CHOCH and b.direction == bias_in for b in ltf_breaks)
            if bias_in == self.flipped_to or failed:
                # the parent caught up, or the lower TF turned back → just follow again
                self.reset()
            else:
                return self.flipped_to

        return bias_in

    def reset(self):
        self.state = LevelState.FOLLOWING
        self.opposing_ob = None
        self.flipped_to = None


@dataclass
class Setup:
    time : pd.Timestamp             # close time of the M15 candle that confirmed it
    direction : SwingDirection
    entry : float                   # limit price
    sl : float                      # swing_sl pushed back by the buffer
    tp : float
    poi_tf : str
    poi : OrderBlock
    entry_ob : OrderBlock
    swing_sl : float = 0.0          # the SL right on the swing, no buffer
    atr : float = 0.0               # entry-TF ATR when the setup fired
    kind : str = "first"            # "first" = the CHoCH out of the POI, "continuation" = a later M15 OB
    # why the bot took it: every TF's structure + every chain level's state at the moment it fired
    context : dict | None = field(default=None, compare=False, repr=False)


@dataclass
class Campaign:
    """Everything traded off one POI: the first setup, then continuation entries in the same direction.
    Over once price takes the target (the higher-TF swing), or the idea is dead: the POI breaks, price takes out
    the swing the first setup's SL sat behind (`invalidation`), or the trade direction changes."""
    direction : SwingDirection
    target : float
    poi_tf : str
    poi : OrderBlock
    start : pd.Timestamp
    invalidation : float | None = None      # the first setup's swing SL (no buffer)

    def is_over(self, candle, bias : SwingDirection | None) -> bool:
        if bias != self.direction or self.poi.status == OrderBlockStatus.INVALID:
            return True
        if self.direction == SwingDirection.BULLISH:
            broken = self.invalidation is not None and candle.low < self.invalidation
            return broken or candle.high >= self.target
        broken = self.invalidation is not None and candle.high > self.invalidation
        return broken or candle.low <= self.target


def stop_level(direction : SwingDirection, block : OrderBlock, leg_extreme : float) -> float:
    """Where the SL goes (before any buffer): beyond the swing the reversal started from, or the block, whichever
    is further. Same for every entry block — breakers too (a stop at the breaker candle itself was tested and got
    wicked out: 3-14% win rate)."""
    if direction == SwingDirection.BULLISH:
        return min(leg_extreme, block.low)
    return max(leg_extreme, block.high)


def rr_ok(entry : float, sl : float, tp : float, min_rr : float) -> bool:
    """True when the TP is at least `min_rr` times the SL distance away (min_rr 0 = no filter)."""
    # tiny tolerance so a price-exact 1.5:1 isn't lost to float rounding (1.1015 - 1.1000 = 0.00149999...)
    return min_rr <= 0 or abs(tp - entry) >= min_rr * abs(entry - sl) * (1 - 1e-9)


def order_prices(direction : SwingDirection, block : OrderBlock, leg_extreme : float,
                 sl_buffer : float = 0.0, spread : float = 0.0) -> tuple[float, float, float]:
    """(entry, swing_sl, sl) for a limit at `block`. `spread` is extra space for the spread on both sides:
    the entry moves toward price (fills a touch earlier), the SL moves away (survives a spread-sized wick)."""
    swing_sl = stop_level(direction, block, leg_extreme)
    if direction == SwingDirection.BULLISH:
        return block.high + spread, swing_sl, swing_sl - sl_buffer - spread
    return block.low - spread, swing_sl, swing_sl + sl_buffer + spread


def early_tap_step(ob : OrderBlock, candle, swept : bool) -> tuple[bool, bool, bool]:
    """Check one ENTRY-TF candle against a higher-TF OB that already has its inducement, without waiting for the
    higher-TF candle to close. Same rules as the OB itself: sweep the inducement, then reach the zone.
    Returns (swept, tapped, broken)."""
    if ob.direction == SwingDirection.BULLISH:
        if candle.low < ob.low:
            return swept, False, True
        swept = swept or candle.low < ob.inducement
        return swept, swept and candle.low <= ob.high, False
    if candle.high > ob.high:
        return swept, False, True
    swept = swept or candle.high > ob.inducement
    return swept, swept and candle.high >= ob.low, False


@dataclass
class PendingEntry:
    """An M15 CHoCH out of a POI, waiting for one of its M15 blocks to get an inducement."""
    direction : SwingDirection
    poi_tf : str
    poi : OrderBlock
    candidates : list[OrderBlock]       # the M15 blocks the CHoCH created in `direction`
    leg_extreme : float                 # the swing the reversal started from
    target : float
    start : pd.Timestamp

    def is_over(self, candle, bias : SwingDirection | None) -> bool:
        if bias != self.direction:
            return True
        if all(c.status == OrderBlockStatus.INVALID for c in self.candidates):
            return True
        if self.direction == SwingDirection.BULLISH:
            return candle.high >= self.target      # ran to the target without us
        return candle.low <= self.target

    def ready(self) -> list[OrderBlock]:
        return [c for c in self.candidates if c.inducement is not None and c.status != OrderBlockStatus.INVALID]


def pending_entry_setup(time, pending : PendingEntry, sl_buffer : float = 0.0, atr : float = 0.0,
                        spread : float = 0.0, min_rr : float = 0.0) -> Setup | None:
    """The limit goes on the block whose inducement appeared first (if two at once: the one price reaches first)."""
    ready = pending.ready()
    if not ready:
        return None
    if pending.direction == SwingDirection.BULLISH:
        block = max(ready, key=lambda c: c.high)
        entry, swing_sl, sl = order_prices(pending.direction, block, pending.leg_extreme, sl_buffer, spread)
        if not sl < entry < pending.target:
            return None
    else:
        block = min(ready, key=lambda c: c.low)
        entry, swing_sl, sl = order_prices(pending.direction, block, pending.leg_extreme, sl_buffer, spread)
        if not pending.target < entry < sl:
            return None
    if not rr_ok(entry, sl, pending.target, min_rr):
        return None
    return Setup(time, pending.direction, entry, sl, pending.target, pending.poi_tf, pending.poi, block, swing_sl, atr)


def nearest_block(direction : SwingDirection, blocks : list[OrderBlock]) -> OrderBlock | None:
    """The block price reaches first on the way back: the highest buy zone, or the lowest sell zone."""
    if not blocks:
        return None
    if direction == SwingDirection.BULLISH:
        return max(blocks, key=lambda b: b.high)
    return min(blocks, key=lambda b: b.low)


def continuation_setup(time, campaign : Campaign, ob : OrderBlock, leg_extreme : float,
                       sl_buffer : float = 0.0, atr : float = 0.0, spread : float = 0.0,
                       min_rr : float = 0.0) -> Setup | None:
    """A limit at an M15 OB that just got its inducement, inside a running campaign.
    leg_extreme = the swing the OB's break started from (bullish: its low, bearish: its high)."""
    if ob.direction != campaign.direction or ob.time <= campaign.start:
        return None
    if campaign.direction == SwingDirection.BULLISH:
        entry, swing_sl, sl = order_prices(campaign.direction, ob, leg_extreme, sl_buffer, spread)
        if not sl < entry < campaign.target:
            return None
    else:
        entry, swing_sl, sl = order_prices(campaign.direction, ob, leg_extreme, sl_buffer, spread)
        if not campaign.target < entry < sl:
            return None
    if not rr_ok(entry, sl, campaign.target, min_rr):
        return None
    return Setup(time, campaign.direction, entry, sl, campaign.target, campaign.poi_tf, campaign.poi, ob,
                 swing_sl, atr, kind="continuation")


def build_setup(time, direction : SwingDirection, pois : list[tuple[str, OrderBlock]], leg_extreme : float,
                entry_ob : OrderBlock, target : float | None, sl_buffer : float = 0.0, atr : float = 0.0,
                spread : float = 0.0, min_rr : float = 0.0) -> Setup | None:
    """pois = (tf, OB) candidates: tapped, unused, in `direction`.
    leg_extreme = where the lower-TF reversal started (bullish: its low, bearish: its high).
    sl_buffer = extra price distance behind the swing for the SL."""
    if target is None:
        return None
    for tf, poi in pois:
        if direction == SwingDirection.BULLISH:
            if not poi.low <= leg_extreme <= poi.high:      # the reversal must start inside the POI
                continue
            entry, swing_sl, sl = order_prices(direction, entry_ob, leg_extreme, sl_buffer, spread)
            if not sl < entry < target:
                return None
        else:
            if not poi.low <= leg_extreme <= poi.high:
                continue
            entry, swing_sl, sl = order_prices(direction, entry_ob, leg_extreme, sl_buffer, spread)
            if not target < entry < sl:
                return None
        if not rr_ok(entry, sl, target, min_rr):
            return None
        return Setup(time, direction, entry, sl, target, tf, poi, entry_ob, swing_sl, atr)
    return None


def pick_target(poi : OrderBlock, direction : SwingDirection, mode : str, structure : StructureDetector,
                live_obs : dict[str, list[OrderBlock]] | None = None, price : float | None = None) -> float | None:
    if mode == "poi_swing":
        return poi.swing_target
    if mode == "htf_ob":
        return nearest_opposing_ob(live_obs or {}, direction, price)
    return liquidity_target(structure, direction)


def nearest_opposing_ob(live_obs : dict[str, list[OrderBlock]], direction : SwingDirection,
                        price : float | None) -> float | None:
    """The near edge of the closest opposing OB beyond `price`, across the given TFs.
    Buy → the lowest bearish-OB low above price. Sell → the highest bullish-OB high below price."""
    if price is None:
        return None
    if direction == SwingDirection.BULLISH:
        edges = [ob.low for obs in live_obs.values() for ob in obs
                 if ob.direction == SwingDirection.BEARISH and ob.low > price]
        return min(edges) if edges else None
    edges = [ob.high for obs in live_obs.values() for ob in obs
             if ob.direction == SwingDirection.BULLISH and ob.high < price]
    return max(edges) if edges else None


def liquidity_target(structure : StructureDetector, direction : SwingDirection) -> float | None:
    """The next swing high (bullish) / swing low (bearish) on this TF — where the TP goes."""
    if structure.trend is None:
        return None
    if direction == SwingDirection.BULLISH:
        if structure.trend == SwingDirection.BULLISH:
            # the high still to break, or (right after a BOS, before a new swing) the top of the leg
            return structure.bos_level if structure.bos_level is not None else structure.choch_extreme
        return structure.choch_level    # bearish trend: its CHoCH level is a swing high
    if structure.trend == SwingDirection.BEARISH:
        return structure.bos_level if structure.bos_level is not None else structure.choch_extreme
    return structure.choch_level


class StateMachine:

    def __init__(self, levels=DEFAULT_LEVELS, poi_tfs=DEFAULT_POI_TFS, entry_tf=DEFAULT_ENTRY_TF,
                 target_tf=DEFAULT_TARGET_TF, sl_buffer_atr=DEFAULT_SL_BUFFER_ATR, continuations=DEFAULT_CONTINUATIONS,
                 entry_mode=DEFAULT_ENTRY_MODE, point : float = 0.0, spread_mult : float = 1.0,
                 target_mode=DEFAULT_TARGET_MODE, breakers_and_mitigation=DEFAULT_BREAKERS_AND_MITIGATION, single_use=DEFAULT_SINGLE_USE,
                 early_taps=DEFAULT_EARLY_TAPS, entry_blocks=DEFAULT_ENTRY_BLOCKS,
                 target_ob_tfs=DEFAULT_TARGET_OB_TFS, min_rr=DEFAULT_MIN_RR):
        """point = the symbol's price step (MT5 symbol_info().point, e.g. 0.00001 EURUSD). Candle spreads are in
        points, so the spread space is spread x point x spread_mult. point=0 → no spread space."""
        self.levels = [Level(htf, ltf) for htf, ltf in levels]
        self.poi_tfs = poi_tfs
        self.entry_tf = entry_tf
        self.target_tf = target_tf
        self.target_mode = target_mode
        self.early_taps = early_taps
        self.min_rr = min_rr
        # every OB that is still a zone (not invalid / spent) on the TFs the "htf_ob" target looks at
        self.target_ob_tfs = target_ob_tfs
        self.live_obs : dict[str, list[OrderBlock]] = {tf: [] for tf in target_ob_tfs}
        self.live_ids : set[int] = set()
        self.entry_blocks = entry_blocks
        self.sl_buffer_atr = sl_buffer_atr
        self.continuations = continuations
        self.entry_mode = entry_mode
        self.point = point
        self.spread_mult = spread_mult
        self.spreads : deque[float] = deque(maxlen=SPREAD_CANDLES)
        self.pending : list[PendingEntry] = []
        self.true_ranges : deque[float] = deque(maxlen=ATR_PERIOD)
        self.prev_close : float | None = None
        tfs = {tf for pair in levels for tf in pair} | set(poi_tfs) | {entry_tf, target_tf} | set(target_ob_tfs)
        self.detectors = {tf: OrderBlockDetector(breakers_and_mitigation=breakers_and_mitigation, single_use=single_use) for tf in tfs}
        self.bias : SwingDirection | None = None
        self.used_pois : set[int] = set()
        self.active_pois : dict[str, list[OrderBlock]] = {tf: [] for tf in poi_tfs}   # tapped, not yet broken
        # POI OBs with an inducement but not tapped yet: watched on every entry-TF candle (ob id → swept yet?)
        self.watching : dict[str, dict[int, tuple[OrderBlock, bool]]] = {tf: {} for tf in poi_tfs}
        self.early_tapped : set[int] = set()
        self.campaigns : list[Campaign] = []
        self.ob_leg_extreme : dict[int, float] = {}     # entry-TF OB → the swing its break started from
        self.setups : list[Setup] = []

    def update(self, now, candles : dict[str, list]) -> list[Setup]:
        """One tick of feed(): `candles` maps TF → the candles that closed at `now`."""
        changed, breaks = {}, {}
        for tf, detector in self.detectors.items():
            changed[tf], breaks[tf] = [], []
            for candle in candles.get(tf, []):
                n = len(detector.structure.breaks)
                changed[tf] += detector.update(candle)
                breaks[tf] += detector.structure.breaks[n:]
        entry_structure = self.detectors[self.entry_tf].structure
        for ob in changed[self.entry_tf]:
            if id(ob) not in self.ob_leg_extreme:
                # just created: right after a break, choch_level = the swing the break started from
                self.ob_leg_extreme[id(ob)] = entry_structure.choch_level
        for candle in candles.get(self.entry_tf, []):
            self.track_atr(candle)
            self.spreads.append(float(getattr(candle, "spread", 0) or 0))

        for tf in self.target_ob_tfs:
            if changed[tf]:
                for ob in changed[tf]:
                    if id(ob) not in self.live_ids:
                        self.live_ids.add(id(ob))
                        self.live_obs[tf].append(ob)
                self.live_obs[tf] = [ob for ob in self.live_obs[tf]
                                     if ob.status not in (OrderBlockStatus.INVALID, OrderBlockStatus.SPENT)]

        # keep a short list of tapped POIs instead of searching every OB ever made
        for tf in self.poi_tfs:
            self.active_pois[tf] += [ob for ob in changed[tf]
                                     if ob.status == OrderBlockStatus.TAPPED and id(ob) not in self.early_tapped]
            for ob in changed[tf]:
                if ob.status in (OrderBlockStatus.VALID, OrderBlockStatus.SWEPT):
                    self.watching[tf].setdefault(id(ob), (ob, ob.status == OrderBlockStatus.SWEPT))
            # the tap is seen on the entry TF, not only when the higher-TF candle closes
            for candle in (candles.get(self.entry_tf, []) if self.early_taps else []):
                for key, (ob, swept) in list(self.watching[tf].items()):
                    if ob.status not in (OrderBlockStatus.VALID, OrderBlockStatus.SWEPT):
                        del self.watching[tf][key]          # the higher-TF close already decided it
                        continue
                    swept, tapped, broken = early_tap_step(ob, candle, swept or ob.status == OrderBlockStatus.SWEPT)
                    if broken:
                        del self.watching[tf][key]
                    elif tapped:
                        del self.watching[tf][key]
                        self.early_tapped.add(key)
                        self.active_pois[tf].append(ob)
                    else:
                        self.watching[tf][key] = (ob, swept)
            self.active_pois[tf] = [ob for ob in self.active_pois[tf]
                                    if ob.status == OrderBlockStatus.TAPPED
                                    or (id(ob) in self.early_tapped
                                        and ob.status not in (OrderBlockStatus.INVALID, OrderBlockStatus.SPENT))]

        # top → bottom through the chain
        bias = self.detectors[self.levels[0].htf].structure.trend
        self.level_inputs = []
        for level in self.levels:
            self.level_inputs.append(bias)
            bias = level.update(bias, changed[level.htf], breaks[level.ltf])
        self.bias = bias

        new = []
        # running campaigns: end them once the target is taken, else look for continuation entries
        for candle in candles.get(self.entry_tf, []):
            self.campaigns = [c for c in self.campaigns if not c.is_over(candle, bias)]
        if self.continuations and self.atr is not None:
            for campaign in self.campaigns:
                for ob in changed[self.entry_tf]:
                    if ob.status != OrderBlockStatus.VALID:
                        continue
                    setup = continuation_setup(now, campaign, ob, self.ob_leg_extreme[id(ob)],
                                               sl_buffer=self.sl_buffer_atr * self.atr, atr=self.atr,
                                               spread=self.spread_space, min_rr=self.min_rr)
                    if setup is not None:
                        new.append(setup)

        # CHoCHs waiting for an inducement: drop the dead ones, place a limit once a block is ready
        for candle in candles.get(self.entry_tf, []):
            self.pending = [p for p in self.pending if not p.is_over(candle, bias)]
        for pending in [p for p in self.pending if p.ready()]:
            self.pending.remove(pending)
            setup = pending_entry_setup(now, pending, sl_buffer=self.sl_buffer_atr * (self.atr or 0.0), atr=self.atr or 0.0,
                                        spread=self.spread_space, min_rr=self.min_rr)
            if setup is not None:
                self.start_campaign(setup, now)
                new.append(setup)

        if bias is not None:
            for brk in breaks[self.entry_tf]:
                if brk.kind != BreakKind.CHOCH or brk.direction != bias:
                    continue
                if self.entry_mode == "immediate":
                    setup = self.try_setup(now, brk, changed[self.entry_tf])
                    if setup is not None:
                        self.used_pois.add(id(setup.poi))
                        self.start_campaign(setup, now)
                        new.append(setup)
                else:
                    pending = self.try_pending(now, brk, changed[self.entry_tf])
                    if pending is not None:
                        self.used_pois.add(id(pending.poi))
                        self.pending.append(pending)
        for setup in new:
            setup.context = self.snapshot()
        self.setups += new
        return new

    def snapshot(self) -> dict:
        """The bot's view right now: structure per TF and the state of each level of the chain."""
        def plain(ob):
            return None if ob is None else dict(direction=str(ob.direction), kind=str(ob.kind), low=ob.low, high=ob.high,
                                                time=ob.time, status=str(ob.status))
        levels = []
        for level, bias_in in zip(self.levels, getattr(self, "level_inputs", [None] * len(self.levels))):
            out = level.flipped_to if level.state == LevelState.FLIPPED else (None if level.state == LevelState.AT_OPPOSING_OB else bias_in)
            levels.append(dict(htf=level.htf, ltf=level.ltf, state=str(level.state), bias_in=None if bias_in is None else str(bias_in),
                               bias_out=None if out is None else str(out), opposing_ob=plain(level.opposing_ob)))
        structure = {tf: dict(trend=None if d.structure.trend is None else str(d.structure.trend),
                              bos_level=d.structure.bos_level, choch_level=d.structure.choch_level)
                     for tf, d in self.detectors.items()}
        live = (OrderBlockStatus.VALID, OrderBlockStatus.SWEPT, OrderBlockStatus.TAPPED)
        pois = {tf: [plain(ob) for ob in self.detectors[tf].order_blocks if ob.status in live][-CONTEXT_POIS_PER_TF:]
                for tf in CONTEXT_POI_TFS if tf in self.detectors}
        return dict(bias=None if self.bias is None else str(self.bias), levels=levels, structure=structure, pois=pois)

    def start_campaign(self, setup : Setup, now):
        self.campaigns.append(Campaign(setup.direction, setup.tp, setup.poi_tf, setup.poi, now,
                                       invalidation=setup.swing_sl))

    def try_pending(self, now, brk : Break, entry_changed : list[OrderBlock]) -> PendingEntry | None:
        candidates = [ob for ob in entry_changed if ob.bos_time == brk.time and ob.direction == brk.direction
                      and (self.entry_blocks == "nearest" or ob.kind == BreakKind.CHOCH)]
        if not candidates or self.atr is None:
            return None
        leg_extreme = self.detectors[self.entry_tf].structure.choch_level
        for tf in self.poi_tfs:
            for poi in reversed(self.active_pois[tf]):
                if poi.direction == brk.direction and id(poi) not in self.used_pois and poi.low <= leg_extreme <= poi.high:
                    target = pick_target(poi, brk.direction, self.target_mode, self.detectors[self.target_tf].structure,
                                         self.live_obs, self.prev_close)
                    if target is None:
                        return None
                    return PendingEntry(brk.direction, tf, poi, candidates, leg_extreme, target, now)
        return None

    @property
    def spread_space(self) -> float:
        if not self.point or not self.spreads:
            return 0.0
        return sum(self.spreads) / len(self.spreads) * self.point * self.spread_mult

    def track_atr(self, candle):
        true_range = candle.high - candle.low
        if self.prev_close is not None:
            true_range = max(true_range, abs(candle.high - self.prev_close), abs(candle.low - self.prev_close))
        self.true_ranges.append(true_range)
        self.prev_close = candle.close

    @property
    def atr(self) -> float | None:
        if len(self.true_ranges) < ATR_PERIOD:
            return None
        return sum(self.true_ranges) / ATR_PERIOD

    def try_setup(self, now, brk : Break, entry_changed : list[OrderBlock]) -> Setup | None:
        blocks = [ob for ob in entry_changed if ob.bos_time == brk.time and ob.direction == brk.direction
                  and (self.entry_blocks == "nearest" or ob.kind == BreakKind.CHOCH)]
        entry_ob = nearest_block(brk.direction, blocks)
        if entry_ob is None or self.atr is None:
            return None
        pois = [(tf, ob) for tf in self.poi_tfs for ob in reversed(self.active_pois[tf])
                if ob.direction == brk.direction and id(ob) not in self.used_pois]
        # right after a CHoCH the entry TF's choch_level = the swing the reversal started from
        leg_extreme = self.detectors[self.entry_tf].structure.choch_level
        poi = next(((tf, ob) for tf, ob in pois if ob.low <= leg_extreme <= ob.high), None)
        if poi is None:
            return None
        target = pick_target(poi[1], brk.direction, self.target_mode, self.detectors[self.target_tf].structure,
                             self.live_obs, self.prev_close)
        return build_setup(now, brk.direction, [poi], leg_extreme, entry_ob, target,
                           sl_buffer=self.sl_buffer_atr * self.atr, atr=self.atr, spread=self.spread_space,
                           min_rr=self.min_rr)
