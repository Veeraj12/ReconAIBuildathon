"""
Load/save trained cause models as a single, human-readable JSON file —
`trained_weights.json`, living next to this module the same way
`genericBank.json` lives next to `configLoader.py`. This is
deliberately a plain, diffable, code-reviewable artifact: after a
retrain, `git diff` on this file shows exactly which cause_ids'
weights changed and by how much, the same way a config change would.

Nothing here ever pickles a scikit-learn object — see
learning/calibration.py's docstring for why that matters. The file on
disk only ever contains plain numbers (TrainedCauseModel.to_dict()).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from .schema import TrainedCauseModel

DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parent / "trained_weights.json"


def load_trained_models(path: Optional[Path] = None) -> Dict[str, TrainedCauseModel]:
    """Returns {cause_id: TrainedCauseModel}. Returns an empty dict
    (not an error) if the file doesn't exist yet — this is the normal
    "no feedback collected yet" cold-start state, not a failure."""
    path = path or DEFAULT_WEIGHTS_PATH
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {cause_id: TrainedCauseModel.from_dict(entry) for cause_id, entry in raw.items()}


def save_trained_models(models: Dict[str, TrainedCauseModel], path: Optional[Path] = None) -> None:
    path = path or DEFAULT_WEIGHTS_PATH
    payload = {cause_id: model.to_dict() for cause_id, model in models.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
