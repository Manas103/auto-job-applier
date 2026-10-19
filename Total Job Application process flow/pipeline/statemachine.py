"""Job state machine: the only way a job's state changes."""

from __future__ import annotations

import sqlite3

from . import db
from .models import Job, JobState, Stage
from .util import now_iso

S = JobState
LITE_STAGE = "lite"  # stage_runs.stage and jobs.failed_stage for the light/archetype lanes

# Stage triplets: (ready, running, done)
STAGE_STATES: dict[Stage, tuple[JobState, JobState, JobState]] = {
    Stage.IDEAMAKER: (S.JD_READY, S.IDEAMAKER_RUNNING, S.IDEAMAKER_DONE),
    Stage.BUILDER: (S.IDEAMAKER_DONE, S.BUILDING, S.BUILT),
    Stage.RECONCILE: (S.BUILT, S.RECONCILING, S.RECONCILED),
    Stage.CRITIC: (S.RECONCILED, S.REVIEWING, S.DONE),
}

TRANSITIONS: dict[JobState, set[JobState]] = {
    S.NEW: {S.SCREENED_OUT, S.PINGED, S.APPROVED},
    S.PINGED: {S.APPROVED, S.IGNORED, S.SCREENED_OUT},  # screened_out: rescreen after a rule change
    S.IGNORED: {S.APPROVED},
    S.SCREENED_OUT: {S.APPROVED, S.NEW},  # NEW: rescreened in after a rule change
    S.APPROVED: {S.JD_READY, S.NEEDS_JD, S.SCREENED_OUT},
    S.NEEDS_JD: {S.JD_READY},
    S.JD_READY: {S.IDEAMAKER_RUNNING, S.TAILORING},
    S.TAILORING: {S.DONE, S.FAILED, S.JD_READY},  # JD_READY: recovered after a restart
    S.IDEAMAKER_RUNNING: {S.IDEAMAKER_DONE, S.FAILED, S.JD_READY},
    S.IDEAMAKER_DONE: {S.BUILDING},
    S.BUILDING: {S.BUILT, S.FAILED, S.IDEAMAKER_DONE},
    S.BUILT: {S.RECONCILING},
    S.RECONCILING: {S.RECONCILED, S.FAILED, S.BUILT},
    S.RECONCILED: {S.REVIEWING},
    S.REVIEWING: {S.DONE, S.FAILED, S.RECONCILED},
    S.FAILED: {S.JD_READY, S.IDEAMAKER_DONE, S.BUILT, S.RECONCILED},
    S.DONE: {S.REVIEWING, S.JD_READY},  # JD_READY: a lite resume upgraded to the full pipeline
}


class IllegalTransition(Exception):
    pass


def can_transition(frm: JobState, to: JobState) -> bool:
    return to in TRANSITIONS.get(frm, set())


def ready_state_for_stage(stage: Stage) -> JobState:
    return STAGE_STATES[stage][0]


def running_state_for_stage(stage: Stage) -> JobState:
    return STAGE_STATES[stage][1]


def done_state_for_stage(stage: Stage) -> JobState:
    return STAGE_STATES[stage][2]


def stage_for_running_state(state: JobState) -> Stage | None:
    for stage, (_, running, _) in STAGE_STATES.items():
        if running == state:
            return stage
    return None


def stage_after(state: JobState) -> Stage | None:
    """Which stage runs next from a ready state; None if the state is not a ready state."""
    for stage, (ready, _, _) in STAGE_STATES.items():
        if ready == state:
            return stage
    return None


def stage_by_name(name: str) -> Stage:
    return Stage(name)


def transition(conn: sqlite3.Connection, job_id: int, to: JobState, event: str, detail: dict | None = None, *,
               expect_from: JobState | None = None, failed_stage: str | None = None,
               extra_fields: dict | None = None) -> Job:
    """Atomically move a job to `to`. Raises IllegalTransition when the move is not allowed
    (or the current state is not `expect_from`). The BEGIN IMMEDIATE makes this a safe claim."""
    with db.tx(conn):
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise IllegalTransition(f"job {job_id} does not exist")
        job = Job.from_row(row)
        if expect_from is not None and job.state != expect_from:
            raise IllegalTransition(f"job {job_id}: expected {expect_from.value}, found {job.state.value}")
        if not can_transition(job.state, to):
            raise IllegalTransition(f"job {job_id}: {job.state.value} -> {to.value} is not allowed")
        fields: dict = {"state": to, "state_updated_at": now_iso()}
        if to == S.FAILED:
            fields["failed_stage"] = failed_stage
        elif job.state == S.FAILED:
            fields["failed_stage"] = None
        if to in (S.APPROVED, S.IGNORED):
            fields["decided_at"] = now_iso()
        if extra_fields:
            fields.update(extra_fields)
        db.update_job(conn, job_id, **fields)
        db.add_event(conn, job_id, event, job.state.value, to.value, detail)
    out = db.job_by_id(conn, job_id)
    assert out is not None
    return out


def retry_target(job: Job) -> JobState | None:
    """For a failed job, the ready state to return to."""
    if job.state != S.FAILED or not job.failed_stage:
        return None
    if job.failed_stage == LITE_STAGE:
        return S.JD_READY
    try:
        return ready_state_for_stage(Stage(job.failed_stage))
    except ValueError:
        return None
