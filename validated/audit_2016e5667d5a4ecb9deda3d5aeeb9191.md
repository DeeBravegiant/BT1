Looking at the codebase, I need to find an analog to the "stale/incorrect measure used instead of time-decayed/correct one" vulnerability class — specifically, vote counting that doesn't filter by current participants.

Let me examine the TEE governance vote functions and their vote-counting internals.