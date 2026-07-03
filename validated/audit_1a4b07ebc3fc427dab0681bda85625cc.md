All cited code references check out against the actual source. Let me verify the complete attack chain:

**`FeeReceiver.sendFunds()` — no access control:** [1](#0-0) 

**`receiveFromRewardReceiver()` — unrestricted payable:** [2](#0-1) 

**`getETHDistributionData()` — uses raw `address(this).balance`:** [3](#0-2) 

**`updateRSETHPrice()` — public, no role guard:** [4](#0-3) 

**Price guard reverts for non-manager callers when threshold exceeded:** [5](#0-4) 

**Deposit minting uses stored (potentially stale) `rsETHPrice`:** [6](#0-5) 

Every step of the described exploit is confirmed by the actual code. The attack chain is coherent: `sendFunds()` inflates `address(this).balance` in the deposit pool → `updateRSETHPrice()` reverts for a non-manager caller when the TVL spike exceeds `pricePercentageLimit` → `rsETHPrice` stays stale → attacker deposits at the under-valued price → manager eventually updates the price → attacker's rsETH is worth more than they paid, at the expense of existing holders.

---

Audit Report

## Title
Unrestricted `sendFunds()` in `FeeReceiver` Enables Stale-Price Deposit to Steal Accumulated Yield - (File: contracts/FeeReceiver.sol)

## Summary
`FeeReceiver.sendFunds()` carries no access-control modifier, allowing any external caller to flush accumulated MEV/execution-layer rewards into `LRTDepositPool` at will. Because `LRTOracle.updateRSETHPrice()` reverts for non-manager callers when the resulting price increase exceeds `pricePercentageLimit`, an attacker can force the reward dump, cause the oracle update to revert, and then deposit at the stale (under-valued) `rsETHPrice`, minting more rsETH than the true exchange rate warrants and stealing yield from all existing rsETH holders.

## Finding Description
`FeeReceiver.sendFunds()` is declared `external` with no role check (contracts/FeeReceiver.sol L53–58). It moves the entire ETH balance into `LRTDepositPool` via `receiveFromRewardReceiver()`, which is itself unrestricted and payable (contracts/LRTDepositPool.sol L61). Once the ETH lands in the pool, `getETHDistributionData()` counts it immediately via `ethLyingInDepositPool = address(this).balance` (contracts/LRTDepositPool.sol L480), which feeds `_getTotalEthInProtocol()` in `LRTOracle`.

`updateRSETHPrice()` is publicly callable (`public whenNotPaused`, contracts/LRTOracle.sol L87–89). When the TVL spike from the forced reward dump causes `newRsETHPrice` to exceed `highestRsethPrice` by more than `pricePercentageLimit`, the function reverts with `PriceAboveDailyThreshold` for any non-manager caller (contracts/LRTOracle.sol L252–266). The stored `rsETHPrice` is therefore not updated.

Deposits then use this stale, lower `rsETHPrice` as the denominator (contracts/LRTDepositPool.sol L519–520):
```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
A lower denominator means the attacker receives more rsETH per ETH than the true exchange rate warrants. When the manager eventually calls `updateRSETHPriceAsManager()`, the price rises to reflect the reward dump, and the attacker's inflated rsETH balance is now worth more than they paid — at the direct expense of all pre-existing rsETH holders.

## Impact Explanation
**High — Theft of unclaimed yield.** Existing rsETH holders are entitled to the price appreciation that accumulated MEV rewards represent. The attacker captures a disproportionate share of that appreciation by depositing at the stale price, diluting every existing holder's claim on the underlying assets. The magnitude scales with the size of the accumulated reward balance in `FeeReceiver` relative to total TVL.

## Likelihood Explanation
**Medium.** `pricePercentageLimit` is explicitly configurable via `setPricePercentageLimit()` and the oracle code is written to enforce it, making it likely to be set in production. MEV/execution-layer rewards accumulate continuously; over days or weeks the balance can be large enough to push a single-block TVL spike past the configured threshold. The attack requires only two public calls (`sendFunds()` + `updateRSETHPrice()`) and a subsequent deposit — no special privileges, no flash loan, and no front-running of a specific transaction.

## Recommendation
Restrict `sendFunds()` to an authorized role so that reward accounting can only be triggered by a trusted keeper or manager:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Additionally, restrict `receiveFromRewardReceiver()` in `LRTDepositPool` to only accept calls from the registered `FeeReceiver` address, preventing direct ETH injection from arbitrary callers that could achieve the same TVL inflation independently.

## Proof of Concept
```
Setup:
  FeeReceiver holds 500 ETH in accumulated MEV rewards.
  rsETHPrice = 1.05e18, highestRsethPrice = 1.05e18.
  pricePercentageLimit = 1e16 (1%).

Step 1: Attacker calls FeeReceiver.sendFunds().
  → 500 ETH transferred to LRTDepositPool.
  → LRTDepositPool.address(this).balance increases by 500 ETH.
  → getETHDistributionData() now returns 500 ETH more.

Step 2: Attacker calls LRTOracle.updateRSETHPrice().
  → _getTotalEthInProtocol() returns ~500 ETH higher value.
  → newRsETHPrice >> 1.05e18 * 1.01 (exceeds 1% threshold).
  → Reverts with PriceAboveDailyThreshold().
  → rsETHPrice remains at 1.05e18 (stale).

Step 3: Attacker calls LRTDepositPool.depositETH{ value: X }(minRSETH, "").
  → getRsETHAmountToMint uses rsETHPrice = 1.05e18 (stale).
  → Attacker receives rsETH as if the 500 ETH reward bump never happened.

Step 4: Manager calls LRTOracle.updateRSETHPriceAsManager().
  → rsETHPrice updated to true higher value reflecting the 500 ETH.
  → Attacker's rsETH is now worth more than they paid,
    at the expense of all pre-existing rsETH holders.

Foundry fork test: deploy against mainnet state, execute the four-step
sequence above, assert that attacker's rsETH redemption value exceeds
their deposit amount, and assert that existing holders' redemption value
decreased proportionally.
```

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
