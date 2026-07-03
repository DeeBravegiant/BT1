Audit Report

## Title
Stale `rsETHPrice` Used in Deposit Minting Without Forcing `updateRSETHPrice()` - (File: `contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool.depositETH()` and `depositAsset()` compute the rsETH mint amount using the cached `LRTOracle.rsETHPrice` storage variable without first refreshing it via `updateRSETHPrice()`. Because the stored price lags behind the true current rate whenever rewards have accrued, depositors receive more rsETH than their contribution warrants, diluting the yield that rightfully belongs to existing rsETH holders.

## Finding Description
`LRTOracle` stores the rsETH/ETH exchange rate in the public state variable `rsETHPrice` (LRTOracle.sol:28). This value is only updated when `updateRSETHPrice()` (LRTOracle.sol:87-89) or `updateRSETHPriceAsManager()` is explicitly called; neither is invoked atomically within the deposit flow.

Both `depositETH()` (LRTDepositPool.sol:87) and `depositAsset()` (LRTDepositPool.sol:111) delegate to `_beforeDeposit()`, which is declared `private view` (LRTDepositPool.sol:648-655) and therefore cannot call any state-mutating function. `_beforeDeposit` calls `getRsETHAmountToMint()` (LRTDepositPool.sol:665), which computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
// LRTDepositPool.sol:520
```

`lrtOracle.rsETHPrice()` returns the **cached** storage value. The true current price, computed in `_updateRsETHPrice()` as `(totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)` (LRTOracle.sol:250), is never consulted during a deposit.

As EigenLayer restaking rewards and LST yield accrue, the true rsETH/ETH rate rises above the stored `rsETHPrice`. The division by a stale, lower denominator mints more rsETH than the depositor's ETH contribution justifies. When `updateRSETHPrice()` is eventually called, the new price is computed against a supply inflated by the excess minted tokens, permanently reducing the per-token ETH claim of all pre-existing holders.

An additional aggravating factor: `_updateRsETHPrice()` enforces a `pricePercentageLimit` guard (LRTOracle.sol:252-266) that causes the public `updateRSETHPrice()` to revert with `PriceAboveDailyThreshold` for non-managers when the price increase exceeds the threshold. This means that after a large reward accrual, only a manager can refresh the price, extending the window during which deposits exploit the stale rate.

## Impact Explanation
**High — Theft of unclaimed yield.**

Yield accrued by existing rsETH holders is embedded in the rising true rsETH/ETH price. A depositor who deposits while `rsETHPrice` is stale receives excess rsETH tokens representing a claim on TVL earned before their deposit. When the price is subsequently updated, the inflated supply causes the settled price to be lower than it would have been, permanently transferring a portion of pre-existing holders' yield to the late depositor. The magnitude scales with (a) elapsed time since the last price update and (b) deposit size. This matches the allowed impact "High. Theft of unclaimed yield."

## Likelihood Explanation
`updateRSETHPrice()` is a separate, permissionless transaction with no on-chain enforcement that it precede a deposit. In normal operation, price updates are driven by off-chain keepers or manual manager calls, making stale-price windows routine. Any unprivileged depositor — including an automated bot — can observe the mempool or simply deposit in any block where the price has not been refreshed, triggering the condition without any special access or coordination. The exploit is repeatable on every deposit in every block where `rsETHPrice` lags the true rate.

## Recommendation
Call `updateRSETHPrice()` at the start of `depositETH()` and `depositAsset()`, before `_beforeDeposit` is invoked:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice(); // add
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
```

Apply the same change to `depositAsset()`. Alternatively, change `_beforeDeposit` from `private view` to `private` and call `_updateRsETHPrice()` internally before computing the mint amount.

## Proof of Concept
1. At T₀, `updateRSETHPrice()` is called. Stored `rsETHPrice = 1.04e18`.
2. EigenLayer rewards accrue. True price rises to `1.05e18`; stored value remains `1.04e18`.
3. Attacker calls `depositETH{value: 10 ether}(0, "")`.
4. `getRsETHAmountToMint` computes: `10e18 * 1e18 / 1.04e18 ≈ 9.615 rsETH`.
5. Fair amount at true price: `10e18 * 1e18 / 1.05e18 ≈ 9.524 rsETH`.
6. Attacker receives ~0.091 excess rsETH, representing ~0.095 ETH of TVL earned by existing holders.
7. When `updateRSETHPrice()` is later called, the settled price reflects the inflated supply, permanently reducing existing holders' per-token ETH claim.

**Foundry fork test plan:** Fork mainnet, snapshot state after a reward accrual event (or simulate by directly increasing `getTotalAssetDeposits` return values), record existing holder balances, call `depositETH` without first calling `updateRSETHPrice`, then call `updateRSETHPrice` and assert that existing holders' ETH-equivalent value (balance × new price) is lower than it would have been had the correct mint amount been used.