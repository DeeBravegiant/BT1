### Title
Attacker Can Sandwich `FeeReceiver.sendFunds()` to Steal MEV/EL Rewards from rsETH Holders - (`contracts/FeeReceiver.sol` / `contracts/LRTOracle.sol`)

---

### Summary

Both `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` are permissionless. rsETH minting uses the **stale stored** `rsETHPrice`, not a live TVL calculation. This allows an attacker to deposit at the pre-reward price, trigger the reward distribution and price update themselves, and exit with a disproportionate share of MEV/EL rewards that should belong to existing rsETH holders.

---

### Finding Description

The `FeeReceiver` contract accumulates MEV and execution-layer rewards. Its `sendFunds()` function has no access control — any caller can push the accumulated ETH into `LRTDepositPool`:

```solidity
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [1](#0-0) 

Similarly, `LRTOracle.updateRSETHPrice()` is public with no role restriction:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

When `_updateRsETHPrice()` runs, it computes `totalETHInProtocol` (which now includes the newly deposited rewards) against `previousTVL = rsethSupply * rsETHPrice` (the stale stored price). The difference is treated as yield, and the new price is set higher:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [3](#0-2) 

Crucially, rsETH minting uses the **stored** `rsETHPrice`, not a live calculation:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

This means an attacker can deposit at the stale price **before** rewards are reflected, then trigger the price update to capture a share of the yield.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders earn MEV/EL rewards proportional to their holdings. An attacker who deposits just before the reward distribution and price update captures a share of those rewards without having contributed to the protocol during the period those rewards were earned. The attacker's rsETH is immediately worth more than what they paid, and they can sell on secondary markets or wait for the withdrawal delay to exit.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. Monitoring `FeeReceiver` for accumulated ETH (trivially observable on-chain).
2. Calling two permissionless functions in sequence.
3. The price increase must stay within `pricePercentageLimit` to avoid reverting for non-managers — but this limit may be unset (defaults to 0, disabling the check), and even when set, the attacker can target smaller reward accumulations or split across multiple blocks.

No flash loan is strictly required (the attacker can use their own capital), and no privileged access is needed.

---

### Recommendation

1. **Restrict `FeeReceiver.sendFunds()`** to a privileged role (e.g., `MANAGER` or `OPERATOR`) so the timing of reward distribution cannot be controlled by an attacker.
2. **Alternatively**, update `rsETHPrice` atomically inside `depositETH`/`depositAsset` before computing the mint amount, so deposits always use the live TVL rather than a stale stored price.
3. Consider a deposit lock or minimum holding period to prevent same-block deposit-and-exit patterns.

---

### Proof of Concept

**Setup**: FeeReceiver holds 10 ETH in accumulated MEV rewards. Current rsETH TVL = 1000 ETH, rsETH supply = 1000, stored `rsETHPrice` = 1.0 ETH/rsETH.

**Attack**:
1. Attacker calls `LRTDepositPool.depositETH{value: 1000 ETH}(...)`.
   - Minted rsETH = `1000 ETH / 1.0` = **1000 rsETH** (at stale price).
   - New supply = 2000 rsETH, TVL = 2000 ETH (FeeReceiver rewards not yet counted).

2. Attacker calls `FeeReceiver.sendFunds()`.
   - 10 ETH moves to `LRTDepositPool`. TVL becomes 2010 ETH.

3. Attacker calls `LRTOracle.updateRSETHPrice()`.
   - `previousTVL` = 2000 rsETH × 1.0 = 2000 ETH.
   - `rewardAmount` = 2010 − 2000 = 10 ETH.
   - After protocol fee (say 10%): `protocolFeeInETH` = 1 ETH.
   - `newRsETHPrice` = (2010 − 1) / 2000 = **1.00450 ETH/rsETH**.

4. Attacker's 1000 rsETH is now worth **1004.5 ETH** — they paid 1000 ETH and captured ~4.5 ETH of the 9 ETH net yield (50%), despite having been in the protocol for zero time.

Existing holders who earned those rewards over the entire period only receive the other ~4.5 ETH instead of the full 9 ETH.

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
