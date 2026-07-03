### Title
Permissionless `FeeReceiver.sendFunds()` Enables Yield Theft via Deposit-Before-Reward-Distribution — (File: `contracts/FeeReceiver.sol`)

---

### Summary

The `FeeReceiver.sendFunds()` function has no access control. Any attacker can deposit at a stale rsETH price, then atomically trigger reward distribution and a price update — stealing accumulated MEV/execution-layer yield from existing rsETH holders.

---

### Finding Description

`FeeReceiver` accumulates MEV and execution-layer rewards. Its `sendFunds()` function is entirely permissionless: [1](#0-0) 

Any external caller can invoke it to push the full accumulated ETH balance into `LRTDepositPool.receiveFromRewardReceiver()`, which is a plain payable receiver with no logic: [2](#0-1) 

Separately, `LRTOracle.updateRSETHPrice()` is also permissionless: [3](#0-2) 

The rsETH minting rate used in `depositETH()` and `depositAsset()` reads the stored `rsETHPrice` from `LRTOracle`: [4](#0-3) 

This price is only updated when `updateRSETHPrice()` is explicitly called. This creates a window where:

1. Rewards have accumulated in `FeeReceiver` but have not yet been pushed to the deposit pool.
2. The stored `rsETHPrice` is stale — lower than the true value.
3. A depositor receives more rsETH than the true value warrants.

An attacker exploits this in three sequential, permissionless steps:

1. **Deposit** a large amount at the current stale price → receives inflated rsETH.
2. **Call `FeeReceiver.sendFunds()`** → pushes accumulated rewards into the deposit pool, increasing TVL.
3. **Call `LRTOracle.updateRSETHPrice()`** → price rises to reflect the new TVL.

The attacker's rsETH is now worth more than what they paid. Existing holders are diluted and lose a portion of the yield that was rightfully theirs.

The `_updateRsETHPrice()` internal logic confirms that the new price is computed from the current total ETH in the protocol (which now includes the pushed rewards) divided by the current rsETH supply (which now includes the attacker's minted tokens): [5](#0-4) 

The `getETHDistributionData()` function confirms that `address(this).balance` of the deposit pool — which includes the just-pushed reward ETH — is counted in TVL: [6](#0-5) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Let R = accumulated reward in `FeeReceiver`, T = existing TVL, X = attacker deposit, P = current rsETH price.

- Attacker deposits X ETH, receives X/P rsETH.
- After `sendFunds()`: TVL = T + X + R.
- After `updateRSETHPrice()`: new price = (T + X + R) / (S + X/P), where S = existing rsETH supply.
- Attacker profit = **R · X / (T + X)**.
- Existing holders lose exactly that amount.

With X = T (deposit equal to existing TVL), the attacker steals **50% of all pending rewards**. With X >> T, the attacker approaches stealing nearly all of R.

---

### Likelihood Explanation

**Medium.** All three steps (`depositETH`, `sendFunds`, `updateRSETHPrice`) are permissionless and callable by any EOA or contract. The `FeeReceiver` balance is publicly visible on-chain. No special role, key, or oracle compromise is required. The only barriers are capital commitment and the withdrawal delay. MEV rewards accumulate continuously, making this a recurring opportunity.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` does not prevent the attack: [7](#0-6) 

The attacker can deliberately make X large enough to dilute the per-token price increase below the threshold, bypassing the revert.

---

### Recommendation

1. **Add access control to `FeeReceiver.sendFunds()`** so only an authorized operator/manager can trigger reward distribution. This prevents an attacker from controlling the timing of reward injection.
2. **Alternatively**, call `updateRSETHPrice()` at the start of `depositETH()` and `depositAsset()` to ensure the price is always current before rsETH is minted. This eliminates the stale-price window entirely.

---

### Proof of Concept

```
State:
  TVL = 1000 ETH, rsETH supply = 1000, rsETHPrice = 1e18
  FeeReceiver.balance = 10 ETH (accumulated MEV rewards)

Step 1: Attacker calls LRTDepositPool.depositETH{value: 1000 ETH}(0, "")
  → rsethAmountToMint = 1000e18 * 1e18 / 1e18 = 1000 rsETH (at stale price)
  → TVL = 2000 ETH, rsETH supply = 2000

Step 2: Attacker calls FeeReceiver.sendFunds()
  → 10 ETH pushed to LRTDepositPool
  → TVL = 2010 ETH, rsETH supply = 2000

Step 3: Attacker calls LRTOracle.updateRSETHPrice()
  → newRsETHPrice = 2010e18 / 2000 = 1.005e18

Result:
  Attacker's 1000 rsETH is worth 1005 ETH → profit = 5 ETH
  Existing holders' 1000 rsETH is worth 1005 ETH (gained 5 ETH)
  Without attack, existing holders would have gained 10 ETH
  → Attacker stole 5 ETH of yield from existing rsETH holders
```

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L252-265)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
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
