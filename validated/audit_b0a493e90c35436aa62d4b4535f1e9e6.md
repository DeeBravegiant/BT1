### Title
Stale `rsETHPrice` Oracle Allows Sandwich Attack Around Beacon-Chain Checkpoint Completion to Steal Unclaimed Yield — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/NodeDelegator.sol`)

---

### Summary

`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` is explicitly called. After an operator calls `NodeDelegator.startCheckpoint`, any address can call `IEigenPod.verifyCheckpointProofs` to finalize the checkpoint, which immediately increases the NDC's EigenLayer shares (and therefore the protocol's real ETH TVL) via `recordBeaconChainETHBalanceUpdate`. Because `depositETH` prices rsETH using the stale stored `rsETHPrice`, an attacker can deposit between `startCheckpoint` and the price update, mint rsETH at the pre-checkpoint price, then trigger the price update and withdraw at the post-checkpoint price, extracting consensus-layer rewards that belong to existing holders.

---

### Finding Description

**Step 1 — `rsETHPrice` is a stored, lazily-updated value.**

`LRTOracle.rsETHPrice` is a plain `uint256` state variable. [1](#0-0) 

`updateRSETHPrice()` is `public` (no role restriction) but must be called explicitly; it is never called inside `depositETH`. [2](#0-1) 

**Step 2 — `depositETH` prices rsETH using the stored stale value.**

`getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()`, which returns the stored variable, not a freshly computed value. [3](#0-2) 

**Step 3 — `startCheckpoint` is operator-only; `verifyCheckpointProofs` is permissionless.**

`NodeDelegator.startCheckpoint` is gated by `onlyLRTOperator`. [4](#0-3) 

`IEigenPod.verifyCheckpointProofs` is explicitly documented as callable by anyone. [5](#0-4) 

**Step 4 — `getEffectivePodShares` reflects live EigenLayer state immediately after checkpoint finalization.**

`getEffectivePodShares` calls `NodeDelegatorHelper.getWithdrawableShare`, which queries `DelegationManager.getWithdrawableShares` live. After `verifyCheckpointProofs` finalizes the checkpoint and `recordBeaconChainETHBalanceUpdate` is called by the EigenPod, the NDC's withdrawable shares increase immediately. [6](#0-5) [7](#0-6) 

`getETHDistributionData` (used by `_getTotalEthInProtocol` in the oracle) reads `getEffectivePodShares` live. [8](#0-7) 

**Step 5 — The price-update guard (`pricePercentageLimit`) does not block normal consensus rewards.**

`_updateRsETHPrice` reverts for non-managers only if the price increase exceeds `pricePercentageLimit`. Consensus-layer rewards accumulate at ~4–5% APR (≈0.01% per checkpoint cycle), well within any reasonable daily limit. If `pricePercentageLimit == 0` the guard is entirely disabled. [9](#0-8) 

---

### Impact Explanation

Let:
- `X` = total ETH in protocol before checkpoint
- `S` = rsETH supply
- `P = X/S` = stored (stale) rsETHPrice
- `R` = consensus rewards credited by checkpoint
- `D` = attacker deposit

Attacker mints `D·S/X` rsETH at stale price `P`. After the checkpoint, real TVL is `X + R + D`. After `updateRSETHPrice`, the new price is approximately `(X + R + D)/(S + D·S/X)`. The attacker's rsETH is worth `D·(X+R+D)/(X+D) > D`. Profit ≈ `D·R/(X+D)`, extracted directly from existing holders' unclaimed yield.

---

### Likelihood Explanation

- `startCheckpoint` is called routinely by the operator to credit consensus rewards — this is a normal, recurring operation.
- `verifyCheckpointProofs` and `updateRSETHPrice` are both permissionless; the attacker controls the entire sequence after `startCheckpoint`.
- No special privileges, leaked keys, or oracle compromise are required.
- The attack is repeatable every checkpoint cycle.
- The only partial mitigation (`pricePercentageLimit`) does not block attacks during normal reward accrual.

---

### Recommendation

1. **Refresh `rsETHPrice` atomically inside `depositETH` and `initiateWithdrawal`** by calling `_updateRsETHPrice()` (or computing the current price inline) before computing the rsETH mint/burn amount.
2. Alternatively, **compute the rsETH price on-the-fly** in `getRsETHAmountToMint` using `_getTotalEthInProtocol()` and `rsETH.totalSupply()` rather than reading the stored `rsETHPrice`.
3. As a defence-in-depth measure, **restrict `verifyCheckpointProofs` calls** through a wrapper on `NodeDelegator` (though EigenLayer's interface makes this difficult to enforce at the EigenPod level).

---

### Proof of Concept

```solidity
// Fork test (Hardhat/Foundry, mainnet fork)
// 1. Setup: protocol has 1000 ETH TVL, 1000 rsETH supply, rsETHPrice = 1e18
//    Operator calls NodeDelegator.startCheckpoint(false)
//    → checkpoint is open, EigenPod awaiting proofs

