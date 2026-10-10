"""The multi-timeframe state machine. One instance per symbol; per-symbol switches live in core/symbols.py.

Bias chain: levels (W1→D1, D1→H4, H4→H1) each follow the direction above, pause when price taps an opposing
higher-TF OB, and flip when the lower TF CHoCHs at it. The last level's direction is the trade direction.

Entry: an H4/H1 POI in the trade direction is tapped → an M15 CHoCH whose reversal started inside it →
a limit on an M15 block (right away, or once it has an inducement), SL beyond the reversal's swing,
TP from the target mode, skipped if the RR is below min_rr.
"""
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from .detectors import Break, BreakKind, OrderBlock, OrderBlockDetector, OrderBlockStatus, SwingDirection
from .detectors.structure import StructureDetector

BULLISH = SwingDirection.BULLISH

DEFAULT_LEVELS = [("w1", "d1"), ("d1", "h4"), ("h4", "h1")]
DEFAULT_POI_TFS = ["h4", "h1"]
DEFAULT_ENTRY_TF = "m15"
DEFAULT_ENTRY_MODE = "immediate"        # "immediate" | "inducement"
DEFAULT_ENTRY_BLOCKS = "choch"          # "choch" | "nearest" (CHoCH OB or a breaker/mitigation block)
DEFAULT_TARGET_MODE = "liquidity"       # "liquidity" (next swing on target_tf) | "poi_swing" | "htf_ob"
DEFAULT_TARGET_TF = "h1"
DEFAULT_TARGET_OB_TFS = ["h1", "h4"]
DEFAULT_MIN_RR = 0.0
DEFAULT_SL_BUFFER_ATR = 0.0
DEFAULT_CONTINUATIONS = False
DEFAULT_BREAKERS_AND_MITIGATION = False
DEFAULT_SINGLE_USE = False
DEFAULT_EARLY_TAPS = False

ATR_PERIOD = 14
SPREAD_CANDLES = 96
CONTEXT_POI_TFS = ["d1", "h4", "h1"]
CONTEXT_POIS_PER_TF = 8

LIVE = (OrderBlockStatus.VALID, OrderBlockStatus.SWEPT, OrderBlockStatus.TAPPED)
DEAD = (OrderBlockStatus.INVALID, OrderBlockStatus.SPENT)
WAITING_FOR_TAP = (OrderBlockStatus.VALID, OrderBlockStatus.SWEPT)


# --- the bias chain ----------------------------------------------------------------------------------------

class LevelState(StrEnum):
    FOLLOWING = "FOLLOWING"
    AT_OPPOSING_OB = "AT_OPPOSING_OB"
    FLIPPED = "FLIPPED"


class Level:
    """One step of the chain: follows `bias_in`, pauses at an opposing HTF OB, flips on a lower-TF CHoCH."""

    def __init__(self, htf : str, ltf : str):
        self.htf = htf
        self.ltf = ltf
        self.reset()

    def reset(self):
        self.state = LevelState.FOLLOWING
        self.opposing_ob : OrderBlock | None = None
        self.flipped_to : SwingDirection | None = None

    def update(self, bias_in : SwingDirection | None, htf_obs : list[OrderBlock], ltf_breaks : list[Break]) -> SwingDirection | None:
        """Returns this level's direction, or None = paused / unknown."""
        if bias_in is None:
            self.reset()
            return None

        if self.state == LevelState.FOLLOWING:
            self.opposing_ob = next((ob for ob in htf_obs
                                     if ob.status == OrderBlockStatus.TAPPED and ob.direction != bias_in), None)
            if self.opposing_ob is not None:
                self.state = LevelState.AT_OPPOSING_OB

        if self.state == LevelState.AT_OPPOSING_OB:
            if self.opposing_ob.status == OrderBlockStatus.INVALID:
                self.reset()
            elif has_choch(ltf_breaks, self.opposing_ob.direction):
                self.state = LevelState.FLIPPED
                self.flipped_to = self.opposing_ob.direction
                self.opposing_ob = None
                return self.flipped_to
            else:
                return None

        if self.state == LevelState.FLIPPED:
            if bias_in == self.flipped_to or has_choch(ltf_breaks, bias_in):
                self.reset()
            else:
                return self.flipped_to

        return bias_in


