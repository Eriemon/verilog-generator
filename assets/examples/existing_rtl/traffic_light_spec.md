# Traffic Light Behavioral Notes

## Reset behavior

When `rst_n` is deasserted low, the controller shall clear `red`, `yellow`, `green`, and restart the internal state machine from idle.

## Sequencing behavior

The controller cycles through red, green, and yellow phases using the internal `cnt` timer. The public `clock` output exposes the remaining count value.

## Pass request behavior

When `pass_request` is asserted during the green phase and the remaining `cnt` value is greater than 10, the controller reloads the timer to shorten the remaining green duration.
