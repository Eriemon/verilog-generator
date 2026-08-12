# Ready Valid Slice Notes

## Handshake behavior

When `i_in_valid` is asserted while `o_in_ready` is high, the slice captures `i_in_data` and presents it on `o_out_data` on the next cycle.

## Backpressure behavior

The slice deasserts `o_in_ready` while a buffered item is waiting to be forwarded.

## Reset behavior

When `i_rstn` is low, the buffered valid/data state and public outputs return to zero.
