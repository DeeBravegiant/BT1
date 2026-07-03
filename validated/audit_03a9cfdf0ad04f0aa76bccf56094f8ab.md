### Title
Unprotected `sendFunds()` Enables Front-Running of MEV Reward Distribution — (File: `contracts/FeeReceiver.sol`)

---

### Summary

`FeeReceiver.sendFunds()` carries no access control, allowing any caller to trigger the flush of accumulated MEV/execution-layer ETH rewards into the `LRTDepositPool` at an arbitrary moment. An attacker can atomically deposit ETH into the pool at the pre-reward rsETH price, call `sendFunds()` to inflate the TVL (and thus the rsETH price), and exit at the higher price — stealing a proportional share of the MEV rewards that should accrue to all existing rsETH holders.

---

### Finding Description

`FeeReceiver` is the protocol's MEV and execution-layer reward sink. Rewards accumulate as raw ETH in the contract over time. The function responsible for forwarding them to the deposit pool is:

```solidity
// contracts/FeeReceiver.sol  line 53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

There is no `onlyRole`, `onlyManager`, or any other guard. Any EOA or contract can call it at any block.

`receiveFromRewardReceiver` deposits the ETH into the pool's TVL. Because the rsETH exchange rate is computed as `totalETHValue / rsETHSupply`, adding ETH to the TVL immediately raises the price of every outstanding rsETH token. An attacker who holds rsETH at the moment `sendFunds()` is called receives a windfall proportional to their share of the supply — at the expense of holders who were already in the protocol before the attacker entered.

Attack path:

1. Attacker observes that `FeeReceiver` has accumulated a meaningful ETH balance.
2. In the same block (or via a private mempool bundle), the attacker calls `LRTDepositPool.depositETH()`, minting rsETH at the current (pre-reward) rate.
3. Attacker immediately calls `FeeReceiver.sendFunds()`, flushing the accumulated rewards into the pool.
4. The rsETH price rises. The attacker's newly minted rsETH is now worth more ETH than they deposited.
5. The attacker initiates a withdrawal and, after the EigenLayer delay, redeems at the inflated price.

The MEV rewards that should have been diluted across all pre-existing rsETH holders are instead partially captured by the attacker.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Every time `sendFunds()` is called, the entire accumulated reward balance is credited to whoever holds rsETH at that instant. An attacker who front-runs the call with a large deposit captures a share of those rewards proportional to `attackerDeposit / (totalTVL + attackerDeposit + rewardBalance)`. For large reward accumulations or small existing TVL, the stolen fraction is material. Existing rsETH holders receive less yield than they are entitled to.

---

### Likelihood Explanation

**Medium.**

The function is unconditionally public. MEV infrastructure (flashbots bundles, private RPCs) makes atomic front-running trivial. The only friction is EigenLayer's withdrawal delay, which prevents same-block profit realisation but does not prevent the yield theft itself — the attacker simply holds rsETH until the delay expires. The attack is profitable whenever the reward balance in `FeeReceiver` is large relative to the attacker's capital cost.

---

### Recommendation

Restrict `sendFunds()` to a trusted role, mirroring the pattern used for every other sensitive function in the codebase:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    ...
}
```

Alternatively, implement a time-lock or minimum-interval between calls so that the reward distribution schedule is predictable and cannot be triggered on demand by an attacker.

---

### Proof of Concept

```
State before attack:
  rsETH supply        = 10 000 rsETH
  Total TVL           = 10 500 ETH  →  price = 1.05 ETH/rsETH
  FeeReceiver balance = 100 ETH (accumulated MEV rewards)

Step 1 – Attacker deposits 1 050 ETH:
  rsETH minted = 1 050 / 1.05 = 1 000 rsETH
  New supply   = 11 000 rsETH,  TVL = 11 550 ETH
  Price unchanged = 1.05 ETH/rsETH

Step 2 – Attacker calls sendFunds():
  TVL += 100 ETH  →  TVL = 11 650 ETH
  New price = 11 650 / 11 000 ≈ 1.05909 ETH/rsETH

Step 3 – Attacker redeems 1 000 rsETH (after withdrawal delay):
  Proceeds = 1 000 × 1.05909 = 1 059.09 ETH
  Profit   = 1 059.09 − 1 050 = 9.09 ETH

  Legitimate holders' share of the 100 ETH reward = 90.91 ETH
  Attacker's stolen share                          =  9.09 ETH  (≈ 9.1 %)
```

The root cause is the missing access control on `sendFunds()` at: [1](#0-0)

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```
