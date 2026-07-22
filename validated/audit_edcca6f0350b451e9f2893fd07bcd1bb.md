Looking at the external bug class: a deadline/timestamp parameter passed through multiple calls without validation that it is not already expired, causing a silent failure that leaves state in an incorrect condition.

Let me trace the Sequencer's proposal validation path for this pattern.