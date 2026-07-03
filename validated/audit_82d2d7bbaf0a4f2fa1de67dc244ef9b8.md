Audit Report

## Title
Stale `rsETHPrice` Enables Depositors to Extract Unclaimed Yield from Existing Holders - (File: `contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool.getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()`, a cached storage variable that is only updated when `updateRSETHPrice()` is explicitly called. Neither `depositETH` nor `depositAsset` triggers this update before computing the mint amount. Because EigenLayer rewards continuously increase the true ETH-per-rsETH rate, any deposit made between oracle updates mints excess rsETH that is not backed by real ETH, diluting existing holders' accrued but unreflected yield.

## Finding Description
`getRsETHAmountToMint` in `LRTDepositPool.sol` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` fetches a live price from the configured `IPriceFetcher`. `lrtOracle.rsETHPrice()` reads the storage variable `rsETHPrice` in `LRTOracle`, which is only written inside `_updateRsETHPrice()` at line 313, triggered exclusively by explicit calls to `updateRSETHPrice()` (public, permissionless) or `updateRSETHPriceAsManager()`.

`_beforeDeposit` (called by both `depositETH` and `depositAsset`) calls `getRsETHAmountToMint` without first calling `updateRSETHPrice()`. As EigenLayer rewards accrue, `totalETHInProtocol` grows while `rsethSupply` stays constant, so the true rsETH/ETH rate rises continuously. Between oracle updates, `rsETHPrice` is lower than the actual current rate.

A depositor who calls `depositETH` while the price is stale receives:
```
rsethAmountToMint = amount / staleLowerRsETHPrice > amount / trueRsETHPrice
```

The excess rsETH is not backed by real ETH. When `updateRSETHPrice()` is next called, `newRsETHPrice = totalETHInProtocol / rsethSupply` is computed with the inflated supply, yielding a lower price than would have resulted without the excess minting. All existing holders' rsETH is now worth less ETH than before the deposit.

No existing guard prevents this. The `pricePercentageLimit` check in `_updateRsETHPrice()` only fires on price *increases* above a threshold and does not prevent stale-price deposits. The `minRSETHAmountExpected` slippage parameter protects the depositor, not existing holders.

## Impact Explanation
**High — Theft of unclaimed yield.** The unreflected EigenLayer rewards belong to existing rsETH holders. A depositor who transacts while `rsETHPrice` is stale receives a portion of that unreflected yield without having earned it. The theft is proportional to (a) the time elapsed since the last `updateRSETHPrice()` call and (b) the EigenLayer reward accrual rate. Because `updateRSETHPrice()` is called off-chain on a periodic schedule, the stale window is the normal operating state between updates, making this condition continuously exploitable.

## Likelihood Explanation
**Medium.** No special permissions are required; `depositETH` is open to any user. A sophisticated actor can monitor the `RsETHPriceUpdate` event to identify when the stale gap is largest, deposit at that moment, then immediately call `updateRSETHPrice()` to lock in the dilution. The attack is repeatable every update cycle and requires no capital beyond the deposit itself.

## Recommendation
Call `_updateRsETHPrice()` (or `updateRSETHPrice()`) at the start of `_beforeDeposit` in `LRTDepositPool.sol` before reading `rsETHPrice`, ensuring all mint calculations use the freshly computed rate. Apply the same fix to `getExpectedAssetAmount` in `LRTWithdrawalManager.sol` before `initiateWithdrawal` reads `rsETHPrice`, to prevent the symmetric shortfall for withdrawers.

## Proof of Concept
1. At time T, `updateRSETHPrice()` is called. `rsETHPrice` is stored as `1.001e18`.
2. EigenLayer rewards accrue. True rate rises to `1.002e18`; `updateRSETHPrice()` has not been called again.
3. Attacker calls `depositETH{value: 10 ether}()`.
4. `getRsETHAmountToMint` computes: `10e18 * 1e18 / 1.001e18 ≈ 9.990 rsETH` (stale price).
5. Correct amount at true rate: `10e18 * 1e18 / 1.002e18 ≈ 9.980 rsETH`.
6. Attacker receives ~0.010 excess rsETH extracted from existing holders' unreflected yield.
7. Attacker calls `updateRSETHPrice()`. New price reflects the diluted supply; all prior holders now hold rsETH worth slightly less ETH.

**Foundry fork test plan:** Fork mainnet, record `rsETHPrice` and `totalETHInProtocol`, warp forward to simulate reward accrual (or mock `_getTotalEthInProtocol` to return a higher value), call `depositETH`, then call `updateRSETHPrice()` and assert that `rsETHPrice` post-deposit is lower than it would have been had `updateRSETHPrice()` been called before the deposit. Verify the rsETH balance of a pre-existing holder decreased in ETH terms.