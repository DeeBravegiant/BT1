### Title
`PriceVelocityGuardExtension` Uses `block.number` for Velocity Timing, Causing Swap DoS on Arbitrum — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

### Summary

`PriceVelocityGuardExtension.beforeSwap` computes `blockDiff = block.number - prevBlock` to scale the allowed price-change budget. On Arbitrum, `block.number` returns the most recently synced L1 block number, which is updated only once per minute. Multiple swaps within the same L1 sync window all observe the same `block.number`, so `blockDiff = 0`, collapsing the allowed budget to its minimum. Any oracle price movement between those swaps that exceeds `maxChangePerBlockE18` causes every subsequent swap in that window to revert with `PriceVelocityExceeded`, making the pool unusable for the duration of the window.

### Finding Description

The velocity guard stores `lastUpdateBlock` as `uint64(block.number)` and computes the allowed squared change as:

```solidity
uint256 blockDiff = block.number - prevBlock;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
``` [1](#0-0) 

On Ethereum mainnet, `block.number` increments once per block (~12 s), so consecutive swaps in different blocks yield `blockDiff ≥ 1`. On Arbitrum, `block.number` is an alias for the L1 block number, which is synced to the Sequencer only once per minute. All Arbitrum transactions processed within the same L1 block sync window share the same `block.number`. Therefore:

- Swap A executes at Arbitrum time T; `lastUpdateBlock` is set to L1 block N.
- Swap B executes 5 seconds later (still within the same L1 sync window); `block.number` is still N.
- `blockDiff = N - N = 0`.
- `allowedSq = maxChange² × 1` — the minimum possible budget.

If the oracle price has moved by more than `maxChangePerBlockE18` in those 5 seconds (entirely plausible for volatile assets), `actualSq > allowedSq` and the swap reverts. Every subsequent swap in the same L1 window faces the same condition, because `lastUpdateBlock` is updated to the same N on each call. [2](#0-1) 

The existence of `PriceProviderL2.sol` and `ProtectedPriceProviderL2.sol` — which include explicit L2 sequencer clock-skew handling via `FUTURE_TOLERANCE` — confirms that L2 deployment is an intended and supported use case. [3](#0-2) 

### Impact Explanation

When `blockDiff = 0`, the velocity guard enforces the strictest possible budget for every swap in the L1 sync window. Any oracle price movement above `maxChangePerBlockE18` — which is the normal operating condition during active markets — causes `PriceVelocityExceeded` to revert every swap. The pool becomes completely unusable for swaps for up to one minute at a time, repeatedly, whenever the oracle price moves. This is broken core swap functionality, not a gas-only DoS: no swap can settle, no LP fees accrue, and traders cannot execute.

### Likelihood Explanation

Arbitrum is a primary L2 target (evidenced by the L2-specific price provider contracts). Any pool on Arbitrum that enables `PriceVelocityGuardExtension` with a non-zero `maxChangePerBlockE18` will experience this condition during every active trading period. The trigger requires no privileged access: any user submitting a swap during a price-movement window hits the revert. The condition recurs every ~60 seconds whenever the oracle price moves between swaps.

### Recommendation

Replace `block.number` with `block.timestamp` throughout `PriceVelocityGuardExtension`. Rename `lastUpdateBlock` to `lastUpdateTime`, rename `maxChangePerBlockE18` to `maxChangePerSecondE18` (or per-interval), and redefine `blockDiff` as elapsed seconds:

```solidity
uint256 timeDiff = block.timestamp - prevTime;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + timeDiff);
```

`block.timestamp` is reliable on both Arbitrum and Optimism (it reflects the sequencer's wall-clock time, not the L1 block counter), and it is already used correctly for staleness checks in `PriceProviderL2` and `ProtectedPriceProviderL2`. [4](#0-3) 

### Proof of Concept

**Setup:** Deploy `PriceVelocityGuardExtension` on Arbitrum. Configure a pool with `maxChangePerBlockE18 = 0.01e18` (1% per block). Set `lastMidPriceX64` to price P₀.

**Step 1:** At Arbitrum time T₀, a swap arrives. The oracle mid-price is P₀. `blockDiff = block.number - lastUpdateBlock`. If this is the first swap after `setLastMidPrice`, `blockDiff` may be large and the swap passes. `lastUpdateBlock` is set to L1 block N.

**Step 2:** 10 seconds later (T₀ + 10 s), the oracle updates its price to P₁ = 1.02 × P₀ (2% move, within normal market volatility). A second swap arrives. `block.number` is still N (Arbitrum has not yet synced to the next L1 block). `blockDiff = 0`.

**Step 3:** Compute:
```
changeE18 = (0.02 * P₀ / P₀) * 1e18 = 0.02e18
actualSq  = (0.02e18)² = 4e32
allowedSq = (0.01e18)² * (1 + 0) = 1e32
actualSq (4e32) > allowedSq (1e32) → revert PriceVelocityExceeded
```

**Step 4:** Every swap for the next ~50 seconds (until the next L1 block sync) faces the same `blockDiff = 0` and the same revert. The pool is frozen for swaps for the entire L1 sync window.

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L55-73)
```text
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L36-38)
```text
    /// @dev L2 sequencer timestamp can lag behind oracle publication time.
    ///      Allows refTime up to FUTURE_TOLERANCE seconds ahead of block.timestamp.
    uint256 public immutable FUTURE_TOLERANCE;
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L104-109)
```text
        if (block.timestamp < lastConfidenceUpdate + CONFIDENCE_COOLDOWN) {
            revert CooldownNotElapsed();
        }

        confidenceParam = newValue;
        lastConfidenceUpdate = block.timestamp;
```
