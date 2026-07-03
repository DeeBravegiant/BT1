### Title
Improper Handling of `rsethSupply == 0` in `_updateRsETHPrice` Returns Incorrect 1:1 Default Price When Protocol ETH Remains - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle._updateRsETHPrice()` unconditionally sets `rsETHPrice = 1 ether` and resets `highestRsethPrice = 1 ether` whenever `rsethSupply == 0`, without checking whether ETH (or LST value) already exists in the protocol. This is the direct analog of the PoolTogether M-20 bug: a default value is returned for one edge of the ratio (supply = 0) without validating the other side (assets > 0), producing a price that does not reflect the protocol's actual state.

---

### Finding Description

In `LRTOracle._updateRsETHPrice()`:

```solidity
if (rsethSupply == 0) {
    rsETHPrice = 1 ether;
    highestRsethPrice = 1 ether;
    return;
}
``` [1](#0-0) 

When `rsethSupply > 0`, the price is correctly computed as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [2](#0-1) 

The inconsistency is structurally identical to the PoolTogether bug:

| State | PoolTogether | LRT-rsETH |
|---|---|---|
| Numerator = 0, Denominator > 0 | Returns `_assetUnit` (1:1) — wrong | N/A |
| Denominator = 0, Numerator > 0 | N/A | Returns `1 ether` — wrong if ETH exists |
| Both non-zero | Correct ratio | Correct ratio |

When `rsethSupply == 0` but `totalETHInProtocol > 0` (e.g., staking rewards have accumulated in the deposit pool, NDCs, or unstaking vault after all users withdrew), the stored `rsETHPrice` is set to `1 ether` instead of the correct value of `totalETHInProtocol / rsethSupply` (which would be undefined/infinite, signalling that no new deposits should be priced at 1:1).

The deposit function in `LRTDepositPool` reads the stored `rsETHPrice` directly:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

So a depositor who arrives while `rsETHPrice == 1 ether` (set by the zero-supply branch) receives rsETH at a 1:1 ratio, even though the protocol holds pre-existing ETH value that should be reflected in the price.

Additionally, `highestRsethPrice` is also reset to `1 ether`, erasing the historical price peak. This disables the downside-protection circuit breaker relative to the true historical high, and allows the upside-protection threshold to be measured from `1 ether` rather than the last real price. [1](#0-0) 

---

### Impact Explanation

**Theft of unclaimed yield (High).**

Scenario:
1. All rsETH holders withdraw → `rsethSupply = 0`.
2. Staking rewards (ETH) continue to flow into the deposit pool via `receiveFromRewardReceiver()` or `receiveFromNodeDelegator()`, or LST rewards accrue in NDCs/EigenLayer strategies. Call this amount `Y`.
3. Anyone calls `updateRSETHPrice()` → `rsETHPrice = 1 ether` (stored on-chain).
4. Attacker deposits `X` ETH → receives `X` rsETH (priced at 1:1 using the stored `1 ether` price).
5. Anyone calls `updateRSETHPrice()` again → `rsETHPrice = (X + Y) / X > 1 ether`.
6. Attacker calls `initiateWithdrawal` with `X` rsETH → `getExpectedAssetAmount` returns `X * rsETHPrice / assetPrice = X + Y` ETH.
7. Attacker receives `X + Y` ETH, stealing `Y` ETH of accumulated yield.

The `getExpectedAssetAmount` function in `LRTWithdrawalManager` uses the live `rsETHPrice`:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [4](#0-3) 

The attacker can size `X` to be large relative to `Y` to stay within any `pricePercentageLimit` threshold on the second `updateRSETHPrice()` call, or exploit the fact that `pricePercentageLimit` may be 0 (no threshold enforced). [5](#0-4) 

---

### Likelihood Explanation

**Low-to-Medium.** The precondition is that `rsethSupply` reaches zero while protocol ETH remains. This can occur when:
- All users complete withdrawals but staking rewards (ETH or LST yield) continue to arrive in the deposit pool, NDCs, or unstaking vault between the last withdrawal and the next deposit.
- The `receiveFromRewardReceiver()` and `receiveFromNodeDelegator()` functions accept ETH unconditionally with no supply check. [6](#0-5) 

In a mature protocol this is unlikely but not impossible, particularly during protocol migrations, emergency wind-downs, or periods of low activity. The `updateRSETHPrice()` function is public and callable by anyone, so no privileged access is required to trigger the mispricing. [7](#0-6) 

---

### Recommendation

Before returning early, check whether `totalETHInProtocol > 0`. If assets exist with zero supply, the protocol is in an anomalous state and should not silently set the price to `1 ether`:

```solidity
if (rsethSupply == 0) {
    uint256 totalETHInProtocol = _getTotalEthInProtocol();
    if (totalETHInProtocol > 0) {
        // Assets exist with no supply — do not set a misleading 1:1 price.
        // Either revert, pause, or leave rsETHPrice unchanged.
        revert ProtocolHasAssetsButNoSupply();
    }
    rsETHPrice = 1 ether;
    highestRsethPrice = 1 ether;
    return;
}
```

This prevents the first depositor from receiving rsETH at a 1:1 ratio when the protocol already holds ETH value, and prevents `highestRsethPrice` from being incorrectly reset.

---

### Proof of Concept

```solidity
// Preconditions:
// 1. All rsETH has been burned (rsethSupply == 0)
// 2. Y ETH of staking rewards has accumulated in the deposit pool

// Step 1: Anyone calls updateRSETHPrice() — sets rsETHPrice = 1 ether
lrtOracle.updateRSETHPrice();
assertEq(lrtOracle.rsETHPrice(), 1 ether);

// Step 2: Attacker deposits X ETH, receives X rsETH at 1:1
uint256 X = 100 ether;
lrtDepositPool.depositETH{value: X}(X, "");
assertEq(rsETH.balanceOf(attacker), X);

// Step 3: updateRSETHPrice() is called again — now rsETHPrice = (X + Y) / X
lrtOracle.updateRSETHPrice();
// rsETHPrice > 1 ether

// Step 4: Attacker initiates withdrawal of X rsETH
// getExpectedAssetAmount returns X * rsETHPrice / 1e18 = X + Y
lrtWithdrawalManager.initiateWithdrawal(ETH_TOKEN, X, "");
// After unlock + delay, attacker receives X + Y ETH, stealing Y ETH of yield
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
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

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTDepositPool.sol (L61-67)
```text
    function receiveFromRewardReceiver() external payable { }

    /// @dev receive from LRTConverter
    function receiveFromLRTConverter() external payable { }

    /// @dev receive from NodeDelegator
    function receiveFromNodeDelegator() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```
