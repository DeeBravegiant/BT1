### Title
Share Inflation Attack via ETH Donation to Inflate `rsETHPrice` When `rsethSupply` Is Small — (File: contracts/LRTOracle.sol)

---

### Summary

The `_updateRsETHPrice()` function in `LRTOracle.sol` computes `rsETHPrice` as `totalETHInProtocol / rsethSupply`, where `totalETHInProtocol` includes `address(depositPool).balance` — a value directly inflatable by anyone via ETH donation. When `rsethSupply` is very small (e.g., at protocol launch) and `pricePercentageLimit == 0` (the default, unset state), an attacker can donate ETH to the deposit pool, call the public `updateRSETHPrice()`, and inflate `rsETHPrice` to an extreme value. Subsequent depositors who call `depositETH` with `minRSETHAmountExpected = 0` receive 0 rsETH while their ETH is absorbed into the pool, which the attacker can then drain by redeeming their rsETH.

---

### Finding Description

**Root cause — `LRTOracle._updateRsETHPrice()`:** [1](#0-0) 

When `rsethSupply > 0`, the price is computed as: [2](#0-1) 

`totalETHInProtocol` is assembled by `_getTotalEthInProtocol()`, which calls `getTotalAssetDeposits()` on the deposit pool. For ETH, this resolves to `getETHDistributionData()`: [3](#0-2) 

`address(this).balance` — the raw ETH balance of the deposit pool — is included. Because the deposit pool has an open `receive()`: [4](#0-3) 

anyone can donate ETH to it, directly inflating `totalETHInProtocol`.

**Price-jump protection is disabled by default:** [5](#0-4) 

The guard `pricePercentageLimit > 0` is false when `pricePercentageLimit` has not been set (it defaults to `0`). The `setPricePercentageLimit` setter is admin-only and is not called in `initialize()`, so the protection is absent at deployment.

**`updateRSETHPrice()` is public:** [6](#0-5) 

Any caller can trigger a price update after donating ETH.

**No floor on `rsethAmountToMint` in `_beforeDeposit`:** [7](#0-6) 

If `rsethAmountToMint == 0` and `minRSETHAmountExpected == 0`, the deposit succeeds and the user receives 0 rsETH.

**`getRsETHAmountToMint` divides by the inflated price:** [8](#0-7) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

Attack math (concrete example):

| Step | Action | `rsethSupply` | `totalETHInProtocol` | `rsETHPrice` |
|---|---|---|---|---|
| 0 | `updateRSETHPrice()` called, supply=0 | 0 | — | 1e18 |
| 1 | Attacker deposits 1 wei ETH | 1 | 1 | 1e18 |
| 2 | Attacker donates 100e18 ETH to pool | 1 | ~100e18 | 1e18 (stale) |
| 3 | Attacker calls `updateRSETHPrice()` | 1 | ~100e18 | **100e18 × 1e18 / 1 = 100e36** |
| 4 | Victim deposits 1e18 ETH, `minRSETHAmountExpected=0` | 1 | ~101e18 | 100e36 |
| — | `rsethAmountToMint = 1e18 × 1e18 / 100e36 = 0` | — | — | — |

Victim loses 1e18 ETH. Attacker holds 1 wei rsETH backed by ~101e18 ETH in the pool and can redeem it through the withdrawal pipeline, recovering their 100e18 donation plus the victim's 1e18 ETH — a net profit of ~1e18 ETH.

---

### Likelihood Explanation

**Low.** Three conditions must hold simultaneously:

1. `pricePercentageLimit == 0` — true at deployment by default; mitigated once admin calls `setPricePercentageLimit`.
2. `rsethSupply` is very small — most likely at protocol launch or after near-total redemption.
3. A victim calls `depositETH` with `minRSETHAmountExpected = 0` — possible for naive integrators or scripts that omit slippage protection.

The window is narrow but real, particularly at launch before the price limit is configured.

---

### Recommendation

1. **Enforce a non-zero `rsethAmountToMint`** in `_beforeDeposit`: revert if `rsethAmountToMint == 0`.
2. **Set `pricePercentageLimit` in `initialize()`** to a safe default (e.g., 10% = `1e17`) so the price-jump guard is active from deployment.
3. **Seed the pool at deployment** by minting a non-trivial amount of rsETH to a dead address, ensuring `rsethSupply` is never negligibly small.

---

### Proof of Concept

```
1. Deploy protocol. rsETHPrice = 0 (uninitialized).
2. Call updateRSETHPrice() → rsETHPrice = 1e18 (supply == 0 branch).
3. Attacker calls depositETH{value: 1}(0, "") → mints 1 wei rsETH.
4. Attacker sends 100e18 ETH to address(lrtDepositPool) directly.
5. Attacker calls updateRSETHPrice():
   - rsethSupply = 1
   - totalETHInProtocol ≈ 100e18
   - newRsETHPrice = 100e18 * 1e18 / 1 = 100e36
   - pricePercentageLimit == 0 → guard skipped → rsETHPrice = 100e36
6. Victim calls depositETH{value: 1e18}(0, ""):
   - rsethAmountToMint = (1e18 * 1e18) / 100e36 = 0
   - minRSETHAmountExpected = 0 → no revert
   - Victim receives 0 rsETH; 1e18 ETH enters pool.
7. Attacker initiates withdrawal of 1 wei rsETH:
   - Pool holds ~101e18 ETH backed by 1 wei rsETH
   - Attacker recovers ~101e18 ETH (net gain ≈ 1e18 ETH).
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-222)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L252-266)
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
            }
```

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
