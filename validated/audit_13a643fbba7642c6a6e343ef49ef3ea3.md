Audit Report

## Title
Stale `rsETHPrice` Used in Deposit and Instant Withdrawal Flows Enables Yield Theft and Direct Fund Theft - (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`, `contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTOracle.rsETHPrice` is a cached state variable updated only when `updateRSETHPrice()` is explicitly called. Neither the deposit path (`depositAsset()`/`depositETH()`) nor the instant withdrawal path (`instantWithdrawal()`) call `updateRSETHPrice()` before reading `rsETHPrice`. This allows an unprivileged attacker to exploit the gap between the stale cached price and the real current price: depositing when the price is stale-low to receive excess rsETH (diluting existing holders' yield), or instantly withdrawing when the price is stale-high (after a slashing event) to extract more underlying assets than their rsETH is worth.

## Finding Description

**Root cause:** `rsETHPrice` is a storage variable updated only on explicit calls to `updateRSETHPrice()`.

```solidity
// LRTOracle.sol L28
uint256 public override rsETHPrice;

// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Neither deposit nor instant withdrawal functions call `updateRSETHPrice()` before reading this value.

**Deposit path:**

`LRTDepositPool.depositETH()` and `depositAsset()` both call `_beforeDeposit()`, which calls `getRsETHAmountToMint()`:

```solidity
// LRTDepositPool.sol L519-520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

The stale (lower) `rsETHPrice` denominator inflates the rsETH minted. The `minRSETHAmountExpected` slippage guard does not protect existing holders — it only protects the depositor from receiving *less* than expected, not from receiving *more*.

**Instant withdrawal path:**

`LRTWithdrawalManager.instantWithdrawal()` calls `getExpectedAssetAmount()` at L228:

```solidity
// LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

A stale (higher) `rsETHPrice` numerator inflates the assets returned to the withdrawer.

**Why existing guards are insufficient:**

The `pricePercentageLimit` guard in `_updateRsETHPrice()` actually worsens the deposit vector: if the price has risen beyond the configured threshold, non-manager callers of `updateRSETHPrice()` receive a `PriceAboveDailyThreshold` revert, meaning the stale price persists even longer and the exploit window grows. The downside-protection auto-pause (triggered when price drops beyond `pricePercentageLimit`) only fires when `updateRSETHPrice()` is called — an attacker exploiting the instant withdrawal path acts *before* anyone calls it, so the pause never triggers in time.

## Impact Explanation

**Deposit — Theft of unclaimed yield (High):** As staking rewards accrue, the real rsETH/ETH rate rises while `rsETHPrice` remains stale. Because `rsethAmountToMint = amount * assetPrice / rsETHPrice`, a stale-low denominator mints excess rsETH to the depositor. This dilutes the yield accrued by all existing rsETH holders, constituting theft of unclaimed yield.

**Instant withdrawal — Direct theft of user funds (Critical):** After a slashing event reduces the real value of rsETH, `rsETHPrice` remains stale-high. `getExpectedAssetAmount` returns `rsETHUnstaked * rsETHPrice / assetPrice`, which is inflated. The attacker burns rsETH and receives more underlying assets from the unstaking vault than their rsETH is worth, directly stealing funds that belong to other users awaiting withdrawal.

## Likelihood Explanation

The deposit vector is continuously exploitable: staking rewards accrue every block, so `rsETHPrice` is always drifting stale. Any depositor who omits a prior `updateRSETHPrice()` call (or who deposits when the price is above the daily threshold and thus cannot be updated by non-managers) benefits from the stale price. The instant withdrawal vector requires a slashing event, which is less frequent but is a known EigenLayer risk. In both cases, the attacker is an unprivileged external user making standard public function calls with no special preconditions beyond timing.

## Recommendation

Call `updateRSETHPrice()` atomically at the start of `depositAsset()`, `depositETH()`, and `instantWithdrawal()` before any price-dependent computation:

```solidity
ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
```

Alternatively, expose `_updateRsETHPrice()` logic as a `view` function that computes the live price on-the-fly (without writing to storage), and replace all reads of `rsETHPrice` in deposit/withdrawal paths with calls to this view function, eliminating the cached-price attack surface entirely.

## Proof of Concept

**Deposit (yield theft):**
1. Staking rewards accrue; real rsETH/ETH rate rises from `1.00e18` to `1.05e18`, but `updateRSETHPrice()` has not been called, so `rsETHPrice = 1.00e18`.
2. Attacker calls `LRTDepositPool.depositETH{value: 10 ether}(0, "")` without calling `updateRSETHPrice()` first.
3. `getRsETHAmountToMint` computes: `10e18 * 1e18 / 1.00e18 = 10 rsETH`. Correct amount at real price: `10e18 * 1e18 / 1.05e18 ≈ 9.524 rsETH`.
4. Attacker receives `~0.476 rsETH` excess, extracted from yield belonging to existing holders.
5. After `updateRSETHPrice()` is eventually called (by anyone), price updates to `1.05e18` and attacker's rsETH is worth `10.5 ETH` — risk-free profit of `~0.476 ETH`.

**Instant withdrawal (direct fund theft):**
1. A slashing event reduces real rsETH value; real price drops from `1.05e18` to `1.00e18`, but `rsETHPrice` remains `1.05e18`.
2. Attacker calls `LRTWithdrawalManager.instantWithdrawal(asset, 10e18 rsETH, "")`.
3. `getExpectedAssetAmount` computes: `10e18 * 1.05e18 / assetPrice`. At real price it should be `10e18 * 1.00e18 / assetPrice`.
4. Attacker receives `~5%` more underlying assets than their rsETH is worth, draining the unstaking vault at the expense of other users' pending withdrawals.

**Foundry fork test outline:**
- Fork mainnet; warp time forward to accumulate staking rewards without calling `updateRSETHPrice()`.
- Record `rsETHPrice` before and after `updateRSETHPrice()` to confirm staleness.
- Deposit as attacker using stale price; assert minted rsETH exceeds the amount computed at the updated price.
- For the withdrawal vector: mock a slashing event by reducing EigenLayer pod shares; assert `getExpectedAssetAmount` returns more than the post-slash fair value.