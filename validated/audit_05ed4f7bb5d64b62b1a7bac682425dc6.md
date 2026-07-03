Audit Report

## Title
Stale Cached `rsETHPrice` in `getRsETHAmountToMint()` Allows Depositors to Capture Unclaimed Yield from Existing Holders - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getRsETHAmountToMint()` divides a live Chainlink asset price by `lrtOracle.rsETHPrice()`, a state variable that is only updated by explicit calls to `updateRSETHPrice()` or `updateRSETHPriceAsManager()`. Between keeper updates, as yield accrues (stETH rebases, EigenLayer rewards), the stored price falls below the true on-chain rate. Any unprivileged depositor who calls `depositETH()` or `depositAsset()` during this window receives more rsETH than their deposit warrants, permanently diluting existing holders and capturing their accrued yield.

## Finding Description
`LRTOracle` stores the rsETH/ETH exchange rate as a plain state variable:

```solidity
// contracts/LRTOracle.sol:28
uint256 public override rsETHPrice;
```

This value is written only inside `_updateRsETHPrice()`, which is invoked by `updateRSETHPrice()` (public, permissionless) or `updateRSETHPriceAsManager()` (manager-only). Neither function is called automatically on deposit.

`getRsETHAmountToMint()` mixes a live feed price with the stale stored price:

```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` delegates to a live `IPriceFetcher` (Chainlink), while `lrtOracle.rsETHPrice()` returns the last written snapshot. The two inputs are therefore from different points in time.

`_beforeDeposit()` calls `getRsETHAmountToMint()` with no price refresh:

```solidity
// contracts/LRTDepositPool.sol:665
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```

Both `depositETH()` and `depositAsset()` call `_beforeDeposit()` directly without any guard that forces a price update first. No existing check in `_beforeDeposit()` — deposit-amount bounds, deposit-limit check, or `minRSETHAmountExpected` slippage guard — prevents minting against a stale denominator; the slippage guard only protects the depositor from receiving *less* than expected, not from receiving *more*.

When `_updateRsETHPrice()` is eventually called, it computes the new price as:

```solidity
// contracts/LRTOracle.sol:250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

At that point `rsethSupply` already includes the excess rsETH minted to the attacker, so the new price is permanently suppressed relative to what it would have been without the dilutive deposit.

## Impact Explanation
This is **High — Theft of unclaimed yield**. Existing rsETH holders have a legitimate claim to yield that has accrued since the last price update. A depositor who exploits the stale price receives rsETH tokens that represent a fractional claim on that accrued yield without having contributed it. After the price update, the attacker's excess rsETH is backed by TVL that belonged to prior holders, permanently reducing the per-token value those holders receive. The loss is proportional to the yield accrued in the update window and the size of the exploiting deposit; it is not recoverable.

## Likelihood Explanation
The condition — stored price below true price — is a normal operating state that exists during every interval between keeper calls. No special role, flash loan, or oracle manipulation is required. Any externally owned account can call `depositETH()` or `depositAsset()` at any time. The attacker simply monitors the accrued yield (observable on-chain via `_getTotalEthInProtocol()` vs. `rsethSupply × rsETHPrice`) and deposits before the keeper fires. The attack is repeatable every update cycle and requires only capital.

## Recommendation
Atomically refresh `rsETHPrice` inside `_beforeDeposit()` (or at the top of `getRsETHAmountToMint()`) before computing the mint amount, so the denominator always reflects the current on-chain TVL:

```solidity
function _beforeDeposit(...) private returns (uint256 rsethAmountToMint) {
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice();
    ...
    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    ...
}
```

Alternatively, expose a pure view function in `LRTOracle` that computes the current rsETH price on-the-fly from `_getTotalEthInProtocol()` and `rsethSupply` without writing state, and use that in `getRsETHAmountToMint()` instead of the stored variable.

## Proof of Concept
**Setup:**
- `rsethSupply = 1000 rsETH`, `rsETHPrice = 1.00 ETH` (stored), true TVL = 1050 ETH (50 ETH yield accrued; true price = 1.05 ETH).
- `updateRSETHPrice()` has **not** been called.

**Steps:**
1. Attacker calls `depositETH{value: 105 ETH}(0, "")`.
2. `getRsETHAmountToMint` computes: `105e18 * 1e18 / 1.00e18 = 105 rsETH` (stale denominator). Correct amount at true price: `105 / 1.05 = 100 rsETH`. Attacker receives **5 excess rsETH**.
3. Keeper calls `updateRSETHPrice()`. New supply = 1105 rsETH, TVL = 1155 ETH. New price = `1155 / 1105 ≈ 1.0453 ETH`.
4. Attacker's 105 rsETH is worth `105 × 1.0453 ≈ 109.76 ETH` — a profit of ~4.76 ETH on a 105 ETH deposit.
5. Original 1000 rsETH holders now hold `1000 × 1.0453 ≈ 1045.3 ETH` instead of the 1050 ETH they were entitled to — ~4.7 ETH of their yield has been transferred to the attacker.

**Foundry fork test plan:**
```solidity
function testStaleRsETHPriceDilution() public fork {
    // 1. Simulate yield accrual: warp time, trigger stETH rebase
    // 2. Assert lrtOracle.rsETHPrice() < computed true price
    // 3. Record balances of existing holder
    // 4. Attacker deposits ETH without calling updateRSETHPrice()
    // 5. Call updateRSETHPrice()
    // 6. Assert attacker rsETH value > deposited ETH
    // 7. Assert existing holder ETH-equivalent balance decreased vs. pre-deposit
}
```