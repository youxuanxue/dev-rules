"""Deterministic GOFLAGS merge: keep existing flags, add one -trimpath."""

from __future__ import annotations


def merge_trimpath(existing: str) -> str:
    tokens = [token for token in existing.split() if token]
    kept = [token for token in tokens if token != "-trimpath"]
    kept.append("-trimpath")
    return " ".join(kept)
