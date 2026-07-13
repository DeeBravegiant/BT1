Let me analyze the bug class from the report: **state not reset during a protocol phase transition in a multi-component protocol**, leading to stale/incorrect state being used after a "raise" or restart event. I'll search the cb-mpc codebase for analogous patterns.

Let me look more carefully at the specific protocol files for multi-component state management patterns.