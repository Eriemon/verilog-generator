# Existing RTL Verification Notes

- Module `fsm_without_default` should return to `IDLE` after a single `WORK` cycle.
- The state transition logic should keep deterministic behavior for any unlisted `state` encoding.
- Preserve the public interface and clock/reset behavior while repairing control logic.
