### Title
Stale `rsETHPrice` Allows Depositors to Capture Accrued Yield Before Price Update, Diluting Existing Holders — (File: `contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTOracle` stores `rsETHPrice` as a mutable state variable updated via the **public** `updateRSETHPrice()`. `LRTDepositPool` uses this stored (potentially stale) price to mint rsETH for every deposit. Because the price is not refreshed atomically with each deposit, an attacker can deposit at a stale (lower) price, then trigger the price update themselves, receiving more rsETH than fair value and capturing yield that belongs to existing holders.

---

### Finding Description

`LRTDepositPool.getRsETHAmountToMint()` calculates the rsETH to mint as:

```
rsethAmountToMint = (amount × assetPrice) / lrtOracle.rsETHPrice()
``` [1](#0-0) 

`rsETHPrice` is a stored state variable in `LRTOracle` that is **not** updated on every deposit. It is updated only when `updateRSETHPrice()` is explicitly called. [2](#0-1) 

Critically, `updateRSETHPrice()` is a **public, permissionless** function:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [3](#0-2) 

The internal `_updateRsETHPrice()` computes the new price from actual on-chain TVL:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [4](#0-3) 

Between price updates, staking rewards accrue inside EigenLayer strategies, increasing the true TVL without updating `rsETHPrice`. During this window, `rsETHPrice` is **lower than the actual per-share value**, so the deposit formula mints **more rsETH than the depositor's fair share**.

An attacker can exploit this in a single atomic sequence:

1. Observe that `rsETHPrice` is stale (actual TVL per rsETH > stored price).
2. Call `depositETH()` or `depositAsset()` with a large amount → receive `amount / rsETHPrice` rsETH, which is more than the fair `amount / actualPrice`.
3. Immediately call the public `updateRSETHPrice()` → price jumps to reflect actual TVL.
4. The attacker now holds rsETH whose ETH value exceeds the deposited amount.
5. Submit a withdrawal request → after the EigenLayer delay, receive more assets than deposited.

The profit is borne entirely by existing rsETH holders, whose proportional claim on TVL is diluted by the extra rsETH minted to the attacker.

The `pricePercentageLimit` guard only applies when `pricePercentageLimit > 0`:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
``` [5](#0-4) 

`pricePercentageLimit` is **not set in `initialize()`**, so it defaults to `0`, meaning the guard is entirely inactive by default and the public caller faces no restriction on the magnitude of the price jump they can trigger. [6](#0-5) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Every rsETH minted to the attacker at the stale price represents a claim on TVL that was earned by existing depositors. After the price update, existing holders' rsETH is worth proportionally less than it should be. The attacker extracts the accrued yield delta without having held rsETH during the period it was earned. The profit scales with deposit size and the magnitude of the price lag.

---

### Likelihood Explanation

**Medium.**

- Staking rewards accrue continuously; `rsETHPrice` is always at least slightly stale between updates.
- `updateRSETHPrice()` is public and permissionless — no privileged access is needed.
- `pricePercentageLimit` defaults to `0`, removing the only on-chain magnitude guard.
- The attacker controls both the deposit and the price-update call, requiring no front-running or mempool manipulation.
- The only friction is the EigenLayer withdrawal delay (~7 days), which reduces immediacy but does not eliminate profit.

---

### Recommendation

1. **Refresh price atomically on deposit**: call `_updateRsETHPrice()` (or an equivalent internal snapshot) at the start of `depositETH()` and `depositAsset()` so every deposit uses the current TVL-derived price.
2. **Restrict public price updates**: make `updateRSETHPrice()` callable only by a keeper/manager role, removing the attacker's ability to self-trigger the price jump.
3. **Enforce a non-zero `pricePercentageLimit`**: set a meaningful daily cap during initialization so that even if the public function remains, the exploitable price delta per call is bounded.

---

### Proof of Concept

**Setup:**
- Total rsETH supply `S = 1000 rsETH`
- Stored `rsETHPrice = 1.00 ETH` (stale; actual TVL = 1010 ETH, so fair price = 1.01 ETH)
- `pricePercentageLimit = 0` (default)

**Attack:**

```
Step 1 — Deposit at stale price:
  depositETH{value: 100 ETH}(0, "")
  rsethAmountToMint = 100e18 * 1e18 / 1.00e18 = 100 rsETH
  (fair amount would be 100 / 1.01 ≈ 99.01 rsETH)
  Attacker receives 100 rsETH instead of 99.01 rsETH.

Step 2 — Trigger price update:
  updateRSETHPrice()
  totalETHInProtocol = 1010 + 100 = 1110 ETH
  rsethSupply        = 1000 + 100 = 1100 rsETH
  newRsETHPrice      = 1110 / 1100 ≈ 1.009 ETH

Step 3 — Attacker's position:
  100 rsETH × 1.009 ETH/rsETH = 100.9 ETH
  Profit ≈ 0.9 ETH extracted from existing holders' accrued yield.

Step 4 — Withdraw:
  requestWithdrawal(100 rsETH) → after EigenLayer delay, receive ≈ 100.9 ETH.
```

The 0.9 ETH profit is yield that belonged to the original 1000-rsETH holders (who earned 10 ETH in rewards but now share the TVL with an attacker who contributed only 100 ETH yet holds a disproportionate share).

### Citations

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
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

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```
