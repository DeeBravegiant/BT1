Audit Report

## Title
Stale `rsETHPrice` Enables Permissionless Yield Dilution via Sandwich of `FeeReceiver.sendFunds()` and `updateRSETHPrice()` - (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle.rsETHPrice` is a stored state variable updated only on explicit calls to the permissionless `updateRSETHPrice()`. Because `LRTDepositPool.depositETH()` mints rsETH using this stale stored price, and both `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` are callable by anyone, an attacker can deposit at the stale lower price, push accumulated rewards into the pool, trigger a price update, and exit at the new higher price — extracting yield that belongs to pre-existing stakers.

## Finding Description

`LRTOracle.rsETHPrice` is a state variable written only inside `_updateRsETHPrice()`:

```solidity
// LRTOracle.sol L313
rsETHPrice = newRsETHPrice;
```

`updateRSETHPrice()` is declared `public whenNotPaused` with no role restriction, making it callable by any external account:

```solidity
// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`LRTDepositPool.getRsETHAmountToMint()` divides by the stored `rsETHPrice` to compute how many tokens to mint:

```solidity
// LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

If `rsETHPrice` has not been updated since rewards arrived, a depositor receives more rsETH than the current backing warrants.

`FeeReceiver.sendFunds()` is also fully permissionless:

```solidity
// FeeReceiver.sol L53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Inside `_updateRsETHPrice()`, `previousTVL` is computed as the **current** (post-deposit) `rsethSupply` multiplied by the **stale** `rsETHPrice`:

```solidity
// LRTOracle.sol L216, L234
uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();
// ...
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

Because the attacker's freshly minted rsETH is already included in `rsethSupply`, the reward increment (`totalETHInProtocol - previousTVL`) is attributed to the reward ETH, but the attacker's position participates in the resulting price appreciation, diluting the yield of pre-existing stakers.

**The `pricePercentageLimit` guard** (LRTOracle.sol L256-257) is a partial mitigation: if `pricePercentageLimit > 0` and the price jump exceeds the threshold, a non-manager call to `updateRSETHPrice()` reverts. However, this guard is entirely bypassed when `pricePercentageLimit == 0` (the condition `pricePercentageLimit > 0` short-circuits to false), and even when set, the attacker can scale the deposit size down so the resulting price increase stays within the configured limit, preserving profitability at reduced scale.

**The `maxFeeMintAmountPerDay` guard** (LRTOracle.sol L205-206) can similarly be circumvented by scaling the attack to keep the fee-mint amount within the daily cap.

## Impact Explanation

**High — Theft of unclaimed yield.** Pre-existing stakers lose a proportional share of pending rewards to the attacker on every attack iteration. In the PoC below, original stakers lose 50% of their expected yield in a single transaction. The attack is repeatable every time rewards accumulate in `FeeReceiver` before `updateRSETHPrice()` is called, which is the normal operating cadence of the protocol.

## Likelihood Explanation

**Medium.** All required conditions are routinely satisfied on mainnet: (1) MEV/staking rewards accumulate in `FeeReceiver` continuously between periodic price updates; (2) `rsETHPrice` is not updated per-block; (3) rsETH has active secondary market liquidity; (4) flash loan capital is universally available. The attack is fully permissionless, requires no privileged access, and is automatable by MEV bots. SECURITY.md explicitly states flash-loan attacks are not excluded from scope.

## Recommendation

Call `updateRSETHPrice()` (or an equivalent internal price snapshot) at the start of `depositETH()` and `depositAsset()` in `LRTDepositPool`, so the price used for minting always reflects the current TVL including any pending rewards. Alternatively, restrict `FeeReceiver.sendFunds()` to a trusted role and require that the caller also atomically updates the oracle price, preventing the permissionless sequencing that enables the attack.

## Proof of Concept

**Setup:**
- `rsETHPrice` = 1.0 ETH/rsETH (stored, stale)
- `rsethSupply` = 1,000 rsETH
- `totalETHInProtocol` = 1,000 ETH (price is accurate)
- `FeeReceiver` holds 100 ETH in accumulated MEV rewards (not yet sent)

**Attack (single transaction via flash loan):**

1. Attacker flash-loans 1,000 ETH.
2. Attacker calls `LRTDepositPool.depositETH{value: 1000 ETH}(0, "")`.
   - `rsethAmountToMint = 1000e18 * 1e18 / 1e18 = 1000 rsETH` (at stale price 1.0).
   - `rsethSupply` → 2,000 rsETH; deposit pool ETH → 2,000 ETH.
3. Attacker calls `FeeReceiver.sendFunds()`.
   - 100 ETH moves to deposit pool; `totalETHInProtocol` → 2,100 ETH.
4. Attacker calls `LRTOracle.updateRSETHPrice()`.
   - `rsethSupply` = 2,000 (includes attacker's tokens).
   - `previousTVL = 2000 × 1.0 = 2000 ETH`.
   - `rewardAmount = 2100 − 2000 = 100 ETH`.
   - `protocolFeeInETH = 100 × 10% = 10 ETH`.
   - `newRsETHPrice = (2100 − 10) / 2000 = 1.045 ETH/rsETH`.
5. Attacker sells 1,000 rsETH on a DEX at ≈1.045 ETH/rsETH → receives **1,045 ETH**.
6. Attacker repays 1,000 ETH flash loan. **Net profit: 45 ETH.**

Without the attack, original stakers (1,000 rsETH) would have received 90 ETH of yield (price → 1.09 ETH/rsETH). With the attack, they receive only 45 ETH (price → 1.045 ETH/rsETH). **The attacker extracted 45 ETH of yield belonging to legitimate stakers.**

**Foundry fork test plan:** Deploy against a mainnet fork; seed `FeeReceiver` with ETH; call `depositETH` → `sendFunds` → `updateRSETHPrice` in sequence from an unprivileged address; assert that `rsETHPrice` increased and that the attacker's rsETH balance × new price exceeds the deposited ETH, while original staker yield per rsETH is lower than it would have been without the attack.