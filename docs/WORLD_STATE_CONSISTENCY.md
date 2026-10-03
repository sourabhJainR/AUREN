# World-State Consistency and Uncertainty

The persistent WorldModel stores observations and predictions. This layer adds
an uncertainty-aware assessment so the runtime does not equate "latest"
with "true".

It groups observations by value, discounts stale observations, preserves
observation IDs and sources, and marks conflicting state as uncertain. A
selected state is returned only when exactly one hypothesis remains after the
configured filters.

This is an inference boundary: observations remain provenance-bearing evidence;
the assessment is not written back as a fact.
