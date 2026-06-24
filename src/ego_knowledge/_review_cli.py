"""Shared implementation for ``ek review`` CLI command."""

from __future__ import annotations

from collections.abc import Callable

import click

from .core import EgoKnowledge


def run_review_command(
    *,
    get_ek: Callable[[], EgoKnowledge],
    entry_to_output: Callable[[object], dict[str, object]],
    show_due: bool,
    overdue: bool,
    queue_id: str | None,
    resolve_id: str | None,
    dismiss_id: str | None,
    origin: str | None,
    approve_flag: bool,
    reject_flag: bool,
    reject_reason: str,
) -> object:
    """Execute ``ek review`` after Click has parsed options."""

    if show_due or overdue:
        ek = get_ek()
        try:
            return [entry_to_output(entry) for entry in ek.review_queue(overdue_only=True)]
        finally:
            ek.close()

    if approve_flag or reject_flag:
        if not queue_id:
            raise click.UsageError("--approve / --reject 需要配合 --id 使用")
        if approve_flag and reject_flag:
            raise click.UsageError("--approve 和 --reject 互斥")
        if resolve_id is not None or dismiss_id is not None:
            raise click.UsageError("--approve / --reject 与 --resolve / --dismiss 互斥")

        from ._approval_executor import approve as executor_approve
        from ._approval_executor import reject as executor_reject

        ek = get_ek()
        try:
            if approve_flag:
                return executor_approve(ek, queue_id)
            return executor_reject(ek, queue_id, reason=reject_reason)
        finally:
            ek.close()

    provided = sum(1 for value in (queue_id, resolve_id, dismiss_id) if value is not None)
    if provided > 1:
        raise click.UsageError("--id / --resolve / --dismiss 互斥，只能选一个")

    ek = get_ek()
    try:
        return ek.maintenance_queue_review(
            queue_id=queue_id,
            resolve_id=resolve_id,
            dismiss_id=dismiss_id,
            origin=origin,
        )
    finally:
        ek.close()
