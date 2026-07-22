Q1700: fee-split drift in buy-token1 analytical target when the current bin is effectively one-sided on one token leg

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with standard-token decimal asymmetry such as 6/18 or 6/6 pairs while the current bin is effectively one-sided on one token leg, so that spread or notional fees are computed from one representation while balances are updated in another along `swap zeroForOne=true path -> analytical target computation for token1 output`, corrupting the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins? This branch is sensitive to rounding because public traders can choose exact-input and exact-output shapes that pin the solution near a crossing threshold. Accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or LP fee ownership.

Target
- File/function: metric-core/contracts/libraries/SwapMath.sol::computeAnalyticalTargetPosForSellToken0
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: standard-token decimal asymmetry such as 6/18 or 6/6 pairs
- Exploit idea: Reach `swap zeroForOne=true path -> analytical target computation for token1 output` in a live public flow and show that accumulate many publicly repeatable operations until the rounding residue shifts protocol, admin, or lp fee ownership. The exact value at risk is the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Invariant to test: Every unit of spread/notional fee must be accounted for exactly once between LPs, protocol, admin, and trader settlement. The concrete assertion should cover the computed final position, `feeExclusiveInputScaled`, and the token1 released from active and crossed bins.
- Expected Immunefi impact: Medium/High protocol-fee loss or LP-fund leakage above Sherlock thresholds.
- Fast validation: Compare analytical and brute-force bin stepping under the same public swap and assert they agree on both the final cursor and the amount of token1 paid out.
