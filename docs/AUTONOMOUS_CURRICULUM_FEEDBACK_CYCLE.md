# Autonomous Curriculum Feedback Cycle

Phase 19 closes the learning loop after an executed external campaign.

Failed validation probes become explicit discovery signals. The discovery
engine creates novel targets, and autonomous curriculum evolution selects the
next fresh holdout set. The prior curriculum digest is checked before any
feedback is accepted.

Clean campaigns do not manufacture synthetic failures or fake progress. If no
failure evidence can produce a new target, the cycle fails closed and waits
for a genuinely new external signal.

The module has no capability promotion, lifecycle mutation, benchmark-answer,
or oracle authority.
