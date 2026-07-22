Q1797: cross-bin overstatement in buy-token1 analytical target when a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public router settlement that reaches the pool callback path while a preparatory user action moved `curBinIdx` or `curPosInBin` immediately before exploitation, so that crossing logic counts output from one bin while charging input as if a different terminal position had been reached along `swap zeroForOne=true path -> analytical target computation for token1 output`, corrupting the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins? This branch is sensitive to rounding because public traders can choose exact-input and exact-output shapes that pin the solution near a crossing threshold. Drive the active cursor across a thin edge so one branch advances the bin while another still prices against the previous state.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForSellToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public router settlement that reaches the pool callback path
- Exploit idea: Reach `swap zeroForOne=true path -> analytical target computation for token1 output` in a live public flow and show that drive the active cursor across a thin edge so one branch advances the bin while another still prices against the previous state. The exact value at risk is the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Invariant to test: For every crossed bin, input charged and output granted must come from the same final per-bin state transition. The concrete assertion should cover the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Expected Immunefi impact: Critical direct loss if a trader can repeatedly extract more output than bin reserves allow.
- Fast validation: Compare analytical and brute-force bin stepping under the same public swap and assert they agree on both the final cursor and the amount of token1 paid out.
