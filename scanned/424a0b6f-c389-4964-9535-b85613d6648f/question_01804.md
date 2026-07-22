Q1804: dust-share asymmetry in buy-token1 analytical target when the active position sits exactly at the start of a bin segment

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with standard-token decimal asymmetry such as 6/18 or 6/6 pairs while the active position sits exactly at the start of a bin segment, so that the dust floor or minimal-liquidity rule applies differently to mint and burn paths along `swap zeroForOne=true path -> analytical target computation for token1 output`, corrupting the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins? This branch is sensitive to rounding because public traders can choose exact-input and exact-output shapes that pin the solution near a crossing threshold. Create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForSellToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: standard-token decimal asymmetry such as 6/18 or 6/6 pairs
- Exploit idea: Reach `swap zeroForOne=true path -> analytical target computation for token1 output` in a live public flow and show that create or unwind tiny but valid public positions until share rounding grants value that a symmetric path would not. The exact value at risk is the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Invariant to test: Minimal-liquidity enforcement must never let a reachable LP cycle mint and burn into net-positive value extraction. The concrete assertion should cover the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Expected Immunefi impact: Medium/High LP-principal loss or unusable liquidity operations that break core functionality.
- Fast validation: Compare analytical and brute-force bin stepping under the same public swap and assert they agree on both the final cursor and the amount of token1 paid out.
