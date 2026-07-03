Audit Report

## Title
Stale Stored `rsETHPrice` Used in L2 Pool Deposit Minting Allows Depositors to Receive Excess rsETH — (`contracts/cross-chain/RSETHRateProvider.sol`, `contracts/pools/RSETHPoolV3.sol`)

## Summary
`LRTOracle` stores the rsETH/ETH exchange rate in a state variable `rsETHPrice` that is only refreshed when `updateRSETHPrice()` is explicitly called. Every L2 pool deposit path (`RSETHPoolV3`, `RSETHPoolV2`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) computes the rsETH minting amount by reading this stored value through the rate provider without triggering a refresh. When the stored price is stale and lower than the true current price, depositors receive more rsETH than their ETH contribution warrants, diluting existing holders' accrued restaking yield.

## Finding Description
`LRTOracle` stores the exchange rate as a plain state variable:

```solidity
uint256 public override rsETHPrice; // LRTOracle.sol L28
```

This value is only updated when `updateRSETHPrice()` or `updateRSETHPriceAsManager()` is explicitly called (LRTOracle.sol L87–96). Neither function is called atomically within any deposit transaction.

`RSETHRateProvider.getLatestRate()` and `RSETHMultiChainRateProvider.getLatestRate()` both read this stored value directly:

```solidity
return ILRTOracle(rsETHPriceOracle).rsETHPrice(); // RSETHRateProvider.sol L28
```

Every pool deposit path calls `viewSwapRsETHAmountAndFee()`, which calls `getRate()`, which calls the rate provider — returning the stale stored value:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate; // RSETHPoolV3.sol L304,307
```

Because rsETH accrues restaking rewards monotonically, a stale (lower) `rsETHPrice` produces a larger `rsETHAmount`. No freshness check, no atomic price update, and no on-chain staleness guard exists in any deposit path across all pool variants. The same stale value is used in `_updateRsETHPrice()` to compute `previousTVL` (LRTOracle.sol L234), understating it and overstating the protocol fee — compounding the mis-accounting.

## Impact Explanation
**High — Theft of unclaimed yield.**

Existing rsETH holders' accrued restaking yield is encoded in the appreciation of `rsETHPrice`. A depositor who mints at a stale lower price receives a larger share of the total rsETH supply than their ETH contribution justifies. When `rsETHPrice` is subsequently updated to the true value, the per-token ETH backing of all existing holders is diluted, effectively transferring their accrued yield to the new depositor. The magnitude scales with deposit size and the duration of staleness. This matches the allowed impact "Theft of unclaimed yield."

## Likelihood Explanation
`updateRSETHPrice()` is a public function but is never called atomically within any deposit. The protocol relies entirely on off-chain keepers or altruistic callers to keep the price current. Any unprivileged depositor can observe the stale `rsETHPrice` on-chain (by comparing it against a freshly computed value off-chain or simply by noting the last update block), deposit before anyone calls `updateRSETHPrice()`, and then call `updateRSETHPrice()` themselves after minting to lock in the gain. The attack is permissionless, repeatable every time the price drifts, and requires no special access or victim cooperation.

## Recommendation
Before computing `rsETHAmount` in each pool's deposit path, trigger a price update so the rate used for minting is always current. Concretely:

1. **Preferred (stateful):** Have the pool (or rate provider) call `ILRTOracle(rsETHPriceOracle).updateRSETHPrice()` before reading `rsETHPrice()` in the deposit path — analogous to how `exchangeRateCurrent()` accrues interest before returning the rate.
2. **Alternative (view-only):** Expose a `rsETHPriceCurrent()` view on `LRTOracle` that computes the price on-the-fly from live TVL without writing state, and have pool contracts call that instead of the stored `rsETHPrice`.

Either approach eliminates the staleness window and ensures depositors cannot mint at a price below the true current rate.

## Proof of Concept
1. At time T, `LRTOracle.rsETHPrice = 1.01e18` (last updated N blocks ago).
2. Restaking rewards accrue; true price rises to `1.02e18`, but `updateRSETHPrice()` has not been called.
3. Attacker calls `RSETHPoolV3.deposit{value: 100 ether}("ref")`.
4. `viewSwapRsETHAmountAndFee(100e18)` → `getRate()` → `RSETHRateProvider.getLatestRate()` → returns stale `1.01e18`.
5. `rsETHAmount = (100e18 - fee) * 1e18 / 1.01e18 ≈ 99.0099 rsETH`.
6. Correct amount at true price: `(100e18 - fee) * 1e18 / 1.02e18 ≈ 98.0392 rsETH`.
7. Attacker receives `≈ 0.97 rsETH` excess — value extracted from existing holders' accrued yield.
8. Attacker calls `updateRSETHPrice()` themselves after minting; price updates to `1.02e18`, locking in the dilution of existing holders.

**Foundry fork test plan:**
```solidity
function testStaleRsETHPriceMintExcess() public fork {
    // 1. Record current rsETHPrice
    uint256 stalePriceStored = lrtOracle.rsETHPrice();
    // 2. Advance time / simulate reward accrual so true TVL > stalePriceStored * supply
    vm.warp(block.timestamp + 1 days);
    // 3. Deposit as attacker without calling updateRSETHPrice first
    uint256 rsETHBefore = wrsETH.balanceOf(attacker);
    vm.prank(attacker);
    pool.deposit{value: 100 ether}("ref");
    uint256 rsETHMinted = wrsETH.balanceOf(attacker) - rsETHBefore;
    // 4. Compute what should have been minted at true price
    lrtOracle.updateRSETHPrice();
    uint256 truePrice = lrtOracle.rsETHPrice();
    uint256 correctMint = (100 ether - fee) * 1e18 / truePrice;
    // 5. Assert attacker received more than correct amount
    assertGt(rsETHMinted, correctMint);
}
```