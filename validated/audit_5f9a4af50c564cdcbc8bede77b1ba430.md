Audit Report

## Title
Publicly Callable `updateRSETHPrice()` Enables Sandwich Attack to Steal Yield from rsETH Holders - (File: `contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` carries no access restriction beyond `whenNotPaused` and can be called by any address. An attacker can deposit assets while `rsETHPrice` is stale (lower), immediately trigger the price update themselves, then initiate a withdrawal at the newly elevated price — capturing staking yield that should have accrued pro-rata to all existing rsETH holders. The instant-withdrawal variant collapses the entire attack into a single block with no delay.

## Finding Description

`updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard:

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`_updateRsETHPrice()` computes the new price as `(totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)`. As EigenLayer staking rewards accrue, `totalETHInProtocol` grows while `rsethSupply` stays constant, so `newRsETHPrice > rsETHPrice` (the stale stored value).

**Deposit minting formula** (`LRTDepositPool.getRsETHAmountToMint`, L519-520):
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
A lower `rsETHPrice` means more rsETH minted per unit deposited.

**Withdrawal redemption formula** (`LRTWithdrawalManager.getExpectedAssetAmount`, L592-593):
```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
A higher `rsETHPrice` means more assets returned per rsETH burned.

**Standard attack sequence:**
1. Attacker calls `depositETH()` while `rsETHPrice` is stale (lower) → receives inflated rsETH.
2. Attacker calls `LRTOracle.updateRSETHPrice()` directly → `rsETHPrice` jumps to reflect accrued rewards.
3. Attacker calls `initiateWithdrawal()` → `expectedAssetAmount` is locked in at the new, higher `rsETHPrice`.
4. After `withdrawalDelayBlocks` (~8 days), attacker calls `completeWithdrawal()` and receives more assets than deposited.

The `_calculatePayoutAmount` minimum check in `_unlockWithdrawalRequests` (L824-834) does **not** prevent this: it takes `min(expectedAssetAmount, currentReturn_at_unlock_time)`. Since the attacker already locked in `expectedAssetAmount` at the elevated price in step 3, and the price at unlock time will be at least that level (rewards only accrue), the attacker receives the full inflated amount.

**Instant-withdrawal variant (atomic, single block):** When `isInstantWithdrawalEnabled[asset] == true`, `instantWithdrawal()` calls `getExpectedAssetAmount()` live at execution time with no minimum-cap mechanism:
```
depositETH → updateRSETHPrice → instantWithdrawal
```
This collapses all four steps into one block with zero price risk.

**Partial mitigation — `pricePercentageLimit`:** The check at L252-266 reverts a non-manager caller only if `pricePercentageLimit > 0` AND the price increase exceeds `pricePercentageLimit.mulWad(highestRsethPrice)`. This is insufficient because: (a) if `pricePercentageLimit == 0` there is no cap at all; (b) any price increase that stays within the limit is fully exploitable; (c) the attacker can observe the accrued yield on-chain and wait until rewards accumulate just below the threshold before striking.

## Impact Explanation

The attacker captures staking yield that should have been distributed pro-rata to all existing rsETH holders. They deposit at the stale price (receiving more rsETH than fair value), trigger the price update, and redeem at the new price — extracting the entire reward delta without having held rsETH during the accrual period. This is a direct **theft of unclaimed yield** from legitimate holders, matching the High severity impact class. At scale (large deposit, long accrual window), the stolen amount is proportional to the total protocol yield since the last price update.

## Likelihood Explanation

- `updateRSETHPrice()` is unconditionally callable by any address; no special role or permission is needed.
- The stale-price window is fully observable on-chain: an attacker can compute `_getTotalEthInProtocol()` off-chain and know exactly how much yield has accrued before calling.
- The attack requires only two standard user-facing transactions (`depositETH`/`depositAsset` and `initiateWithdrawal`) plus one permissionless oracle call.
- The 8-day withdrawal delay is the only friction for the standard path; the instant-withdrawal path removes even that.
- The attack is repeatable every time rewards accrue and the price has not yet been updated.

## Recommendation

Restrict `updateRSETHPrice()` to authorized callers so that an attacker cannot atomically control both the deposit timing and the price-update timing:

```solidity
// Before
function updateRSETHPrice() public whenNotPaused {

// After
function updateRSETHPrice() external whenNotPaused onlyLRTManager {
```

Alternatively, snapshot the rsETH price at deposit time and use the **minimum** of the deposit-time price and the current price when computing the withdrawal amount, preventing any benefit from a price increase that occurs after deposit.

## Proof of Concept

```
Assume:
  rsETHPrice (stale) = 1.05e18  (1.05 ETH per rsETH)
  rsETHPrice (new)   = 1.06e18  (after rewards accrued)
  assetPrice (ETH)   = 1e18

Step 1 — depositETH(1 ether):
  rsethMinted = 1e18 * 1e18 / 1.05e18 = 0.9524 rsETH

Step 2 — updateRSETHPrice():
  rsETHPrice updated to 1.06e18

Step 3 — initiateWithdrawal(ETH, 0.9524 rsETH):
  expectedAssetAmount = 0.9524e18 * 1.06e18 / 1e18 = 1.00952 ETH
  (locked in at initiation time)

Step 4 — completeWithdrawal() (after delay):
  _calculatePayoutAmount returns min(1.00952 ETH, 0.9524e18 * 1.06e18 / 1e18)
  = min(1.00952 ETH, 1.00952 ETH) = 1.00952 ETH
  Attacker receives 1.00952 ETH, having deposited 1 ETH.
  Profit = 0.00952 ETH per 1 ETH deposited (~0.95% of deposit).

Instant-withdrawal variant (single block, no delay):
  depositETH(1 ether) → updateRSETHPrice() → instantWithdrawal(ETH, 0.9524 rsETH)
  instantWithdrawal calls getExpectedAssetAmount live → 1.00952 ETH returned immediately.
  Profit = 0.00952 ETH, atomically, with no price risk.
```

Foundry fork test plan: fork mainnet, deploy/use existing contracts, call `depositETH` with 1 ETH, call `updateRSETHPrice()` as an unprivileged EOA, call `instantWithdrawal` (if enabled) or `initiateWithdrawal` + fast-forward blocks + `unlockQueue` + `completeWithdrawal`, assert final ETH balance exceeds initial deposit minus gas.