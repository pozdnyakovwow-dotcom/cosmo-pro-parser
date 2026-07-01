from apps.reviews.services.input_loader import DoctorSeed, normalize_profile_url
from apps.reviews.services.matching import group_seeds_by_doctor, normalize_text, split_full_name


def test_normalize_text_removes_parentheses_and_extra_spaces():
    assert normalize_text("Spasskaya Olga Yurevna (Dekret)") == "spasskaya olga yurevna"


def test_group_seeds_by_doctor_merges_same_doctor_across_sources():
    seeds = [
        DoctorSeed(
            clinic_label="Moscow, Leninsky",
            doctor_name="Anandaeva Ayuna Bolotovna",
            source_site="doctu",
            profile_url="https://doctu.example/doctor",
            source_file="a.csv",
            input_payload={},
        ),
        DoctorSeed(
            clinic_label="Doctors Leninsky",
            doctor_name="Anandaeva Ayuna Bolotovna",
            source_site="prodoctorov",
            profile_url="https://prodoctorov.example/doctor",
            source_file="b.csv",
            input_payload={},
        ),
    ]
    grouped = group_seeds_by_doctor(seeds)
    assert len(grouped) == 1
    only_group = next(iter(grouped.values()))
    assert {item.source_site for item in only_group} == {"doctu", "prodoctorov"}


def test_normalize_profile_url_removes_fragment_and_trailing_slash():
    url = "HTTPS://ProDoctorov.ru/moskva/vrach/939453-anandaeva//#otzivi"
    assert normalize_profile_url(url) == "https://prodoctorov.ru/moskva/vrach/939453-anandaeva"


def test_split_full_name_returns_last_first_middle():
    last_name, first_name, middle_name = split_full_name("Лачашвили Нино Паатовна")
    assert last_name == "Лачашвили"
    assert first_name == "Нино"
    assert middle_name == "Паатовна"