def has_choch(breaks : list[Break], direction : SwingDirection) -> bool:
    return any(b.kind == BreakKind.CHOCH and b.direction == direction for b in breaks)


# --- trades ------------------------------------------------------------------------------------------------

@dataclass
class Setup:
    time : pd.Timestamp             # close time of the M15 candle that confirmed it
    direction : SwingDirection
    entry : float
    sl : float                      # swing_sl pushed back by the buffer and spread
    tp : float
    poi_tf : str
    poi : OrderBlock
    entry_ob : OrderBlock
    swing_sl : float = 0.0          # the SL right on the swing
    atr : float = 0.0
    kind : str = "first"            # "first" | "continuation"
    context : dict | None = field(default=None, compare=False, repr=False)


@dataclass
class Campaign:
    """Continuation entries off one POI. Over once the target is taken, the POI breaks, price takes out the
    first setup's swing, or the direction changes."""
    direction : SwingDirection
    target : float
    poi_tf : str
    poi : OrderBlock
    start : pd.Timestamp
    invalidation : float | None = None

    def is_over(self, candle, bias : SwingDirection | None) -> bool:
        if bias != self.direction or self.poi.status == OrderBlockStatus.INVALID:
            return True
        if self.direction == BULLISH:
            broken = self.invalidation is not None and candle.low < self.invalidation
            return broken or candle.high >= self.target
        broken = self.invalidation is not None and candle.high > self.invalidation
        return broken or candle.low <= self.target


@dataclass
class PendingEntry:
    """An M15 CHoCH out of a POI, waiting for one of its M15 blocks to get an inducement."""
    direction : SwingDirection
    poi_tf : str
    poi : OrderBlock
    candidates : list[OrderBlock]
    leg_extreme : float
    target : float
    start : pd.Timestamp

    def is_over(self, candle, bias : SwingDirection | None) -> bool:
        if bias != self.direction:
            return True
        if all(c.status == OrderBlockStatus.INVALID for c in self.candidates):
            return True
        return reached(self.direction, candle, self.target)

    def ready(self) -> list[OrderBlock]:
        return [c for c in self.candidates if c.inducement is not None and c.status != OrderBlockStatus.INVALID]


def reached(direction : SwingDirection, candle, price : float) -> bool:
    return candle.high >= price if direction == BULLISH else candle.low <= price


# --- prices ------------------------------------------------------------------------------------------------

def stop_level(direction : SwingDirection, block : OrderBlock, leg_extreme : float) -> float:
    """Beyond the swing the reversal started from, or the block, whichever is further."""
    if direction == BULLISH:
        return min(leg_extreme, block.low)
    return max(leg_extreme, block.high)


def order_prices(direction : SwingDirection, block : OrderBlock, leg_extreme : float,
                 sl_buffer : float = 0.0, spread : float = 0.0) -> tuple[float, float, float]:
    """(entry, swing_sl, sl): the entry moves toward price by the spread, the SL away by the spread + buffer."""
    swing_sl = stop_level(direction, block, leg_extreme)
    if direction == BULLISH:
        return block.high + spread, swing_sl, swing_sl - sl_buffer - spread
    return block.low - spread, swing_sl, swing_sl + sl_buffer + spread


def prices_valid(direction : SwingDirection, sl : float, entry : float, tp : float) -> bool:
    return sl < entry < tp if direction == BULLISH else tp < entry < sl


def rr_ok(entry : float, sl : float, tp : float, min_rr : float) -> bool:
    # tiny tolerance so a price-exact 1.5:1 isn't lost to float rounding (1.1015 - 1.1000 = 0.00149999...)
    return min_rr <= 0 or abs(tp - entry) >= min_rr * abs(entry - sl) * (1 - 1e-9)


def make_setup(time, direction, block, leg_extreme, target, poi_tf, poi, sl_buffer, atr, spread, min_rr,
               kind="first") -> Setup | None:
    entry, swing_sl, sl = order_prices(direction, block, leg_extreme, sl_buffer, spread)
    if not prices_valid(direction, sl, entry, target) or not rr_ok(entry, sl, target, min_rr):
        return None
    return Setup(time, direction, entry, sl, target, poi_tf, poi, block, swing_sl, atr, kind=kind)


