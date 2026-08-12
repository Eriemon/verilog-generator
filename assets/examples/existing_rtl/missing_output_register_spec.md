# Existing RTL Verification Notes

- Module `missing_output_register` should forward `i_valid` and the matching data path in one registered stage.
- `o_valid` and `o_data` should stay interface-compatible with the current RTL.
- The repair should complete the missing sequential datapath assignment instead of changing ports.
