Looking at the code carefully, I need to trace the exact execution path through `_updateRsETHPrice()` when either `lrtDepositPool` or `withdrawalManager` is paused.

The execution path is clear. Here is my assessment:

---

### Title
Fee Yield Permanently Lost When Deposit Pool or Withdrawal Manager Is Paused — (`contracts/LRTOracle.sol`)

### Summary
When `LRTDepositPool` or `LRTWithdrawalManager` is independently paused (while `LRTOracle` itself remains unpaused), any call to `updateRSETHPrice()` skips the protocol fee computation but **still updates `rsETHPrice` to the full TVL-backed price**. The yield that accrued during the pause is permanently absorbed into the price baseline, making it irrecoverable as protocol fee.

### Finding Description

In `_updateRsETHPrice()`, the `protocolPaused` flag is derived from the pause state of three contracts: [1](#0-0) 

When `protocolPaused` is `true`, the fee computation block is skipped entirely, leaving `protocolFeeInETH = 0`: [2](#0-1) 

The new price is then computed using the full `totalETHInProtocol` with no fee deducted: [3](#0-2) 

And `rsETHPrice` is unconditionally updated to this new value at the end of the function (no early return for the paused case): [4](#0-3) 

The next time `updateRSETHPrice()` is called (after the pause is lifted), `previousTVL` is computed as:

```
previousTVL = rsethSupply * rsETHPrice
```

where `rsETHPrice` already incorporates the yield that accrued during the pause. If no additional yield has accrued since the pause ended, `totalETHInProtocol ≈ previousTVL`, so the condition `totalETHInProtocol > previousTVL` is false and no fee is taken. The fee opportunity for the entire paused period is permanently gone.

The `LRTOracle`'s own `whenNotPaused` modifier only checks the oracle's own `paused` state: [5](#0-4) 

So `updateRSETHPrice()` remains callable by anyone while the deposit pool or withdrawal manager is paused. Both `LRTDepositPool` and `LRTWithdrawalManager` inherit from OpenZeppelin's `PausableUpgradeable` and can be paused independently of the oracle: [6](#0-5) [7](#0-6) 

### Impact Explanation

The protocol treasury permanently loses all fee revenue for any period during which the deposit pool or withdrawal manager is paused. Staking rewards continue to accrue on-chain (EigenLayer restaking rewards, beacon chain ETH) regardless of the protocol's pause state. Each call to `updateRSETHPrice()` during the pause silently shifts the price baseline upward, permanently erasing the fee entitlement for that yield increment. This matches **Medium — Permanent freezing of unclaimed yield**.

### Likelihood Explanation

Pauses are a routine operational event (security incidents, upgrades, emergency responses). The oracle itself does not need to be paused. Any EOA or keeper can call the public `updateRSETHPrice()` during the pause, and the price update will proceed. The longer the pause duration and the more yield that accrues, the larger the permanent fee loss. No privileged access or collusion is required beyond the normal pause event itself.

### Recommendation

Do not update `rsETHPrice` when `protocolPaused` is `true` and there is a TVL increase that would have generated a fee. Two options:

1. **Skip the price update entirely when paused** — return early (or avoid writing `rsETHPrice`) when `protocolPaused && totalETHInProtocol > previousTVL`, so the yield delta is preserved for the next call after unpause.
2. **Decouple fee accrual from the pause flag** — track a `pendingFeeETH` accumulator that is incremented even during pauses, and mint it on the first post-pause price update.

Option 1 is simpler and lower risk. The key invariant to enforce is: **`rsETHPrice` must not advance past a point where unearned fee yield has been silently absorbed**.

### Proof of Concept

```solidity
// Fork test outline (local fork, no mainnet)
function test_pausedDepositPoolSuppressesFee() public {
    // 1. Setup: protocol has rsETH supply, rsETHPrice = 1e18
    uint256 initialPrice = lrtOracle.rsETHPrice(); // 1e18

    // 2. Pause the deposit pool (not the oracle)
    vm.prank(pauser);
    lrtDepositPool.pause();
    assertTrue(lrtDepositPool.paused());
    assertFalse(lrtOracle.paused()); // oracle still callable

    // 3. Simulate TVL growth (e.g., EigenLayer rewards accrue)
    // totalETHInProtocol increases by 10 ETH above previousTVL
    _simulateTVLGrowth(10 ether);

    // 4. Call updateRSETHPrice() — oracle is not paused, so this succeeds
    lrtOracle.updateRSETHPrice();

    // 5. Price increased (yield absorbed), but no fee was minted
    assertGt(lrtOracle.rsETHPrice(), initialPrice);
    // FeeMinted event was never emitted

    // 6. Unpause and call again — no fee taken because baseline already shifted
    vm.prank(admin);
    lrtDepositPool.unpause();
    lrtOracle.updateRSETHPrice();
    // Still no FeeMinted — the 10 ETH yield is permanently lost as fee
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L240-240)
```text
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;
```

**File:** contracts/LRTOracle.sol (L243-247)
```text
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L26-26)
```text
contract LRTDepositPool is ILRTDepositPool, LRTConfigRoleChecker, PausableUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/LRTWithdrawalManager.sol (L26-30)
```text
contract LRTWithdrawalManager is
    ILRTWithdrawalManager,
    LRTConfigRoleChecker,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
```
