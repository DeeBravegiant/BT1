Q1847: paused-withdraw inconsistency in buy-token1 analytical target when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with repeated exact-output attempts that accumulate rounding residue while the active position sits exactly at the start of a bin segment, so that the contract intentionally permits withdrawals while paused, but some reachable branch still depends on active-swap assumptions along `swap zeroForOne=true path -> analytical target computation for token1 output`, corrupting the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins? This branch is sensitive to rounding because public traders can choose exact-input and exact-output shapes that pin the solution near a crossing threshold. Pause the pool through the scoped factory rules, then burn liquidity from a state that still carries active-swap accounting assumptions.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForSellToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: repeated exact-output attempts that accumulate rounding residue
- Exploit idea: Reach `swap zeroForOne=true path -> analytical target computation for token1 output` in a live public flow and show that pause the pool through the scoped factory rules, then burn liquidity from a state that still carries active-swap accounting assumptions. The exact value at risk is the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Invariant to test: A paused pool must remain solvent and withdrawable for honest LPs without reusing swap-only assumptions. The concrete assertion should cover the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Expected Immunefi impact: Medium broken core functionality or constrained loss of LP funds above contest thresholds.
- Fast validation: Compare analytical and brute-force bin stepping under the same public swap and assert they agree on both the final cursor and the amount of token1 paid out.