// 2. Attacker deposits 10 ETH via LRTDepositPool.depositETH{value: 10 ether}(0, "")
//    → mints 10 rsETH at stale price 1e18 (pre-checkpoint)

// 3. Attacker (or anyone) calls eigenPod.verifyCheckpointProofs(balanceProof, proofs)
//    → checkpoint finalizes, recordBeaconChainETHBalanceUpdate credits +0.1 ETH rewards
//    → getEffectivePodShares() now returns 32.1 ETH (was 32 ETH)
//    → real TVL is now 1010.1 ETH, rsETH supply still 1010

// 4. Attacker calls LRTOracle.updateRSETHPrice()
//    → newRsETHPrice = 1010.1e18 / 1010 ≈ 1.0000990e18

// 5. Attacker calls LRTWithdrawalManager.initiateWithdrawal(ETH, 10 rsETH)
//    → withdrawal valued at 10 * 1.0000990e18 / 1e18 ≈ 10.00099 ETH

// assert(attackerETHOut > 10 ether);  // profit ≈ 0.00099 ETH stolen from existing holders
```

The profit scales linearly with the attacker's deposit size `D` and the checkpoint reward `R`, and is bounded only by the deposit limit and available liquidity.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTDepositPool.sol (L487-487)
```text
            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/NodeDelegator.sol (L259-261)
```text
    function startCheckpoint(bool revertIfNoBalance) external onlyLRTOperator {
        eigenPod.startCheckpoint(revertIfNoBalance);
    }
```

**File:** contracts/NodeDelegator.sol (L556-562)
```text
    function getEffectivePodShares() external view override returns (uint256 ethStaked) {
        uint256 withdrawableShare =
            NodeDelegatorHelper.getWithdrawableShare(lrtConfig, IStrategy(lrtConfig.beaconChainETHStrategy()));

        // staker balances can no longer be negative
        return stakedButUnverifiedNativeETH + withdrawableShare;
    }
```

**File:** contracts/external/eigenlayer/interfaces/IEigenPod.sol (L176-190)
```text
     * @dev Progress the current checkpoint towards completion by submitting one or more validator
     * checkpoint proofs. Anyone can call this method to submit proofs towards the current checkpoint.
     * For each validator proven, the current checkpoint's `proofsRemaining` decreases.
     * @dev If the checkpoint's `proofsRemaining` reaches 0, the checkpoint is finalized.
     * (see `_updateCheckpoint` for more details)
     * @dev This method can only be called when there is a currently-active checkpoint.
     * @param balanceContainerProof proves the beacon's current balance container root against a checkpoint's
     * `beaconBlockRoot`
     * @param proofs Proofs for one or more validator current balances against the `balanceContainerRoot`
     */
    function verifyCheckpointProofs(
        BeaconChainProofs.BalanceContainerProof calldata balanceContainerProof,
        BeaconChainProofs.BalanceProof[] calldata proofs
    )
        external;
```

**File:** contracts/NodeDelegatorHelper.sol (L52-65)
```text
    function getWithdrawableShare(
        ILRTConfig lrtConfig,
        IStrategy strategy
    )
        internal
        view
        returns (uint256 withdrawableShare)
    {
        IStrategy[] memory strategies = new IStrategy[](1);
        strategies[0] = strategy;

        uint256[] memory withdrawableShares = getWithdrawableShares(lrtConfig, strategies);
        return withdrawableShares[0];
    }
```
