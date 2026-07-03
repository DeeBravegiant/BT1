Audit Report

## Title
Stale `rsETHPrice` Used in Mint Calculation Without Prior Oracle Update — (File: `contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool.depositETH()` and `depositAsset()` compute the rsETH mint amount using `LRTOracle.rsETHPrice`, a stored state variable that is only written when `updateRSETHPrice()` is explicitly called. Neither deposit function triggers a price update before minting. During any staleness window — which is always non-zero and can be extended by the `pricePercentageLimit` guard — new depositors receive more rsETH than the true current price warrants, diluting existing holders' accrued yield.

## Finding Description
`LRTOracle.rsETHPrice` is a plain storage variable:

```solidity
// contracts/LRTOracle.sol:28
uint256 public override rsETHPrice;
```

It is only written inside `_updateRsETHPrice()`, which is invoked by `updateRSETHPrice()` and `updateRSETHPriceAsManager()`. A grep across all production Solidity files confirms `updateRSETHPrice` appears exclusively in `LRTOracle.sol` — it is never called from `LRTDepositPool` or any other contract in the deposit path.

The mint-amount calculation reads this stored value directly:

```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

This line is reached on every user deposit via:

`depositETH()` / `depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` → `lrtOracle.rsETHPrice()`

None of these steps call `updateRSETHPrice()` first. As EigenLayer staking rewards and LST rebases accrue, the true TVL grows while `rsETHPrice` remains frozen at its last stored value. Any depositor who transacts during this staleness window receives:

```
rsethAmountToMint = depositValue / stalePrice  >  depositValue / truePrice
```

The excess rsETH minted to the new depositor represents a direct dilution of existing holders' proportional claim on the protocol TVL.

Additionally, the `pricePercentageLimit` guard inside `_updateRsETHPrice()` reverts calls from non-manager accounts when the accumulated price increase exceeds the configured threshold:

```solidity
// contracts/LRTOracle.sol:260-265
if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        revert PriceAboveDailyThreshold();
    }
}
```

This means that during periods of high reward accrual, the public `updateRSETHPrice()` call reverts for ordinary users, extending the staleness window and amplifying the exploitable gap. The existing `minRSETHAmountExpected` slippage guard in `_beforeDeposit()` does not protect against this — it only prevents the depositor from receiving *less* than expected, not *more*.

## Impact Explanation
Every deposit made while `rsETHPrice` is stale mints excess rsETH. The excess is funded by diluting the proportional claim of all existing rsETH holders on the protocol TVL. The accrued staking yield that should belong to existing holders is instead partially captured by the new depositor. This constitutes **theft of unclaimed yield** from existing rsETH holders, matching the allowed High impact scope.

## Likelihood Explanation
`rsETHPrice` is updated off-chain by a keeper. There is always a non-zero window between consecutive updates (at minimum one block, in practice minutes to hours). The `pricePercentageLimit` mechanism can extend this window further by blocking public updates during high-reward periods. Any depositor — including a sophisticated actor who monitors the mempool for pending oracle updates and front-runs them — can exploit this gap. No special role or privileged access is required; the entry point is the public `depositETH()` / `depositAsset()` functions. Likelihood: **Medium**.

## Recommendation
Call `updateRSETHPrice()` at the start of `depositETH()` and `depositAsset()` before the mint amount is computed:

```solidity
function depositETH(...) external payable nonReentrant whenNotPaused ... {
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice(); // add this
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
```

Apply the same fix to `depositAsset()`. If the `pricePercentageLimit` guard is a concern for the deposit path (i.e., it may revert during high-reward periods), a dedicated internal update function that uses the manager path or bypasses the threshold revert should be used instead.

## Proof of Concept
1. At time T₀, `updateRSETHPrice()` is called; `rsETHPrice` is stored as `1.001e18`.
2. Staking rewards accrue over the next N blocks; the true TVL-per-rsETH rises to `1.002e18`.
3. At time T₁ (before the next oracle update), Alice calls `depositETH{value: 1 ether}(0, "")`.
4. `getRsETHAmountToMint(ETH, 1e18)` computes: `1e18 * 1e18 / 1.001e18 ≈ 0.999001 rsETH` — but the fair amount at the true price would be `1e18 / 1.002e18 ≈ 0.998004 rsETH`.
5. Alice receives ~0.000997 rsETH more than she is entitled to, funded by diluting existing holders.
6. If `pricePercentageLimit` is set and the accumulated increase exceeds it, public calls to `updateRSETHPrice()` revert, keeping the price stale for longer and increasing the per-deposit excess.

**Foundry fork test plan:**
- Fork mainnet at a block where `rsETHPrice` was last updated.
- Advance the block timestamp by several hours (simulating reward accrual).
- Call `depositETH{value: 1 ether}()` without calling `updateRSETHPrice()` first.
- Assert that the rsETH minted exceeds `1e18 / truePrice` (computed via `_getTotalEthInProtocol() / rsethSupply`).
- Optionally, set `pricePercentageLimit` to a small value and confirm that `updateRSETHPrice()` reverts for non-manager callers, while deposits still succeed with the stale price.