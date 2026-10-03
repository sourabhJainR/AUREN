# Open-Ended Task & Environment Discovery

Discovery converts externally observed failures into bounded exploration targets
for future independent evaluation.

It introduces novelty through:
- unseen task instances;
- fresh environments;
- constraint shifts;
- long-horizon tasks;
- adversarial constraints;
- unfamiliar tools.

Every target requires a fresh holdout and independent oracle. The module only
creates an exploration specification; it has no execution, oracle, or
capability-promotion authority.

Flow:

external failure -> discovery signal -> novel target -> independent generator
-> fresh environment -> independent oracle -> validation -> learning loop
