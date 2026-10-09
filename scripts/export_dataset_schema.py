"""Regenerate .actor/dataset_schema.json from the output model."""

from __future__ import annotations

import json
from pathlib import Path

from src.models.output import Result

FIELDS = ["status", "checkedAt", "atTime", "summary", "constraints", "coverage", "billing"]


def main() -> None:
    schema = {
        "actorSpecification": 1,
        "fields": Result.model_json_schema(by_alias=True),
        "views": {
            "overview": {
                "title": "Overview",
                "transformation": {"fields": FIELDS},
                "display": {"component": "table", "properties": {f: {"label": f} for f in FIELDS}},
            }
        },
    }
    Path(".actor/dataset_schema.json").write_text(json.dumps(schema, indent=2) + "\n")


if __name__ == "__main__":
    main()
