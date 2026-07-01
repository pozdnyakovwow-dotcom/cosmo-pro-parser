import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass
class DoctorSeed:
    clinic_label: str
    doctor_name: str
    source_site: str
    profile_url: str
    source_file: str
    input_payload: dict

    def to_dict(self) -> dict:
        return asdict(self)


class CSVInputLoader:
    SOURCE_PATTERNS = {
        "docdoc": "DocDoc",
        "doctu": "Докту",
        "napopravku": "НаПоправку",
        "prodoctorov": "Апрель 2026",
    }

    def __init__(self, docs_dir: Union[str, Path]):
        self.docs_dir = Path(docs_dir)

    def load(self) -> list[DoctorSeed]:
        seeds: list[DoctorSeed] = []
        seen_pairs: set[tuple[str, str]] = set()
        for path in sorted(self.docs_dir.glob("*.csv")):
            source = self._detect_source(path.name)
            if not source:
                continue
            if source == "prodoctorov":
                current_rows = self._load_prodoctorov_csv(path)
            else:
                current_rows = self._load_standard_csv(path, source)
            for seed in current_rows:
                dedupe_key = (seed.source_site, normalize_profile_url(seed.profile_url))
                if dedupe_key in seen_pairs:
                    continue
                seen_pairs.add(dedupe_key)
                seed.profile_url = dedupe_key[1]
                seeds.append(seed)
        return seeds

    def _detect_source(self, filename: str) -> Optional[str]:
        for source, token in self.SOURCE_PATTERNS.items():
            if token in filename:
                return source
        return None

    def _load_standard_csv(self, path: Path, source_site: str) -> Iterable[DoctorSeed]:
        rows = self._read_rows(path)
        seeds: list[DoctorSeed] = []
        for row in rows:
            clinic = (row.get("Клиника") or "").strip()
            doctor = (row.get("Врач") or "").strip()
            url = normalize_profile_url((row.get("Ссылка на отзывы") or "").strip())
            if not doctor or not url.startswith("http"):
                continue
            seeds.append(
                DoctorSeed(
                    clinic_label=clinic,
                    doctor_name=doctor,
                    source_site=source_site,
                    profile_url=url,
                    source_file=path.name,
                    input_payload=row,
                )
            )
        return seeds

    def _load_prodoctorov_csv(self, path: Path) -> Iterable[DoctorSeed]:
        rows = self._read_raw_rows(path)
        if not rows:
            return []
        clinic_slots = [
            (0, 1, rows[0][0] if len(rows[0]) > 0 else ""),
            (3, 4, rows[0][3] if len(rows[0]) > 3 else ""),
        ]
        seeds: list[DoctorSeed] = []
        for row in rows[2:]:
            for doctor_index, url_index, clinic_label in clinic_slots:
                if len(row) <= url_index:
                    continue
                doctor = row[doctor_index].strip()
                url = normalize_profile_url(row[url_index].strip())
                if not doctor or not url.startswith("http"):
                    continue
                seeds.append(
                    DoctorSeed(
                        clinic_label=clinic_label.strip(),
                        doctor_name=doctor,
                        source_site="prodoctorov",
                        profile_url=url,
                        source_file=path.name,
                        input_payload={
                            "comment": row[url_index + 1].strip() if len(row) > url_index + 1 else "",
                        },
                    )
                )
        return seeds

    def _read_rows(self, path: Path) -> list[dict]:
        encodings = ("utf-8-sig", "utf-8", "cp1251")
        for encoding in encodings:
            try:
                with path.open("r", encoding=encoding, newline="") as handle:
                    return list(csv.DictReader(handle))
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("csv", b"", 0, 1, f"Cannot decode {path}")

    def _read_raw_rows(self, path: Path) -> list[list[str]]:
        encodings = ("utf-8-sig", "utf-8", "cp1251")
        for encoding in encodings:
            try:
                with path.open("r", encoding=encoding, newline="") as handle:
                    return list(csv.reader(handle))
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("csv", b"", 0, 1, f"Cannot decode {path}")


def normalize_profile_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url.strip())
    normalized_query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    normalized_path = parsed.path.rstrip("/") or parsed.path or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            normalized_query,
            "",
        )
    )
