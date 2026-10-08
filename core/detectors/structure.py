from dataclasses import dataclass

import pandas as pd

from .enums import BreakKind, SwingDirection
from .swings import Swing, SwingDetector


@dataclass
class Break :
    direction : str
    kind : BreakKind
    level : float
    time : pd.Timestamp


class StructureDetector :

    def __init__(self):
       self.trend : SwingDirection = None
       self.bos_level : float | None = None
       self.choch_level : float | None = None
       self.breaks : list[Break] = []
       self.swing_detector = SwingDetector()
       # the far end of the leg, tracked so that a break can hand it over as the next CHoCH level:
       #   bos_extreme   = lowest low (bullish) / highest high (bearish) since bos_level was set
       #                   → on a BOS it becomes the new choch_level (the swing low/high the move started from)
       #   choch_extreme = highest high (bullish) / lowest low (bearish) since choch_level was set
       #                   → on a CHoCH it becomes the new choch_level (the swing high/low the reversal started from)
       self.bos_extreme : float | None = None
       self.choch_extreme : float | None = None

    def update(self , candle) :
        swings = self.swing_detector.update(candle)
        for swing in swings :
            if swing.direction == SwingDirection.BEARISH:
               self.set_trends_and_levels(
                   SwingDirection.BEARISH,
                   swing.low ,
                   swing.high,
                   swing
               )
            elif swing.direction == SwingDirection.BULLISH :
                self.set_trends_and_levels(
                    SwingDirection.BULLISH,
                    swing.high,
                    swing.low,
                    swing
                )
        self.track_extremes(candle)
        current_breaks = self.compare_and_set_breaks(candle)
        self.breaks.extend(current_breaks)
        return current_breaks

    def set_trends_and_levels(self, trend : SwingDirection , bos_level : float , coch_level : float, swing : Swing ):
        if self.trend is None :
           self.trend = trend
           self.bos_level = bos_level
           self.choch_level = coch_level
           self.bos_extreme = coch_level      # the swing's own low (bullish) / high (bearish)
           self.choch_extreme = None          # filled from this candle on
        elif swing.direction == self.trend and self.bos_level is None:
            self.bos_level = bos_level
            self.bos_extreme = coch_level

    def track_extremes(self, candle):
        if self.trend == SwingDirection.BULLISH:
            far_for_bos, far_for_choch = candle.low, candle.high
            pick_bos, pick_choch = min, max
        elif self.trend == SwingDirection.BEARISH:
            far_for_bos, far_for_choch = candle.high, candle.low
            pick_bos, pick_choch = max, min
        else:
            return
        if self.bos_level is not None:
            self.bos_extreme = far_for_bos if self.bos_extreme is None else pick_bos(self.bos_extreme, far_for_bos)
        if self.choch_level is not None:
            self.choch_extreme = far_for_choch if self.choch_extreme is None else pick_choch(self.choch_extreme, far_for_choch)

    def compare_and_set_breaks(self , candle):
        if self.trend is None:
            return []
        bos_is_set = self.bos_level is not None
        choch_is_set = self.choch_level is not None

        if self.trend == SwingDirection.BULLISH:
            if choch_is_set and candle.low < self.choch_level:
                return self.try_break(candle, BreakKind.CHOCH, self.choch_level, SwingDirection.BEARISH, self.choch_extreme)
            elif bos_is_set and candle.high > self.bos_level:
                return self.try_break(candle, BreakKind.BOS, self.bos_level, SwingDirection.BULLISH, self.bos_extreme)
        else:
            if choch_is_set and candle.high > self.choch_level:
                return self.try_break(candle, BreakKind.CHOCH, self.choch_level, SwingDirection.BULLISH, self.choch_extreme)
            elif bos_is_set and candle.low < self.bos_level:
                return self.try_break(candle, BreakKind.BOS, self.bos_level, SwingDirection.BEARISH, self.bos_extreme)
        return []

    def try_break(self, candle, kind : BreakKind, level : float, new_trend : SwingDirection, new_choch_level : float):
        new_break = Break(new_trend, kind, level, candle.time)
        self.trend = new_trend
        self.choch_level = new_choch_level
        self.bos_level = None
        self.bos_extreme = None
        # start tracking the far end of the new leg from the break candle itself
        self.choch_extreme = candle.high if new_trend == SwingDirection.BULLISH else candle.low
        return [new_break]
