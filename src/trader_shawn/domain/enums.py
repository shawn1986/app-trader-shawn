from enum import StrEnum


class DecisionAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    HOLD = "hold"


class OptionRight(StrEnum):
    CALL = "call"
    PUT = "put"


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"
