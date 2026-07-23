Looking at the actual `beforeSwap` implementation:

The EXTSLOAD/reentrancy framing in the question is a red herring, but the underlying state-update-before-check ordering bug is **real and demonstrable** from the code alone.

---

### Title
Velocity guard baseline advances intra-block, allowing cumulative price movement to exceed the per-block cap — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

### Summary

`beforeSwap` writes `lastMidPriceX64` and `lastUpdateBlock` to storage **before** the velocity check executes. A second swap in the same block reads the updated storage as its baseline, so each swap is independently allowed `maxChangePerBlock` of movement. Two sequential swaps in the same block can collectively move the price by `2 × maxChangePerBlock`, defeating the guard's purpose.

### Finding Description

In `beforeSwap`, lines 54–55 snapshot `prevMid`/`prevBlock` into locals, then lines 57–58 immediately overwrite storage, and only then do lines 60–76 perform the check using those locals: [1](#0-0) 

The current swap's own check is correct (it uses the local snapshot). The problem is the **next** swap in the same block:

- Swap 1 (block N): `prevMid = P0` (previous block), storage → `P1`, check: `|P1−P0| ≤ cap`. Passes.
- Swap 2 (block N): `prevMid = P1` (swap 1's price), `prevBlock = N`, `blockDiff = 0`, `allowedSq = maxChange² × 1`. Check: `|P2−P1| ≤ cap`. Passes.

Total movement `|P2−P0|` can reach `2 × cap` within a single block. The intended invariant — that all swaps in a block compare against the **previous block's** last price — is broken. [2](#0-1) 

No reentrancy or EXTSLOAD is required; two ordinary sequential swaps suffice.

### Impact Explanation

The velocity guard exists to prevent oracle price manipulation. Bypassing it allows a price that should have been rejected to reach the pool swap, constituting **bad-price execution** under the impact gate. A manipulator can push the oracle mid-price by `N × maxChangePerBlock` in a single block by issuing N swaps, making the cap effectively meaningless for well-capitalised actors.

### Likelihood Explanation

Any unprivileged user can issue two swaps against the same pool in the same block. No special role, malicious setup, or non-standard token is required. The only cost is gas and swap fees.

### Recommendation

Do not advance the stored baseline until the check passes, **or** only update `lastUpdateBlock` when the block actually changes (keeping `lastMidPriceX64` fixed for the remainder of the block):

```solidity
// Only update the baseline when entering a new block
if (block.number > prevBlock) {
    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
}
// ... then run the check against prevMid / prevBlock
```

This ensures all swaps within the same block compare against the same previous-block baseline.

### Proof of Concept

```solidity
// Two swaps in block N, each moving price by (cap - ε)
// Together they move price by 2*(cap - ε) > cap
// Both pass individually; combined movement exceeds the guard.
vm.roll(N);
pool.swap(..., bidPrice1, askPrice1, ...); // |P1-P0| = cap - ε  ✓
pool.swap(..., bidPrice2, askPrice2, ...); // |P2-P1| = cap - ε  ✓
// |P2-P0| ≈ 2*cap — guard never reverted
```

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L54-58)
```text
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L63-70)
```text
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
```
