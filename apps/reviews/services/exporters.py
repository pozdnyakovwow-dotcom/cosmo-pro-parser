import csv
import json
from pathlib import Path
from typing import Iterable, Union


EXPORT_COLUMNS = [
    "clinic",
    "doctor",
    "source_site",
    "doctor_profile_url",
    "review_text",
    "review_published_at",
    "review_rating",
]


def flatten_export_records(records: Iterable[dict], include_empty_reviews: bool = True) -> list[dict]:
    rows: list[dict] = []
    for record in records:
        base = {
            "clinic": record.get("clinic", ""),
            "doctor": record.get("doctor", ""),
            "source_site": record.get("source_site", ""),
            "doctor_profile_url": record.get("doctor_profile_url", ""),
        }
        reviews = record.get("reviews") or []
        if not reviews and include_empty_reviews:
            rows.append(
                {
                    **base,
                    "review_text": "",
                    "review_published_at": "",
                    "review_rating": "",
                }
            )
            continue
        for review in reviews:
            rows.append(
                {
                    **base,
                    "review_text": review.get("review_text", ""),
                    "review_published_at": review.get("review_published_at", ""),
                    "review_rating": review.get("review_rating", ""),
                }
            )
    return rows


def export_to_csv(rows: Iterable[dict], output_path: Union[str, Path]) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in EXPORT_COLUMNS})
    return output


def export_to_json(rows: Iterable[dict], output_path: Union[str, Path]) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(list(rows), handle, ensure_ascii=False, indent=2)
    return output
