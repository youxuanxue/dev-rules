"""Deterministic helpers for the /xj-review skill.

The skill's prose only keeps real judgement; mechanical bookkeeping
(loop rounds, circuit-breaker counters) lives here so the model consumes
deterministic stdout instead of re-counting by hand every turn.
"""
