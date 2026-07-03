Audit Report

## Title
Permissionless `FeeReceiver.sendFunds()` Enables Sandwich Attack to Steal Unclaimed MEV/Execution-Layer Yield - (File: `contracts/FeeReceiver.sol`, `contracts/LRTDepositPool.sol`)

## Summary
`FeeReceiver.sendFunds()` carries no access-control modifier, allowing any caller to flush accumulated MEV/execution-layer rewards into `LRTDepositPool` at will. Because `depositETH()` prices new mints against the cached `rsETHPrice` state variable rather than a freshly computed value, an attacker can sandwich the flush: deposit at the stale lower price, then trigger a price update to crystallise the inflated yield into their rsETH balance, stealing a portion of yield that belonged to existing holders.

## Finding Description

**Root cause — permissionless reward flush**

`FeeReceiver.sendFunds()` has no role guard: [1](#0-0) 

`receiveFromRewardReceiver()` in the deposit pool is equally unguarded: [2](#0-1) 

After the call, `address(this).balance` in `LRTDepositPool` immediately increases by the full reward amount `R`.

**Stale price on deposit**

`depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()` divides by the stored `rsETHPrice` state variable: [3](#0-2) 

`rsETHPrice` is only updated when `_updateRsETHPrice()` is explicitly called. The flushed rewards are already counted in `address(this).balance` (and therefore in `getTotalAssetDeposits` via `getETHDistributionData`): [4](#0-3) 

…but are **not** yet reflected in the cached price. The attacker therefore mints rsETH at a price that does not include `R`.

**Public price update**

`updateRSETHPrice()` is public and callable by anyone: [5](#0-4) 

`_updateRsETHPrice()` recomputes the price from `_getTotalEthInProtocol()`, which reads the live `address(this).balance`. After the attacker's deposit, the total ETH in the protocol is `T + R + D` but the rsETH supply is `S + D·S/T` (minted at the old price `T/S`). The new price is therefore `(T+R+D)·T / (S·(T+D))`, and the attacker's rsETH is worth `D + D·R/(T+D)`, extracting `D·R/(T+D)` ETH of yield from existing holders.

**Why `pricePercentageLimit` does not block this**

The threshold guard only reverts non-managers when the price increase exceeds `pricePercentageLimit`: [6](#0-5) 

This does not protect against the attack because:
- For typical MEV reward sizes (small relative to TVL), the threshold is not breached and the attacker calls `updateRSETHPrice()` directly.
- Even when the threshold is breached, the attacker has **already minted** the excess rsETH in step 2. They simply wait for the manager/keeper to call `updateRSETHPriceAsManager()` as part of normal operations.

## Impact Explanation
Existing rsETH holders lose a fraction of every accumulated reward batch. The attacker's profit per attack is `D·R/(T+D)`, where `D` is the attacker's deposit, `R` is the reward balance in `FeeReceiver`, and `T` is the pre-attack TVL. The stolen yield is permanently transferred to the attacker's rsETH position; existing holders receive proportionally less than they are owed. This matches the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation
- `FeeReceiver.sendFunds()` requires no role, no signature, no precondition — one public call.
- `depositETH()` is the standard user-facing deposit function.
- `updateRSETHPrice()` is public and called routinely by keepers.
- The attack is executable atomically in a single transaction (flash loan for `D` if needed), making it low-cost and repeatable every time rewards accumulate in `FeeReceiver`.

## Recommendation
1. **Restrict `sendFunds()`** to a privileged role (e.g., `MANAGER` or `LRT_OPERATOR`) so that the reward flush cannot be triggered by an arbitrary caller.
2. **Alternatively**, update `rsETHPrice` atomically inside `receiveFromRewardReceiver()` (or inside `sendFunds()` before the transfer) so the price is never stale relative to the deposit pool balance.
3. **Deeper fix**: decouple the TVL accounting for "pending rewards" from `address(this).balance` so that rewards are only included in the price after an authorised oracle update, not immediately upon receipt.

## Proof of Concept
```solidity
// Fork test (Foundry) — unmodified production contracts
function testSandwichYield() public {
    // Setup: FeeReceiver holds 10 ETH of accumulated MEV rewards
    vm.deal(address(feeReceiver), 10 ether);

    uint256 priceBefore = lrtOracle.rsETHPrice(); // e.g. 1.05e18

    // Step 1: attacker flushes rewards into deposit pool (no role required)
    vm.prank(attacker);
    feeReceiver.sendFunds();

    // Step 2: attacker deposits 1 ETH at the stale (lower) price
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
    uint256 rsethMinted = rsETH.balanceOf(attacker);

    // Step 3: price update (public, or wait for keeper)
    lrtOracle.updateRSETHPrice();
    uint256 priceAfter = lrtOracle.rsETHPrice();

    // Attacker's rsETH is worth more than 1 ETH deposited
    uint256 attackerEthValue = rsethMinted * priceAfter / 1e18;
    assertGt(attackerEthValue, 1 ether, "attacker stole yield");

    // Verify: price increased due to flushed rewards
    assertGt(priceAfter, priceBefore);
}
```

The assertion `attackerEthValue > 1 ether` passes because the attacker minted rsETH at `priceBefore` (which did not include the 10 ETH reward), but the price update at step 3 incorporates those rewards into the new price, inflating the value of the attacker's rsETH beyond their 1 ETH deposit.

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L256-265)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```
