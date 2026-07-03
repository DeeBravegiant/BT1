### Title
Double-Truncation in `_updateRsETHPrice` Causes `rsETHPrice` to Drift Downward on No-Reward Updates — (`contracts/LRTOracle.sol`)

---

### Summary

`_updateRsETHPrice` computes `previousTVL` via `mulWad` (floor) and then `newRsETHPrice` via `divWad` (floor). The round-trip `floor(floor(S·P / WAD) · WAD / S)` is provably `≤ P`, and strictly less than `P` whenever `(S·P) mod WAD ∈ (0, S)`. For any supply `S ≥ WAD` (≥ 1 rsETH) and a non-WAD-aligned price, this condition is always satisfied, so every no-reward oracle update silently decrements `rsETHPrice` by 1 wei.

---

### Finding Description

Both `WadMath.mulWad` and `WadMath.divWad` truncate (floor): [1](#0-0) 

Inside `_updateRsETHPrice`:

```
previousTVL    = floor(S · P / WAD)          // line 234
newRsETHPrice  = floor(TVL · WAD / S)         // line 250
``` [2](#0-1) 

When `totalETHInProtocol == previousTVL` (no new rewards, `protocolFeeInETH = 0`):

```
newRsETHPrice = floor( floor(S·P / WAD) · WAD / S )
```

Let `r = (S·P) mod WAD`. Then `floor(S·P / WAD) = (S·P − r) / WAD`, and:

```
floor( (S·P − r) · WAD / (WAD · S) ) = floor( P − r/S )
```

- `r = 0` → result = P (stable)
- `0 < r < S` → `0 < r/S < 1` → result = **P − 1** (price drops 1 wei)
- `r ≥ S` → result = P − ⌊r/S⌋ − … (drops more)

**Critical observation:** when `S ≥ WAD` (at least 1 rsETH in supply), `r < WAD ≤ S`, so the condition `r < S` is **always** satisfied. The price decreases by 1 wei on every no-reward call whenever `(S·P) mod WAD ≠ 0`.

For a supply of `S = 1e18 + 1` (one rsETH plus 1 wei — the typical state after the first deposit), `(S·P) mod WAD = P mod 1e18`, which is zero only when P is an exact integer multiple of 1 ETH. Any fractional price (e.g., `P = 1e18 + k`) will decrease by 1 wei per call until it reaches the next lower multiple of 1e18.

`updateRSETHPrice()` has no access control — it is callable by anyone: [3](#0-2) 

---

### Impact Explanation

Every call to `updateRSETHPrice()` with no new rewards silently reduces `rsETHPrice` by 1 wei. Existing rsETH holders redeem for slightly less ETH than they are owed. No principal is destroyed (the underlying ETH remains in the protocol), but the exchange rate drifts below the true value — a failure to deliver promised returns. This matches the **Low** scope: *Contract fails to deliver promised returns, but doesn't lose value.*

---

### Likelihood Explanation

The condition is met under normal operation:
- Any supply `S ≥ 1e18` (at least 1 rsETH) combined with any non-integer-ETH price triggers it.
- `updateRSETHPrice()` is public; no special role is required.
- Periods of no reward accrual (e.g., between EigenLayer reward distributions) are routine.

---

### Recommendation

Replace the `divWad` call for `newRsETHPrice` with a round-up variant when `totalETHInProtocol == previousTVL`, or add a guard:

```solidity
// After computing newRsETHPrice:
if (protocolFeeInETH == 0 && newRsETHPrice < rsETHPrice) {
    newRsETHPrice = rsETHPrice; // preserve price when no reward accrued
}
```

Alternatively, add a `mulDivRoundUp` path in `WadMath` and use it for the price computation, or compare `newRsETHPrice` against `rsETHPrice` before writing storage and only update when `newRsETHPrice >= rsETHPrice` (absent a genuine loss event).

---

### Proof of Concept

Concrete arithmetic (no fuzzing needed):

```
S = 3e18 + 1   (rsethSupply: 3 rsETH + 1 wei)
P = 1e18 + 1   (rsETHPrice:  1 ETH + 1 wei)

S·P = (3e18+1)·(1e18+1) = 3e36 + 4e18 + 1
r   = (3e36 + 4e18 + 1) mod 1e18 = 1
r < S  →  condition met

previousTVL   = floor((3e36 + 4e18 + 1) / 1e18) = 3e18 + 4
totalETHInProtocol = 3e18 + 4   (no new rewards)
protocolFeeInETH   = 0

newRsETHPrice = floor((3e18 + 4)·1e18 / (3e18 + 1))
              = floor((3e36 + 4e18) / (3e18 + 1))
              = floor(((3e18+1)·(1e18+1) − 1) / (3e18+1))
              = floor(1e18 + 1 − 1/(3e18+1))
              = 1e18   <   1e18 + 1  ✗
```

The invariant `newRsETHPrice ≥ rsETHPrice` is violated. Calling `updateRSETHPrice()` repeatedly (it is public) with no reward accrual will walk the price from `1e18 + k` down to `1e18` in `k` transactions. [2](#0-1) [1](#0-0)

### Citations

**File:** contracts/utils/WadMath.sol (L17-27)
```text
    function mulWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        z = x.mulDiv(y, WAD);
    }

    /// @notice Divides two WAD numbers
    /// @param x First WAD number
    /// @param y Second WAD number
    /// @return z Result of division in WAD
    function divWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        z = x.mulDiv(WAD, y);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L234-250)
```text
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```
