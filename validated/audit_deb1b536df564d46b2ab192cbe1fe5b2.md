Looking at the external bug class — **missing input validation during initialization that allows an unintended value to be set, permanently breaking a core invariant** — I need to find an analog in the Metric OMM liquidity path.

Let me trace the `addLiquidity` path carefully.