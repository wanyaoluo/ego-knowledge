from __future__ import annotations

from datetime import date, timedelta

from .support import dossier_payload, source_payload


def test_review_queue_orders_and_filters_overdue_dossiers(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="复盘来源"))
    overdue = fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="年度审查档案",
            reviewed_at=date.today() - timedelta(days=40),
            review_due_at=date.today() - timedelta(days=1),
        ),
    )
    upcoming = fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="季度复盘档案",
            reviewed_at=date.today(),
            review_due_at=date.today() + timedelta(days=7),
        ),
    )

    all_items = fresh_ek.review_queue(overdue_only=False)
    overdue_items = fresh_ek.review_queue(overdue_only=True)

    assert [entry.id for entry in all_items] == [overdue.id, upcoming.id]
    assert [entry.id for entry in overdue_items] == [overdue.id]
