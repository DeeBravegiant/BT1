Audit Report

## Title
Stale `rsETHPrice` Enables Permissionless Sandwich Attack to Steal Yield from Existing Stakers - (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle.rsETHPrice` is a stored state variable updated only on explicit calls to `updateRSETHPrice()`, which is a permissionless `public` function. Because `FeeReceiver.sendFunds()` is also permissionless, an attacker can deposit at the stale (lower) price, atomically push accumulated rewards into the TVL, trigger a price update, and exit via DEX at the new higher price — extracting yield that belongs to pre-existing stakers.

## Finding Description
`LRTOracle.updateRSETHPrice()` carries no access control:

```solidity
// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`LRTDepositPool.getRsETHAmountToMint()` divides by the stored `rsETHPrice` state variable, not a freshly computed price:

```solidity
// LRTDepositPool.sol L519-520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`FeeReceiver.sendFunds()` is also permissionless — any caller can push accumulated MEV/staking rewards from `FeeReceiver` into the deposit pool:

```solidity
// FeeReceiver.sol L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Inside `_updateRsETHPrice()`, `previousTVL` is computed using the **current** `rsethSupply` (which already includes the attacker's freshly minted tokens) multiplied by the **stale** stored price:

```solidity
// LRTOracle.sol L234
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

This means the attacker's deposit is treated as if it was always part of the pool at the old price, so the entire reward increment (`totalETHInProtocol - previousTVL`) is shared pro-rata with the attacker's position, diluting the yield of pre-existing stakers.

The `pricePercentageLimit` guard at L252-266 is only active when `pricePercentageLimit > 0`; it defaults to `0` (Solidity default), so it provides no protection in the default configuration. The `maxFeeMintAmountPerDay` guard can also be bypassed if set to a sufficiently large value or if the fee amount is zero.

## Impact Explanation
**High — Theft of unclaimed yield.** Pre-existing stakers lose a proportional share of pending rewards to the attacker on every price-update cycle. In the concrete PoC below, original stakers lose 50% of their expected yield in a single atomic transaction. The attack is repeatable every time rewards accumulate in `FeeReceiver` before `updateRSETHPrice()` is called, which is the normal operating cadence of the protocol.

## Likelihood Explanation
**Medium.** All required conditions are routinely satisfied on mainnet: (1) MEV/staking rewards accumulate in `FeeReceiver` continuously; (2) `rsETHPrice` is updated periodically, not per-block; (3) rsETH has active secondary market DEX liquidity; (4) flash loan capital is universally available. The attack is fully permissionless, requires no privileged access, and is automatable by MEV bots monitoring the mempool for `sendFunds()` calls or monitoring `FeeReceiver.balance`.

## Recommendation
Call `updateRSETHPrice()` (or an equivalent internal price snapshot) at the start of `depositETH()` and `depositAsset()` in `LRTDepositPool`, so the minting price always reflects current TVL including any pending rewards. Alternatively, restrict `FeeReceiver.sendFunds()` to a trusted role (e.g., `MANAGER`) so rewards can only be pushed atomically with a price update by an authorized operator.

## Proof of Concept
**Setup:**
- `rsETHPrice` = 1.0 ETH/rsETH (stored, stale)
- `rsethSupply` = 1,000 rsETH
- `totalETHInProtocol` = 1,000 ETH
- `FeeReceiver` holds 100 ETH in accumulated rewards (not yet sent)
- `pricePercentageLimit` = 0 (default — guard disabled)

**Attack (single transaction via flash loan):**

1. Attacker flash-loans 1,000 ETH.
2. Attacker calls `LRTDepositPool.depositETH{value: 1000 ETH}(0, "")`.
   - `rsethAmountToMint = 1000e18 * 1e18 / 1e18 = 1,000 rsETH` (at stale price 1.0).
   - `rsethSupply` → 2,000 rsETH; deposit pool ETH → 2,000 ETH.
3. Attacker calls `FeeReceiver.sendFunds()`.
   - 100 ETH moves to deposit pool; `totalETHInProtocol` → 2,100 ETH.
4. Attacker calls `LRTOracle.updateRSETHPrice()`.
   - `rsethSupply` = 2,000 (includes attacker's tokens)
   - `previousTVL` = 2,000 × 1.0 = 2,000 ETH
   - `rewardAmount` = 2,100 − 2,000 = 100 ETH
   - `protocolFeeInETH` = 100 × 10% = 10 ETH (example fee)
   - `newRsETHPrice` = (2,100 − 10) / 2,000 = **1.045 ETH/rsETH**
5. Attacker sells 1,000 rsETH on DEX at 1.045 ETH/rsETH → receives **1,045 ETH**.
6. Attacker repays 1,000 ETH flash loan. **Net profit: 45 ETH.**

**Without the attack**, original stakers (1,000 rsETH) would have received 90 ETH of yield (100 ETH reward minus 10 ETH fee), raising their price to 1.09 ETH/rsETH. With the attack, they only receive 45 ETH of yield (price 1.045 ETH/rsETH). **The attacker extracted 45 ETH — 50% of the yield belonging to legitimate stakers.**