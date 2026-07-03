### Title
Public `updateRSETHPrice()` Enables Deposit-Before-Update Oracle Sandwich to Steal Staking Yield — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a `public` function callable by any unprivileged address. An attacker can deposit ETH/LST at a stale (lower) rsETH price, immediately call `updateRSETHPrice()` to push accrued staking rewards into the stored price, then initiate a withdrawal at the new higher price — capturing yield that belongs to existing rsETH holders.

---

### Finding Description

**Root cause — `updateRSETHPrice()` is unrestricted:** [1](#0-0) 

The function carries only a `whenNotPaused` modifier. Any EOA or contract can call it at will.

**Price is computed as `totalETHInProtocol / rsethSupply`:** [2](#0-1) 

`totalETHInProtocol` grows continuously as EigenLayer strategies earn staking rewards. Between calls to `updateRSETHPrice()`, the stored `rsETHPrice` is stale (lower than the true value).

**Deposits use the stale stored price:** [3](#0-2) 

`rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. A lower `rsETHPrice` means more rsETH minted per unit of ETH deposited.

**Withdrawal requests lock in the price at request time:** [4](#0-3) 

`getExpectedAssetAmount()` reads `lrtOracle.rsETHPrice()` at the moment `initiateWithdrawal()` is called and stores it as `expectedAssetAmount`.

**Payout is `min(expectedAssetAmount, currentReturn)` at unlock time:** [5](#0-4) 

As long as the price does not fall below the price at withdrawal-request time, the attacker receives the full `expectedAssetAmount` locked in at the higher price.

**`pricePercentageLimit` defaults to 0 (no cap):** [6](#0-5) 

When `pricePercentageLimit == 0`, the condition `pricePercentageLimit > 0 && ...` is always false, so there is no ceiling on how large a price jump a non-manager can push through.

**Instant withdrawal removes the 8-day delay entirely:** [7](#0-6) 

If `isInstantWithdrawalEnabled[asset]` is true, the attacker can execute deposit → `updateRSETHPrice()` → `instantWithdrawal()` in consecutive blocks, with no lock-up period.

---

### Impact Explanation

The attacker captures accrued staking rewards that should be distributed pro-rata to all existing rsETH holders. Each attack extracts yield proportional to the attacker's share of TVL after deposit. This is **theft of unclaimed yield (High severity)**. When instant withdrawal is enabled the attack is atomic and the impact escalates toward direct fund theft (Critical).

---

### Likelihood Explanation

- `updateRSETHPrice()` is `public` with no access control — any address can call it.
- Staking rewards accrue every block; the price is always slightly stale between updates.
- `pricePercentageLimit` is `0` by default, imposing no cap on non-manager callers.
- The attack is repeatable on every reward accrual cycle and scales linearly with deposit size.
- No front-running of a privileged transaction is required; the attacker directly controls the oracle update.

---

### Recommendation

1. Restrict `updateRSETHPrice()` to an authorized keeper or `onlyLRTManager`, removing the public entry point.
2. Alternatively, enforce a minimum lock-up period (e.g., 24 hours) between deposit and the earliest allowed withdrawal request, analogous to the fix recommended in the Olympus report.
3. Ensure `pricePercentageLimit` is always set to a non-zero value before deployment so that even if the function remains public, the per-call price jump is bounded.

---

### Proof of Concept

**Setup:** Staking rewards have accrued; stored `rsETHPrice = P1`; true price (after rewards) `= P2 > P1`. No one has called `updateRSETHPrice()` yet.

**Transaction 1 — deposit at stale price:**
```
LRTDepositPool.depositETH{value: X}(minRSETH, "")
→ rsethMinted = X / P1          (more rsETH than the true value warrants)
```

**Trigger oracle update:**
```
LRTOracle.updateRSETHPrice()
→ rsETHPrice updated from P1 to P2
```

**Transaction 2 — initiate withdrawal at new price:**
```
LRTWithdrawalManager.initiateWithdrawal(ETH, X/P1 rsETH, "")
→ expectedAssetAmount = (X/P1) * P2 / 1e18   (locked in at the higher price)
```

**After `withdrawalDelayBlocks` (≈ 8 days):**
```
LRTWithdrawalManager.completeWithdrawal(ETH, "")
→ receives (X/P1) * P2 ETH
```

**Attacker profit:**
```
profit = (X/P1) * P2 - X = X * (P2/P1 - 1)
```

For example, if rewards have accumulated for 7 days at 4 % APY (≈ 0.077 % increase) and the attacker deposits 1 000 ETH, profit ≈ 0.77 ETH before gas — repeatable every reward cycle.

### Citations

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

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-229)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
