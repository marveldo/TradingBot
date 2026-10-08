from collections import namedtuple
from dataclasses import dataclass, field

import pandas as pd

from .enums import BlockKind, BreakKind, OrderBlockStatus, SwingDirection
from .structure import Break, StructureDetector


# our own copy of the OB candle: pandas' itertuples rows can't be pickled (saved/cached)
OBCandle = namedtuple("OBCandle", "time open high low close")


@dataclass
class OrderBlock:
    direction : SwingDirection
    kind : BreakKind | BlockKind        # BOS / CHoCH = a normal OB, breaker / mitigation = a flipped one
    high : float
    low : float
    time : pd.Timestamp
    bos_time : pd.Timestamp
    status : OrderBlockStatus = OrderBlockStatus.PENDING
    inducement : float | None = None
    inducement_time : pd.Timestamp | None = None
    # the pullback after the break — it becomes the inducement once price continues past it
    pb_high : float | None = None
    pb_low : float | None = None
    pb_time : pd.Timestamp | None = None    # time of the pullback extreme that will be the inducement
    # for breaker vs mitigation: the break candle's high (bullish) / low (bearish), and how far price went past it
    break_extreme : float | None = None
    peak : float | None = None
    broken : bool = False                   # True = killed by price going through the far side (can flip)
    # the extreme price reached after the inducement formed and before it got swept (bullish: highest high,
    # bearish: lowest low) = the liquidity left behind → the TP for trades from this zone
    swing_target : float | None = None
    single_use : bool = True                # False → a tapped zone stays tapped (reusable) instead of becoming spent
    candle : tuple | None = field(default=None, repr=False, compare=False)   # the OB candle (to rebuild a flipped zone)

    def update(self, candle) -> bool:
        """Move this OB forward by one candle. Returns True if its status changed."""
        if self.status == OrderBlockStatus.INVALID:
            return False
        if self.direction == SwingDirection.BULLISH:
            self.peak = candle.high if self.peak is None else max(self.peak, candle.high)
            return self.update_bullish(candle)
        self.peak = candle.low if self.peak is None else min(self.peak, candle.low)
        return self.update_bearish(candle)

    @property
    def swept_before_break(self) -> bool:
        if self.break_extreme is None or self.peak is None:
            return False
        if self.direction == SwingDirection.BULLISH:
            return self.peak > self.break_extreme
        return self.peak < self.break_extreme

    def update_bullish(self, candle) -> bool:
        # checked first, every candle: price broke through the bottom of the OB
        if candle.low < self.low:
            self.status = OrderBlockStatus.INVALID
            self.broken = True
            return True

        match self.status:
            case OrderBlockStatus.PENDING:
                if self.pb_high is None:
                    # waiting for the first red candle to start the pullback
                    if candle.open > candle.close:
                        self.pb_high = candle.high
                        self.pb_low = candle.low
                        self.pb_time = candle.time
                elif candle.high > self.pb_high:
                    # continuation past the pullback → valid, the pullback low is the inducement
                    self.status = OrderBlockStatus.VALID
                    self.inducement = self.pb_low
                    self.inducement_time = self.pb_time
                    self.swing_target = candle.high
                    return True
                elif candle.low < self.pb_low:
                    # pullback going deeper
                    self.pb_low = candle.low
                    self.pb_time = candle.time

                # pullback already came back into the OB before any inducement formed
                if self.pb_low is not None and self.pb_low <= self.high:
                    self.status = OrderBlockStatus.INVALID
                    return True
                return False

            case OrderBlockStatus.VALID:
                self.swing_target = max(self.swing_target, candle.high)
                if candle.low < self.inducement:
                    self.status = OrderBlockStatus.SWEPT
                    if candle.low <= self.high:     # same candle also reaches the OB
                        self.status = OrderBlockStatus.TAPPED
                    return True
                return False

            case OrderBlockStatus.SWEPT:
                if candle.low <= self.high:
                    self.status = OrderBlockStatus.TAPPED
                    return True
                return False

            case OrderBlockStatus.TAPPED:
                # price reacted and left back above the inducement → this zone has been used up
                if self.single_use and candle.high > self.inducement:
                    self.status = OrderBlockStatus.SPENT
                    return True
                return False

        return False    # spent: only a break of the OB low changes it now (→ invalid, and it may flip)

    def update_bearish(self, candle) -> bool:
        # mirror of update_bullish
        if candle.high > self.high:
            self.status = OrderBlockStatus.INVALID
            self.broken = True
            return True

        match self.status:
            case OrderBlockStatus.PENDING:
                if self.pb_low is None:
                    # waiting for the first green candle to start the pullback
                    if candle.open < candle.close:
                        self.pb_high = candle.high
                        self.pb_low = candle.low
                        self.pb_time = candle.time
                elif candle.low < self.pb_low:
                    # continuation past the pullback → valid, the pullback high is the inducement
                    self.status = OrderBlockStatus.VALID
                    self.inducement = self.pb_high
                    self.inducement_time = self.pb_time
                    self.swing_target = candle.low
                    return True
                elif candle.high > self.pb_high:
                    self.pb_high = candle.high
                    self.pb_time = candle.time

                if self.pb_high is not None and self.pb_high >= self.low:
                    self.status = OrderBlockStatus.INVALID
                    return True
                return False

            case OrderBlockStatus.VALID:
                self.swing_target = min(self.swing_target, candle.low)
                if candle.high > self.inducement:
                    self.status = OrderBlockStatus.SWEPT
                    if candle.high >= self.low:
                        self.status = OrderBlockStatus.TAPPED
                    return True
                return False

            case OrderBlockStatus.SWEPT:
                if candle.high >= self.low:
                    self.status = OrderBlockStatus.TAPPED
                    return True
                return False

            case OrderBlockStatus.TAPPED:
                if self.single_use and candle.low < self.inducement:
                    self.status = OrderBlockStatus.SPENT
                    return True
                return False

        return False


