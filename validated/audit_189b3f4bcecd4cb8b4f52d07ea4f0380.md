Audit Report

## Title
Permissionless `updateRSETHPrice()` Enables Sandwich Attack to Steal Accrued Yield from rsETH Holders — (`contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is callable by any address with no access control beyond `whenNotPaused`. Because `LRTDepositPool.getRsETHAmountToMint()` prices deposits against the stored (potentially stale) `rsETHPrice`, an attacker can atomically deposit at the stale lower price to receive excess rsETH, then call `updateRSETHPrice()` to crystallise the true higher price. The attacker captures a fraction of the yield increment that should have accrued exclusively to pre-existing rsETH holders.

## Finding Description

`updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard:

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Every deposit is priced against the stored `rsETHPrice`:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

When rewards accrue between price updates, `rsETHPrice` is stale (lower than the true per-share ETH value). A depositor at this moment receives more rsETH than the true exchange rate warrants.

Inside `_updateRsETHPrice()`, `previousTVL` is computed as:

```solidity
// contracts/LRTOracle.sol L234
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

After the attacker's deposit, `rsethSupply` already includes the attacker's freshly minted tokens. So `previousTVL = (S + A/P_old) * P_old = T_old + A`, and `rewardAmount = totalETHInProtocol - previousTVL = T_true - T_old` — the correct reward amount. However, the new price is then:

```solidity
// contracts/LRTOracle.sol L250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
// = (T_true + A - fee) / (S + A/P_old)
```

Because the attacker's deposit dilutes the denominator with excess rsETH (minted at the stale price), `newRsETHPrice` is lower than the true `(T_true - fee) / S`. Existing holders' rsETH is worth less than it would have been without the attack.

**The `pricePercentageLimit` guard does not block this.** It only reverts if `newRsETHPrice > highestRsethPrice` by more than the configured threshold. In the attack scenario, the attacker's deposit actually suppresses the new price below the true price, so the increase from `highestRsethPrice` is small (e.g., 0.09% for a 10 ETH reward window against 1,000 ETH TVL), well within any realistic daily limit. The public path succeeds unconditionally for routine reward windows.

**Attack steps (atomic, single transaction):**
1. Observe `_getTotalEthInProtocol() > rsethSupply * rsETHPrice` (rewards have accrued).
2. Call `LRTDepositPool.depositETH{value: A}(0, "")` — receive `A / P_old` rsETH at the stale price.
3. Call `LRTOracle.updateRSETHPrice()` — price updates to `(T_true + A - fee) / (S + A/P_old)`.
4. Attacker's rsETH is worth more than `A`; existing holders receive proportionally less yield.

## Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders are entitled to the full reward increment `(T_true − T_old − fee)`. The attacker intercepts a fraction proportional to their excess rsETH relative to the post-deposit supply. Using the submitted example (10 ETH reward window, 10,000 ETH attacker deposit, 1,000 ETH pre-existing TVL, zero fee):

- Without attack: existing holders' 1,000 rsETH → 1,010 ETH (price = 1.010).
- With attack: existing holders' 1,000 rsETH → 1,000.909 ETH (price = 1.000909).
- Attacker's 10,000 rsETH → 10,009.09 ETH — a risk-free gain of ≈ 9.09 ETH stolen from existing holders.

This matches the allowed impact "High — Theft of unclaimed yield."

## Likelihood Explanation

**Medium.** Reward accrual is continuous and predictable. Any on-chain actor can read `rsETHPrice` and `_getTotalEthInProtocol()` to determine the current stale gap. No privileged access, leaked keys, or governance capture is required. The attack is executable by any EOA or contract and can be batched atomically in a single transaction, eliminating execution risk. The only practical constraints are gas cost and the size of the reward window, both of which are easily modelled off-chain. The attack is repeatable every reward accrual cycle.

## Recommendation

1. **Restrict `updateRSETHPrice()`** to authorised callers (e.g., `onlyLRTManager` or a dedicated keeper role), mirroring the already-existing `updateRSETHPriceAsManager()`. The public entry point provides no security benefit that the manager path does not already cover.
2. **Alternatively**, record a `lastUpdateTimestamp` and reject deposits made in the same block as a price update (or vice-versa) to break atomicity.
3. **Alternatively**, use a commit-delay or time-weighted mechanism so the price used for minting lags behind the price used for accounting, eliminating the sandwich window.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

interface IDepositPool {
    function depositETH(uint256 minRSETH, string calldata ref) external payable;
}
interface IOracle {
    function updateRSETHPrice() external;
}

contract SandwichYield {
    IDepositPool immutable pool;
    IOracle      immutable oracle;

    constructor(address _pool, address _oracle) {
        pool   = IDepositPool(_pool);
        oracle = IOracle(_oracle);
    }

    function attack() external payable {
        // Step 1: deposit at stale (lower) rsETHPrice → receive excess rsETH
        pool.depositETH{value: msg.value}(0, "");
        // Step 2: update price → attacker's rsETH is now worth more than msg.value
        oracle.updateRSETHPrice();
    }
}
```

**Foundry fork test plan:**
1. Fork mainnet at a block where `rsETHPrice` is stale (i.e., `_getTotalEthInProtocol() > rsethSupply * rsETHPrice`).
2. Deploy `SandwichYield` pointing at the live `LRTDepositPool` and `LRTOracle`.
3. Call `attack{value: 10_000 ether}()`.
4. Assert `rsETH.balanceOf(attacker) * lrtOracle.rsETHPrice() > 10_000 ether` after the call.
5. Assert existing holders' rsETH value decreased relative to the pre-attack true price.