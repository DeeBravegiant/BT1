Q1632: rounding desynchronization in buy-token1 analytical target when protocol and admin fee accumulators are already non-zero

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with timing around a just-moved cursor or a just-paused pool while protocol and admin fee accumulators are already non-zero, so that two reachable math branches round in opposite directions and stop agreeing on the same terminal state along `swap zeroForOne=true path -> analytical target computation for token1 output`, corrupting the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins? This branch is sensitive to rounding because public traders can choose exact-input and exact-output shapes that pin the solution near a crossing threshold. Push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForSellToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: timing around a just-moved cursor or a just-paused pool
- Exploit idea: Reach `swap zeroForOne=true path -> analytical target computation for token1 output` in a live public flow and show that push the public flow into an edge condition where ceil/floor decisions desynchronize price, position, and payout accounting. The exact value at risk is the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Invariant to test: A reachable user flow must conserve value across scaled math, native transfers, and the final cursor location. The concrete assertion should cover the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Expected Immunefi impact: High/Critical direct loss through swap-conservation failure or LP principal leakage.
- Fast validation: Compare analytical and brute-force bin stepping under the same public swap and assert they agree on both the final cursor and the amount of token1 paid out.
