### Title
Unprotected `FeeReceiver::sendFunds` Allows Any Caller to Trigger MEV Reward Flush, Enabling Yield Theft from Existing rsETH Holders via Deposit Front-Running - (File: contracts/FeeReceiver.sol)

---

### Summary

`FeeReceiver.sendFunds()` carries no access control and `LRTOracle.updateRSETHPrice()` is a public function. An unprivileged attacker can atomically (1) deposit ETH at the stale, pre-reward rsETH price, (2) call `sendFunds()` to flush accumulated MEV/EL rewards into the deposit pool, and (3) call `updateRSETHPrice()` to revalue rsETH upward — capturing a proportional share of rewards they did not earn, at the direct expense of existing rsETH holders.

---

### Finding Description

`FeeReceiver` accumulates MEV and execution-layer rewards passively. Its only outbound function is:

```solidity
// contracts/FeeReceiver.sol:53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

There is no `onlyRole`, `onlyLRTManager`, or any other modifier — any EOA or contract can call it. [1](#0-0) 

`LRTOracle.updateRSETHPrice()` is equally unrestricted:

```solidity
// contracts/LRTOracle.sol:87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

`_updateRsETHPrice()` computes the new price as `(totalETHInProtocol - protocolFee) / rsethSupply` and stores it in `rsETHPrice`. [3](#0-2) 

`LRTDepositPool.depositETH()` mints rsETH using the **stored** `rsETHPrice` (not a live recalculation):

```solidity
// contracts/LRTDepositPool.sol:506-521
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

**Attack sequence (executable atomically in one transaction):**

1. Observe that `FeeReceiver` holds a significant accumulated ETH balance (MEV rewards).
2. Call `LRTDepositPool.depositETH()` — minting rsETH at the current stale price (before rewards are counted).
3. Call `FeeReceiver.sendFunds()` — flushing the accumulated rewards into the deposit pool, increasing `totalETHInProtocol`.
4. Call `LRTOracle.updateRSETHPrice()` — recomputing rsETH price upward over the now-larger ETH base.
5. The attacker's rsETH, minted at the old lower price, is now worth more ETH than deposited. The yield stolen equals `MEV_rewards × X / (totalETH + X)` where `X` is the attacker's deposit.

Existing rsETH holders receive a proportionally smaller share of the MEV rewards than they would have without the attacker's deposit.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders earn MEV/EL rewards through the `FeeReceiver` mechanism. An attacker who deposits just before triggering `sendFunds()` + `updateRSETHPrice()` dilutes those rewards, extracting `MEV_rewards × X / (totalETH + X)` in ETH value from legitimate holders. The attacker can exit via secondary-market rsETH sales (e.g., Curve/Balancer pools) without waiting for the withdrawal queue.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` only reverts if the price increase exceeds the configured threshold and the caller is not a manager. [5](#0-4)  If `pricePercentageLimit == 0` (unset) or the accumulated rewards are within the limit, the attack succeeds unconditionally.

---

### Likelihood Explanation

**High.** Both `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` are callable by any address with no preconditions. The attacker does not need to front-run any specific transaction — they can initiate the entire sequence themselves. MEV rewards accumulate continuously, so the opportunity recurs every time the `FeeReceiver` balance is non-trivial. No special role, key, or governance action is required.

---

### Recommendation

1. **Restrict `FeeReceiver.sendFunds()`** to a trusted role (e.g., `LRTConstants.MANAGER` or a dedicated `REWARD_SENDER_ROLE`) so that only authorized callers can flush rewards into the deposit pool.
2. **Alternatively**, call `updateRSETHPrice()` atomically inside `sendFunds()` (or inside `receiveFromRewardReceiver()`) so that the price is updated in the same transaction as the reward flush, eliminating the window in which a deposit can be made at the stale price.
3. Consider a deposit delay or snapshot mechanism analogous to the mitigation recommended in the external report.

---

### Proof of Concept

```solidity
// Pseudocode — executable as a single Foundry test or flashloan contract

// Step 1: Record current rsETH price (stale, pre-reward)
uint256 stalePricePerRsETH = lrtOracle.rsETHPrice(); // e.g., 1.05 ETH

// Step 2: Deposit ETH at stale price — attacker gets more rsETH than post-reward price would give
lrtDepositPool.depositETH{value: 10 ether}(0, "");
uint256 attackerRsETH = rsETH.balanceOf(attacker); // minted at 1.05 ETH/rsETH

// Step 3: Flush accumulated MEV rewards (e.g., 5 ETH sitting in FeeReceiver)
feeReceiver.sendFunds(); // no access control — anyone can call

// Step 4: Update rsETH price — now reflects the extra 5 ETH
lrtOracle.updateRSETHPrice(); // new price e.g., 1.07 ETH/rsETH

// Step 5: Attacker's rsETH is now worth more than deposited
// attackerRsETH * 1.07 > 10 ETH
// Existing holders received a smaller share of the 5 ETH MEV reward
// than they would have without the attacker's deposit
```

The stolen yield per attack is `MEV_balance_in_FeeReceiver × deposit / (totalETHInProtocol + deposit)`. For a 5 ETH reward pool and a 100 ETH deposit against a 1000 ETH TVL, the attacker extracts approximately `5 × 100 / 1100 ≈ 0.45 ETH` from existing holders in a single transaction.

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

**File:** contracts/LRTOracle.sol (L256-265)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
