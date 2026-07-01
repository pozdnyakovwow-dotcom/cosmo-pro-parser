from apps.reviews.services.exporters import flatten_export_records


def test_flatten_export_records_creates_separate_row_for_each_review():
    rows = flatten_export_records(
        [
            {
                "clinic": "Moscow, Leninsky",
                "doctor": "Anandaeva Ayuna Bolotovna",
                "source_site": "prodoctorov",
                "doctor_profile_url": "https://example.com/doctor",
                "reviews": [
                    {
                        "review_text": "Very good",
                        "review_published_at": "2026-01-01T10:00:00",
                        "review_rating": "5.0",
                    },
                    {
                        "review_text": "Good",
                        "review_published_at": "2026-01-02T10:00:00",
                        "review_rating": "4.0",
                    },
                ],
            }
        ]
    )
    assert len(rows) == 2
    assert rows[0]["review_text"] == "Very good"
    assert rows[1]["review_rating"] == "4.0"


def test_flatten_export_records_keeps_doctor_without_reviews():
    rows = flatten_export_records(
        [
            {
                "clinic": "Moscow, Leninsky",
                "doctor": "No Reviews Doctor",
                "source_site": "doctu",
                "doctor_profile_url": "https://example.com/no-reviews",
                "reviews": [],
            }
        ],
        include_empty_reviews=True,
    )
    assert len(rows) == 1
    assert rows[0]["review_text"] == ""