def nearest_block(direction : SwingDirection, blocks : list[OrderBlock]) -> OrderBlock | None:
    """The block price reaches first on the way back: the highest buy zone, or the lowest sell zone."""
    if not blocks:
        return None
    if direction == BULLISH:
        return max(blocks, key=lambda b: b.high)
    return min(blocks, key=lambda b: b.low)


def build_setup(time, direction : SwingDirection, pois : list[tuple[str, OrderBlock]], leg_extreme : float,
                entry_ob : OrderBlock, target : float | None, sl_buffer : float = 0.0, atr : float = 0.0,
                spread : float = 0.0, min_rr : float = 0.0) -> Setup | None:
    """A limit at `entry_ob`, from the first POI whose zone contains the reversal's start (`leg_extreme`)."""
    if target is None:
        return None
    for tf, poi in pois:
        if poi.low <= leg_extreme <= poi.high:
            return make_setup(time, direction, entry_ob, leg_extreme, target, tf, poi, sl_buffer, atr, spread, min_rr)
    return None


def pending_entry_setup(time, pending : PendingEntry, sl_buffer : float = 0.0, atr : float = 0.0,
                        spread : float = 0.0, min_rr : float = 0.0) -> Setup | None:
    """The limit goes on the block whose inducement appeared first (if several at once: the one price reaches first)."""
    block = nearest_block(pending.direction, pending.ready())
    if block is None:
        return None
    return make_setup(time, pending.direction, block, pending.leg_extreme, pending.target, pending.poi_tf,
                      pending.poi, sl_buffer, atr, spread, min_rr)


def continuation_setup(time, campaign : Campaign, ob : OrderBlock, leg_extreme : float,
                       sl_buffer : float = 0.0, atr : float = 0.0, spread : float = 0.0,
                       min_rr : float = 0.0) -> Setup | None:
    """A limit at an M15 OB that just got its inducement, inside a running campaign."""
    if ob.direction != campaign.direction or ob.time <= campaign.start:
        return None
    return make_setup(time, campaign.direction, ob, leg_extreme, campaign.target, campaign.poi_tf, campaign.poi,
                      sl_buffer, atr, spread, min_rr, kind="continuation")


def early_tap_step(ob : OrderBlock, candle, swept : bool) -> tuple[bool, bool, bool]:
    """One entry-TF candle against a higher-TF OB that already has its inducement. Returns (swept, tapped, broken)."""
    if ob.direction == BULLISH:
        if candle.low < ob.low:
            return swept, False, True
        swept = swept or candle.low < ob.inducement
        return swept, swept and candle.low <= ob.high, False
    if candle.high > ob.high:
        return swept, False, True
    swept = swept or candle.high > ob.inducement
    return swept, swept and candle.high >= ob.low, False


# --- targets -----------------------------------------------------------------------------------------------

def pick_target(poi : OrderBlock, direction : SwingDirection, mode : str, structure : StructureDetector,
                live_obs : dict[str, list[OrderBlock]] | None = None, price : float | None = None) -> float | None:
    if mode == "poi_swing":
        return poi.swing_target
    if mode == "htf_ob":
        return nearest_opposing_ob(live_obs or {}, direction, price)
    return liquidity_target(structure, direction)


def nearest_opposing_ob(live_obs : dict[str, list[OrderBlock]], direction : SwingDirection,
                        price : float | None) -> float | None:
    """Buy → the lowest bearish-OB low above price. Sell → the highest bullish-OB high below price."""
    if price is None:
        return None
    obs = [ob for tf_obs in live_obs.values() for ob in tf_obs]
    if direction == BULLISH:
        edges = [ob.low for ob in obs if ob.direction == SwingDirection.BEARISH and ob.low > price]
        return min(edges) if edges else None
    edges = [ob.high for ob in obs if ob.direction == BULLISH and ob.high < price]
    return max(edges) if edges else None


def liquidity_target(structure : StructureDetector, direction : SwingDirection) -> float | None:
    """The next swing high (bullish) / swing low (bearish) on this TF."""
    if structure.trend is None:
        return None
    if structure.trend != direction:
        return structure.choch_level
    return structure.bos_level if structure.bos_level is not None else structure.choch_extreme


