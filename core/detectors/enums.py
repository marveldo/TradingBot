from enum import StrEnum


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

class OrderBlockStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    SWEPT = "swept"
    TAPPED = "tapped"
    SPENT = "spent"         # tapped, then price reacted away past the inducement → single-use, no longer a POI
    INVALID = "invalid"

class BlockKind(StrEnum):
    """A broken OB that flipped direction (see OrderBlockDetector)."""
    BREAKER = "breaker"         # price swept a high (bullish OB) / low (bearish OB) first, then broke the OB
    MITIGATION = "mitigation"   # broke the OB without sweeping that high/low first
