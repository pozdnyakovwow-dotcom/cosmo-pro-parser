import re
from collections import defaultdict
from typing import Iterable

from .input_loader import DoctorSeed


def normalize_text(value: str) -> str:
    normalized = value.lower().replace("ё", "е").strip()
    normalized = re.sub(r"\([^)]*\)", " ", normalized)
    normalized = re.sub(r"[^a-zа-я0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def split_full_name(full_name: str) -> tuple[str, str, str]:
    parts = normalize_display_name(full_name).split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return "", parts[0], ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], " ".join(parts[2:])


def normalize_display_name(full_name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", full_name)).strip()


def group_seeds_by_doctor(seeds: Iterable[DoctorSeed]) -> dict[str, list[DoctorSeed]]:
    grouped: dict[str, list[DoctorSeed]] = defaultdict(list)
    for seed in seeds:
        grouped[normalize_text(seed.doctor_name)].append(seed)
    return dict(grouped)
