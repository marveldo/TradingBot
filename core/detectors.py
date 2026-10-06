from enum import  StrEnum
from dataclasses import dataclass
import pandas as pd


class SwingDirection(StrEnum):
    BEARISH = "bearish"
    BULLISH = "bullish"
class SwingStates(StrEnum):
    
    WAITING = "WAITING"
    MOVING = "MOVING"
    PULL_BACK = "PULL_BACK"

class BreakKind(StrEnum):
    CHOCH = "CHoCH"
    BOS = "BOS"

@dataclass
class Swing :
    direction: SwingDirection 
    high: float 
    high_time: pd.Timestamp
    low: float 
    low_time: pd.Timestamp 
    confirmed_time: pd.Timestamp 

class SwingDetector :
    def __init__(self):
        self.bullish = BullishSwingDetector()
        self.bearish = BearishSwingDetector()

        self.swings = []

    def update(self , candle : tuple):
        new = self.bullish.update(candle) + self.bearish.update(candle)
        self.swings.extend(new)
        return new
class BullishSwingDetector:

    def __init__(self):
        self.state : SwingStates = SwingStates.WAITING
        self.move_high = None
        self.move_high_time = None
        self.move_start = None
        self.pb_high = None
        self.pb_low = None
        self.pb_low_time = None
   
    def update(self, candle : tuple) -> list[Swing] :
        match self.state :
            case SwingStates.WAITING:
                return self.handle_waiting(candle)
            case SwingStates.MOVING:
                return self.handle_moving(candle)
            case SwingStates.PULL_BACK:
                return self.handle_pull_back(candle)
        
    def handle_waiting(self, candle : tuple) -> list[Swing]:
        if candle.open < candle.close :
           self.state = SwingStates.MOVING
           self.move_high = candle.high
           self.move_high_time = candle.time
           self.move_start = candle.low
        return []
    
    def handle_moving(self, candle : tuple) -> list[Swing]:
        if candle.high  > self.move_high :
            self.move_high = candle.high 
            self.move_high_time = candle.time
        if candle.open > candle.close :
            if candle.low < self.move_start:      
                self.state = SwingStates.WAITING 
                return []     
            self.pb_high = candle.high
            self.pb_low = candle.low
            self.pb_low_time = candle.time
            self.state = SwingStates.PULL_BACK
        return []
    def handle_pull_back(self, candle : tuple) -> list[Swing]:

        if candle.high > self.pb_high :
            swing = Swing(SwingDirection.BULLISH, self.move_high, self.move_high_time, self.pb_low, self.pb_low_time, candle.time)
            self.move_high = candle.high
            self.move_high_time = candle.time
            self.move_start = self.pb_low
            self.state = SwingStates.MOVING
            return [swing]
        else :
            if candle.low < self.pb_low :
                if candle.low < self.move_start:        
                    self.state = SwingStates.WAITING
                    return self.handle_waiting(candle) 
                self.pb_low = candle.low
                self.pb_low_time = candle.time
            return []

class BearishSwingDetector:

    def __init__(self):
        self.state : SwingStates = SwingStates.WAITING
        self.move_low = None
        self.move_low_time = None
        self.move_start = None
        self.pb_high = None
        self.pb_low = None
        self.pb_high_time = None
   
    def update(self, candle : tuple) -> list[Swing] :
        match self.state :
            case SwingStates.WAITING:
                return self.handle_waiting(candle)
            case SwingStates.MOVING:
                return self.handle_moving(candle)
            case SwingStates.PULL_BACK:
                return self.handle_pull_back(candle)
        
    def handle_waiting(self, candle : tuple) -> list[Swing]:
        if candle.open > candle.close :
           self.state = SwingStates.MOVING
           self.move_low = candle.low
           self.move_low_time = candle.time
           self.move_start = candle.high
        return []
    
    def handle_moving(self, candle : tuple) -> list[Swing]:
        if candle.low  <  self.move_low :
            self.move_low = candle.low
            self.move_low_time = candle.time
        if candle.open < candle.close :
            if candle.high > self.move_start:      
                self.state = SwingStates.WAITING 
                return []     
            self.pb_high = candle.high
            self.pb_low = candle.low
            self.pb_high_time = candle.time
            self.state = SwingStates.PULL_BACK
        return []
    def handle_pull_back(self, candle : tuple) -> list[Swing]:

        if candle.low < self.pb_low :
            swing = Swing(SwingDirection.BEARISH, self.pb_high, self.pb_high_time, self.move_low, self.move_low_time, candle.time)
            self.move_low = candle.low
            self.move_low_time = candle.time
            self.move_start = self.pb_high
            self.state = SwingStates.MOVING
            return [swing]
        else :
            if candle.high > self.pb_high:
                if candle.high  > self.move_start:        
                    self.state = SwingStates.WAITING
                    return self.handle_waiting(candle) 
                self.pb_high= candle.high
                self.pb_high_time = candle.time
            return []



@dataclass
class Break :
    direction : str 
    kind : BreakKind 
    level : float
    time : pd.Timestamp
    ob_time : pd.Timestamp

class StructureDetector :

    def __init__(self):
       self.trend : SwingDirection = None
       self.bos_level : float | None = None
       self.choch_level : float | None = None
       self.breaks = []
       self.prev : tuple | None = None
       self.swing_detector = SwingDetector()

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
        current_breaks = self.compare_and_set_breaks(candle)
        self.breaks.extend(current_breaks)
        self.prev = candle
        return current_breaks
        
    def set_trends_and_levels(self, trend : SwingDirection , bos_level : float , coch_level : float, swing : Swing ):
        if self.trend is None :
           self.trend = trend
           self.bos_level = bos_level
           self.choch_level = coch_level
        elif swing.direction == self.trend and self.bos_level is None:
            self.bos_level = bos_level

    def apply_break(self, trend : SwingDirection , bos_level : float , coch_level : float):
        self.trend = trend
        self.choch_level = coch_level
        self.bos_level = bos_level


    def compare_and_set_breaks(self , candle):
        if self.trend is None:
            return []
        bos_is_set = self.bos_level is not None
        choch_is_set = self.choch_level is not None

        if self.trend == SwingDirection.BULLISH:
            if choch_is_set and candle.low < self.choch_level:
                return self.try_break(candle, BreakKind.CHOCH, self.choch_level, SwingDirection.BEARISH, self.prev.high)
            elif bos_is_set and candle.high > self.bos_level:
                return self.try_break(candle, BreakKind.BOS, self.bos_level, SwingDirection.BULLISH, self.prev.low)
        else:
            if choch_is_set and candle.high > self.choch_level:
                return self.try_break(candle, BreakKind.CHOCH, self.choch_level, SwingDirection.BULLISH, self.prev.low)
            elif bos_is_set and candle.low < self.bos_level:
                return self.try_break(candle, BreakKind.BOS, self.bos_level, SwingDirection.BEARISH, self.prev.high)
        return []

    def try_break(self, candle, kind : BreakKind, level : float, new_trend : SwingDirection, new_choch_level : float):
        new_break = Break(new_trend, kind, level, candle.time, self.prev.time)
        self.apply_break(new_trend, None, new_choch_level)
        return [new_break]
                   
                   
                   
                  
   
