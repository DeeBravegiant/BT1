Audit Report

## Title
Donation to EigenLayer Strategy Inflates TVL, Triggering Unearned Protocol Fee Minting — (`contracts/NodeDelegatorHelper.sol`, `contracts/LRTOracle.sol`)

## Summary
`NodeDelegatorHelper.getAssetBalance` converts the NDC's withdrawable shares to underlying tokens via `IStrategy.sharesToUnderlyingView`, which in EigenLayer's `StrategyBase` is computed from the strategy's live token balance. An attacker can donate underlying tokens directly to the strategy address, inflating the per-share rate without changing share counts. Because `updateRSETHPrice()` is public and no sanity bound is applied to the returned value, the inflated TVL is treated as genuine yield, and the protocol mints excess fee rsETH to the treasury at the expense of existing rsETH holders.

## Finding Description

**Root cause — `getAssetBalance` trusts the live strategy balance unconditionally**

`NodeDelegatorHelper.getAssetBalance` fetches the NDC's withdrawable shares from `DelegationManager.getWithdrawableShares`, then passes them directly to `IStrategy.sharesToUnderlyingView`:

```solidity
// contracts/NodeDelegatorHelper.sol L31-39
function getAssetBalance(ILRTConfig lrtConfig, address asset) internal view returns (uint256) {
    address strategy = lrtConfig.assetStrategy(asset);
    if (strategy == address(0)) { return 0; }
    uint256 withdrawableShare = getWithdrawableShare(lrtConfig, IStrategy(strategy));
    return IStrategy(strategy).sharesToUnderlyingView(withdrawableShare);
}
```

EigenLayer's `StrategyBase.sharesToUnderlyingView` computes the exchange rate from `underlyingToken.balanceOf(strategy)`. Donating tokens directly to the strategy address increases `balanceOf` without changing `totalShares`, inflating the per-share value. EigenLayer's virtual-shares mechanism (`VIRTUAL_SHARE_AMOUNT = 1e3`) raises the cost of the attack but does not prevent it for donations that are large relative to the existing TVL.

**Inflated value propagates to TVL accounting**

`getAssetDistributionData` accumulates `getAssetBalance` across all NDCs into `assetStakedInEigenLayer`:

```solidity
// contracts/LRTDepositPool.sol L450
assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
```

`getTotalAssetDeposits` sums all components including `assetStakedInEigenLayer`, and `_getTotalEthInProtocol` multiplies each asset total by its oracle price to produce `totalETHInProtocol`.

**Oracle treats the inflation as yield and mints fee rsETH**

```solidity
// contracts/LRTOracle.sol L244-246
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
```

The delta is treated as `rewardAmount`, a fee is computed, and rsETH is minted to the treasury:

```solidity
// contracts/LRTOracle.sol L301-307
uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
if (rsethAmountToMintAsProtocolFee > 0) {
    IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
}
```

**`updateRSETHPrice()` is public — no role required**

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Any EOA can trigger the fee-minting path after donating.

**Existing guards are insufficient**

- `pricePercentageLimit`: Only activates when `pricePercentageLimit > 0` AND the price increase exceeds the threshold. If the parameter is unset (`== 0`) or the donation is sized to stay within the threshold, the guard is fully bypassed. An attacker can split donations across multiple calls to stay under the limit each time.
- `maxFeeMintAmountPerDay`: Caps per-day fee minting but does not prevent the attack; it only bounds the per-day impact and resets each 24-hour period.

## Impact Explanation

**High — Theft of unclaimed yield.** The donated tokens should accrue entirely to rsETH holders as an increase in rsETH price. Instead, `protocolFeeInBPS / 10_000` of the donation value is siphoned to the treasury as minted rsETH. Existing rsETH holders bear the dilution: their proportional claim on protocol assets is permanently reduced by the fee amount. The treasury receives rsETH it is not entitled to. This is a concrete, irreversible transfer of value from rsETH holders to the treasury, matching the "Theft of unclaimed yield" impact class.

## Likelihood Explanation

**Low.** The attacker must sacrifice the donated tokens permanently; the attack is not self-profitable. Realistic motivations include a large short position on rsETH or a competitor willing to absorb the cost. The attack is repeatable (once per day up to `maxFeeMintAmountPerDay`, or continuously if that limit is unset). The `pricePercentageLimit` guard, if set to a non-zero value, forces the attacker to use smaller donations per call, increasing total cost but not preventing the attack. If `pricePercentageLimit == 0`, there is no per-call size constraint.

## Recommendation

1. **Track deposited shares internally.** Record the number of strategy shares held at deposit time and convert to underlying using a trusted external price oracle rather than the live `sharesToUnderlyingView` rate. This decouples TVL accounting from the strategy's token balance.
2. **Alternatively**, compare `sharesToUnderlyingView` against an independent price oracle before computing fees; revert or skip fee minting if the deviation exceeds a configurable threshold.
3. **Ensure `pricePercentageLimit` is always set to a non-zero value** in deployment configuration and governance procedures, so that large single-call donations are blocked for unprivileged callers.
4. **Consider access-controlling `updateRSETHPrice()`** or adding a minimum time delay between calls to limit the frequency of fee-minting triggers.

## Proof of Concept

```solidity
// Mainnet fork test — no public-mainnet state changes
function testDonationInflatesProtocolFee() public {
    // 1. Record baseline treasury balance
    uint256 treasuryBefore = rsETH.balanceOf(treasury);

    // 2. Attacker donates stETH directly to the EigenLayer stETH strategy
    //    (permissionless — no role required)
    address strategy = lrtConfig.assetStrategy(stETH);
    uint256 currentBal = IERC20(stETH).balanceOf(strategy);
    deal(stETH, strategy, currentBal + 500 ether); // sized to stay within pricePercentageLimit

    // 3. Anyone calls the public updateRSETHPrice
    lrtOracle.updateRSETHPrice();

    // 4. Treasury received excess rsETH beyond what genuine yield justifies
    uint256 treasuryAfter = rsETH.balanceOf(treasury);
    assertGt(treasuryAfter, treasuryBefore,
        "treasury minted excess rsETH from donation");

    // 5. rsETH price increased less than it should have (fee was extracted)
    //    Verify: newPrice < (totalETHInProtocol / rsethSupply) by feeRate%
}
```

The test donates tokens directly to the strategy address (no privileged access required), calls the public `updateRSETHPrice`, and asserts that the treasury received rsETH representing a fee on the donation — confirming that rsETH holders were diluted by an amount they should have received in full.