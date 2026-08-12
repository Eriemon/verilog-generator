# Reset Gap Counter Notes

## Reset behavior

When `i_rstn` is low, `o_count` must return to zero.

## Counting behavior

When `i_en` is high, `o_count` increments by one on each rising edge of `i_clk`.
