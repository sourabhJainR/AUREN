# Campaign Intervention Loop

This module closes the operational loop between external evaluation and
learning: measured failure patterns become falsifiable intervention hypotheses,
and every proposal produces a retest contract requiring a new independent
holdout.

It deliberately does not execute learning changes, promote capabilities, or
decide that an intervention worked. Those decisions remain downstream of
independent evaluation and causal evidence.

The intended loop is:

external campaign -> failure attribution -> intervention hypothesis ->
fresh independent holdout -> causal evidence -> promotion/rollback evidence.
