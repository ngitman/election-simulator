"""
Centralized state metadata used by backend and loaders.
Keeping labels and ids here avoids string duplication and makes i18n easier later.
"""
from __future__ import annotations


STATE_LABELS = {
    "florida": "Florida",
    "new_york": "New York",
}

STATE_FIPS = {
    "florida": "12",
    "new_york": "36",
}

STATE_EC_VOTES = {
    "florida": 30,
    "new_york": 28,
}

SUPPORTED_STATES = tuple(STATE_LABELS.keys())


def normalize_state_key(state: str) -> str:
    return state.lower().replace(" ", "_")


def get_state_label(state_key: str) -> str:
    return STATE_LABELS.get(state_key, state_key)

