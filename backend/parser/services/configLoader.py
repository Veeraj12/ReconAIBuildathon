import json
from pathlib import Path


class ConfigLoader:
    """
    Loads a header-mapping config (see genericBank.json / genericTally.json
    in this same directory) so that supporting a new bank/tally export
    format is a JSON edit, not a code change.

    To add a new export format: open the relevant JSON file and add the
    new header spelling to the appropriate alias list under
    "headerMapping". Nothing in reconciliationService.py needs to change.
    """

    CONFIG_DIR = Path(__file__).resolve().parent

    @classmethod
    def load(cls, filename):
        path = cls.CONFIG_DIR / filename
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)