# --- the machine -------------------------------------------------------------------------------------------

class StateMachine:

    def __init__(self, levels=DEFAULT_LEVELS, poi_tfs=DEFAULT_POI_TFS, entry_tf=DEFAULT_ENTRY_TF,
                 target_tf=DEFAULT_TARGET_TF, sl_buffer_atr=DEFAULT_SL_BUFFER_ATR, continuations=DEFAULT_CONTINUATIONS,
                 entry_mode=DEFAULT_ENTRY_MODE, point : float = 0.0, spread_mult : float = 1.0,
                 target_mode=DEFAULT_TARGET_MODE, breakers_and_mitigation=DEFAULT_BREAKERS_AND_MITIGATION,
                 single_use=DEFAULT_SINGLE_USE, early_taps=DEFAULT_EARLY_TAPS, entry_blocks=DEFAULT_ENTRY_BLOCKS,
                 target_ob_tfs=DEFAULT_TARGET_OB_TFS, min_rr=DEFAULT_MIN_RR):
        """point = the symbol's price step (MT5 symbol_info().point); spreads are in points. 0 → no spread space."""
        self.levels = [Level(htf, ltf) for htf, ltf in levels]
        self.poi_tfs = poi_tfs
        self.entry_tf = entry_tf
        self.target_tf = target_tf
        self.target_ob_tfs = target_ob_tfs
        self.sl_buffer_atr = sl_buffer_atr
        self.continuations = continuations
        self.entry_mode = entry_mode
        self.entry_blocks = entry_blocks
        self.target_mode = target_mode
        self.early_taps = early_taps
        self.min_rr = min_rr
        self.point = point
        self.spread_mult = spread_mult

        tfs = {tf for pair in levels for tf in pair} | set(poi_tfs) | {entry_tf, target_tf} | set(target_ob_tfs)
        self.detectors = {tf: OrderBlockDetector(breakers_and_mitigation=breakers_and_mitigation,
                                                 single_use=single_use) for tf in tfs}
        self.bias : SwingDirection | None = None
        self.level_inputs : list[SwingDirection | None] = [None] * len(self.levels)

        self.true_ranges : deque[float] = deque(maxlen=ATR_PERIOD)
        self.spreads : deque[float] = deque(maxlen=SPREAD_CANDLES)
        self.prev_close : float | None = None

        self.active_pois : dict[str, list[OrderBlock]] = {tf: [] for tf in poi_tfs}      # tapped, not dead
        self.watching : dict[str, dict[int, tuple[OrderBlock, bool]]] = {tf: {} for tf in poi_tfs}
        self.early_tapped : set[int] = set()
        self.used_pois : set[int] = set()
        self.live_obs : dict[str, list[OrderBlock]] = {tf: [] for tf in target_ob_tfs}
        self.live_ids : set[int] = set()
        self.ob_leg_extreme : dict[int, float] = {}

        self.pending : list[PendingEntry] = []
        self.campaigns : list[Campaign] = []
        self.setups : list[Setup] = []

    # --- one tick ---

    def update(self, now, candles : dict[str, list]) -> list[Setup]:
        """One tick of feed(): `candles` maps TF → the candles that closed at `now`. Returns the new setups."""
        entry_candles = candles.get(self.entry_tf, [])
        changed, breaks = self.feed_detectors(candles)
        self.remember_leg_extremes(changed[self.entry_tf])
        self.track_entry_candles(entry_candles)
        self.track_live_obs(changed)
        self.track_pois(changed, entry_candles)
        bias = self.run_chain(changed, breaks)

        new = self.continuation_setups(now, changed[self.entry_tf], entry_candles, bias)
        new += self.ready_pending_setups(now, entry_candles, bias)
        new += self.new_choch_setups(now, breaks[self.entry_tf], changed[self.entry_tf], bias)
        for setup in new:
            setup.context = self.snapshot()
        self.setups += new
        return new

    def feed_detectors(self, candles):
        changed, breaks = {}, {}
        for tf, detector in self.detectors.items():
            changed[tf], breaks[tf] = [], []
            for candle in candles.get(tf, []):
                n = len(detector.structure.breaks)
                changed[tf] += detector.update(candle)
                breaks[tf] += detector.structure.breaks[n:]
        return changed, breaks

    def remember_leg_extremes(self, entry_changed):
        choch_level = self.detectors[self.entry_tf].structure.choch_level
        for ob in entry_changed:
            self.ob_leg_extreme.setdefault(id(ob), choch_level)

    def track_entry_candles(self, entry_candles):
        for candle in entry_candles:
            self.track_atr(candle)
            self.spreads.append(float(getattr(candle, "spread", 0) or 0))

    def track_live_obs(self, changed):
        for tf in self.target_ob_tfs:
            if not changed[tf]:
                continue
            for ob in changed[tf]:
                if id(ob) not in self.live_ids:
                    self.live_ids.add(id(ob))
                    self.live_obs[tf].append(ob)
            self.live_obs[tf] = [ob for ob in self.live_obs[tf] if ob.status not in DEAD]

    def track_pois(self, changed, entry_candles):
        for tf in self.poi_tfs:
            self.active_pois[tf] += [ob for ob in changed[tf]
                                     if ob.status == OrderBlockStatus.TAPPED and id(ob) not in self.early_tapped]
            for ob in changed[tf]:
                if ob.status in WAITING_FOR_TAP:
                    self.watching[tf].setdefault(id(ob), (ob, ob.status == OrderBlockStatus.SWEPT))
            if self.early_taps:
                for candle in entry_candles:
                    self.check_early_taps(tf, candle)
            self.active_pois[tf] = [ob for ob in self.active_pois[tf]
                                    if ob.status == OrderBlockStatus.TAPPED
                                    or (id(ob) in self.early_tapped and ob.status not in DEAD)]

    def check_early_taps(self, tf, candle):
        """See an H1/H4 tap on the entry TF instead of waiting for the higher-TF candle to close."""
        for key, (ob, swept) in list(self.watching[tf].items()):
            if ob.status not in WAITING_FOR_TAP:
                del self.watching[tf][key]
                continue
            swept, tapped, broken = early_tap_step(ob, candle, swept or ob.status == OrderBlockStatus.SWEPT)
            if broken or tapped:
                del self.watching[tf][key]
            else:
                self.watching[tf][key] = (ob, swept)
            if tapped:
                self.early_tapped.add(key)
                self.active_pois[tf].append(ob)

    def run_chain(self, changed, breaks):
        bias = self.detectors[self.levels[0].htf].structure.trend
        self.level_inputs = []
        for level in self.levels:
            self.level_inputs.append(bias)
            bias = level.update(bias, changed[level.htf], breaks[level.ltf])
        self.bias = bias
        return bias

    def continuation_setups(self, now, entry_changed, entry_candles, bias):
        for candle in entry_candles:
            self.campaigns = [c for c in self.campaigns if not c.is_over(candle, bias)]
        if not self.continuations or self.atr is None:
            return []
        new = []
        for campaign in self.campaigns:
            for ob in entry_changed:
                if ob.status == OrderBlockStatus.VALID:
                    setup = continuation_setup(now, campaign, ob, self.ob_leg_extreme[id(ob)],
                                               sl_buffer=self.sl_buffer_atr * self.atr, atr=self.atr,
                                               spread=self.spread_space, min_rr=self.min_rr)
                    if setup is not None:
                        new.append(setup)
        return new

    def ready_pending_setups(self, now, entry_candles, bias):
        for candle in entry_candles:
            self.pending = [p for p in self.pending if not p.is_over(candle, bias)]
        new = []
        for pending in [p for p in self.pending if p.ready()]:
            self.pending.remove(pending)
            setup = pending_entry_setup(now, pending, sl_buffer=self.sl_buffer_atr * (self.atr or 0.0),
                                        atr=self.atr or 0.0, spread=self.spread_space, min_rr=self.min_rr)
            if setup is not None:
                self.start_campaign(setup, now)
                new.append(setup)
        return new

    def new_choch_setups(self, now, entry_breaks, entry_changed, bias):
        if bias is None:
            return []
        new = []
        for brk in entry_breaks:
            if brk.kind != BreakKind.CHOCH or brk.direction != bias:
                continue
            if self.entry_mode == "immediate":
                setup = self.try_setup(now, brk, entry_changed)
                if setup is not None:
                    self.used_pois.add(id(setup.poi))
                    self.start_campaign(setup, now)
                    new.append(setup)
            else:
                pending = self.try_pending(now, brk, entry_changed)
                if pending is not None:
                    self.used_pois.add(id(pending.poi))
                    self.pending.append(pending)
        return new

    # --- building entries ---

    def entry_candidates(self, brk, entry_changed):
        return [ob for ob in entry_changed if ob.bos_time == brk.time and ob.direction == brk.direction
                and (self.entry_blocks == "nearest" or ob.kind == BreakKind.CHOCH)]

    def find_poi(self, direction, leg_extreme):
        """The newest unused tapped POI in `direction` whose zone contains the reversal's start, as (tf, poi)."""
        for tf in self.poi_tfs:
            for poi in reversed(self.active_pois[tf]):
                if poi.direction == direction and id(poi) not in self.used_pois and poi.low <= leg_extreme <= poi.high:
                    return tf, poi
        return None

    def target_for(self, poi, direction):
        return pick_target(poi, direction, self.target_mode, self.detectors[self.target_tf].structure,
                           self.live_obs, self.prev_close)

    def try_setup(self, now, brk : Break, entry_changed : list[OrderBlock]) -> Setup | None:
        entry_ob = nearest_block(brk.direction, self.entry_candidates(brk, entry_changed))
        if entry_ob is None or self.atr is None:
            return None
        leg_extreme = self.detectors[self.entry_tf].structure.choch_level
        found = self.find_poi(brk.direction, leg_extreme)
        if found is None:
            return None
        target = self.target_for(found[1], brk.direction)
        return build_setup(now, brk.direction, [found], leg_extreme, entry_ob, target,
                           sl_buffer=self.sl_buffer_atr * self.atr, atr=self.atr, spread=self.spread_space,
                           min_rr=self.min_rr)

    def try_pending(self, now, brk : Break, entry_changed : list[OrderBlock]) -> PendingEntry | None:
        candidates = self.entry_candidates(brk, entry_changed)
        if not candidates or self.atr is None:
            return None
        leg_extreme = self.detectors[self.entry_tf].structure.choch_level
        found = self.find_poi(brk.direction, leg_extreme)
        if found is None:
            return None
        tf, poi = found
        target = self.target_for(poi, brk.direction)
        if target is None:
            return None
        return PendingEntry(brk.direction, tf, poi, candidates, leg_extreme, target, now)

    def start_campaign(self, setup : Setup, now):
        self.campaigns.append(Campaign(setup.direction, setup.tp, setup.poi_tf, setup.poi, now,
                                       invalidation=setup.swing_sl))

    # --- ATR, spread, snapshot ---

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

    @property
    def spread_space(self) -> float:
        if not self.point or not self.spreads:
            return 0.0
        return sum(self.spreads) / len(self.spreads) * self.point * self.spread_mult

    def snapshot(self) -> dict:
        """The bot's view right now (used by the context chart): structure per TF, the chain, live POIs."""
        def plain(ob):
            if ob is None:
                return None
            return dict(direction=str(ob.direction), kind=str(ob.kind), low=ob.low, high=ob.high,
                        time=ob.time, status=str(ob.status))

        def text(value):
            return None if value is None else str(value)

        levels = []
        for level, bias_in in zip(self.levels, self.level_inputs):
            if level.state == LevelState.FLIPPED:
                out = level.flipped_to
            elif level.state == LevelState.AT_OPPOSING_OB:
                out = None
            else:
                out = bias_in
            levels.append(dict(htf=level.htf, ltf=level.ltf, state=str(level.state), bias_in=text(bias_in),
                               bias_out=text(out), opposing_ob=plain(level.opposing_ob)))
        structure = {tf: dict(trend=text(d.structure.trend), bos_level=d.structure.bos_level,
                              choch_level=d.structure.choch_level) for tf, d in self.detectors.items()}
        pois = {tf: [plain(ob) for ob in self.detectors[tf].order_blocks if ob.status in LIVE][-CONTEXT_POIS_PER_TF:]
                for tf in CONTEXT_POI_TFS if tf in self.detectors}
        return dict(bias=text(self.bias), levels=levels, structure=structure, pois=pois)