class OrderBlockDetector:

    def __init__(self, flip_blocks : bool = True, single_use : bool = True):
        """flip_blocks: broken OBs + a CHoCH → breaker / mitigation blocks. single_use: tapped zones become spent."""
        self.flip_blocks = flip_blocks
        self.single_use = single_use
        self.structure = StructureDetector()
        self.last_red : tuple | None = None      # last red candle BEFORE the current one
        self.last_green : tuple | None = None    # last green candle BEFORE the current one
        self.order_blocks : list[OrderBlock] = []
        self.broken_since_break : list[OrderBlock] = []   # OBs broken through since the last BOS/CHoCH

    def update(self, candle) -> list[OrderBlock]:
        changed = []

        # 1. move the OBs we already have forward with this candle
        for ob in self.order_blocks:
            if ob.update(candle):
                changed.append(ob)
                if ob.broken and isinstance(ob.kind, BreakKind):
                    self.broken_since_break.append(ob)

        # 2. did this candle break structure? → the last opposite-colour candle before it is a new OB
        #    (bullish break → last red candle, bearish break → last green candle)
        for brk in self.structure.update(candle):
            ob = self.make_order_block(brk, candle)
            if ob is not None:
                self.order_blocks.append(ob)
                changed.append(ob)
            # a CHoCH flips the OBs that were broken on the way into it → breaker / mitigation blocks
            if self.flip_blocks and brk.kind == BreakKind.CHOCH:
                for broken in self.broken_since_break:
                    if broken.direction != brk.direction:
                        flipped = self.flip(broken, brk, candle)
                        self.order_blocks.append(flipped)
                        changed.append(flipped)
            self.broken_since_break = []

        # 3. only now does this candle count as "before" for the next call
        if candle.close < candle.open:
            self.last_red = candle
        elif candle.close > candle.open:
            self.last_green = candle
        return changed

    def make_order_block(self, brk : Break, candle) -> OrderBlock | None:
        # body to wick: the wick on the far side of the OB is left out
        if brk.direction == SwingDirection.BULLISH:
            ob_candle = self.last_red
            if ob_candle is None:
                return None
            high = max(ob_candle.open, ob_candle.close)
            low = ob_candle.low
        else:
            ob_candle = self.last_green
            if ob_candle is None:
                return None
            high = ob_candle.high
            low = min(ob_candle.open, ob_candle.close)
        return OrderBlock(brk.direction, brk.kind, high, low, ob_candle.time, candle.time,
                          break_extreme=candle.high if brk.direction == SwingDirection.BULLISH else candle.low,
                          candle=OBCandle(ob_candle.time, ob_candle.open, ob_candle.high, ob_candle.low, ob_candle.close),
                          single_use=self.single_use)

    def flip(self, broken : OrderBlock, brk : Break, candle) -> OrderBlock:
        """Same candle, opposite direction, zone rebuilt body-to-wick for the NEW direction
        (a bearish breaker = body bottom → wick high). Then it needs its own pullback → inducement → sweep → tap."""
        kind = BlockKind.BREAKER if broken.swept_before_break else BlockKind.MITIGATION
        c = broken.candle
        if brk.direction == SwingDirection.BEARISH:
            high, low = c.high, min(c.open, c.close)
        else:
            high, low = max(c.open, c.close), c.low
        return OrderBlock(brk.direction, kind, high, low, broken.time, candle.time, candle=c, single_use=self.single_use)
