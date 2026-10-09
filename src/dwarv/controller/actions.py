from enum import Enum


class Action(Enum):
    RETRY_WITH_FEEDBACK = 1  # same model, same ctx
    RETRY_LOWER_TEMP = 2
    RETRY_HIGHER_TEMP = 3  # resample for diversity
    SHRINK_CONTEXT = 4  # reload same model with smaller ctx (needs reload)
    SWITCH_SMALLER_MODEL = 5  # needs reload
    SWITCH_LARGER_MODEL = 6  # needs reload, only if predicted RSS fits headroom
    STOP_SAFELY = 7
