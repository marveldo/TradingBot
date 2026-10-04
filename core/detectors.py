from enum import Enum
from dataclasses import dataclass
import pandas as pd

class SwingStates(Enum):
    
    WAITING = "WAITING"
    MOVING = "MOVING"
    PULL_BACK = "PULL_BACK"

@dataclass
class Swing :
    direction: str | None
    high: float | None
    high_time: pd.Timestamp | None
    low: float | None
    low_time: pd.Timestamp | None
    confirmed_time: pd.Timestamp | None

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
            swing = Swing("bullish", self.move_high, self.move_high_time, self.pb_low, self.pb_low_time, candle.time)
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
            swing = Swing("bearish", self.pb_high, self.pb_high_time, self.move_low, self.move_low_time, candle.time)
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



