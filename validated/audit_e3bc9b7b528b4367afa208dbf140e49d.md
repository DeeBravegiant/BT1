Let me analyze the bug class from the external report and search for analogs in the sequencer codebase.

The core invariant violated: a "kill/disable" operation updates an aggregate counter but leaves item-level state intact, causing downstream calculations to use inconsistent state (inflated index from reduced total, but non-zero item weight still generates amounts).