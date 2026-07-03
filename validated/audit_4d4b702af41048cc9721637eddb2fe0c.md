### Title
Stale `rsETHPrice` Allows Yield Theft via Deposit-Before-Oracle-Update Attack - (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle.rsETHPrice` is a cached storage variable updated only when `updateRSETHPrice()` is called. Because `updateRSETHPrice()` is unrestricted (`public`, no role check), any attacker can: (1) deposit ETH/LST at the stale (lower) price to receive more rsETH than fair value, (2) immediately call `updateRSETHPrice()` to advance the price to its true value, and (3) later redeem the over-minted rsETH at the higher price — extracting yield that belongs to existing rsETH holders.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is declared `public` with no role restriction: [1](#0-0) 

The function writes to the `rsETHPrice` storage slot: [2](#0-1) 

Between two successive calls to `updateRSETHPrice()`, staking rewards and EigenLayer yield cause the true TVL to grow while `rsETHPrice` remains frozen at its last-written value. `LRTDepositPool.getRsETHAmountToMint()` divides by this stale price: [3](#0-2) 

Because `rsETHPrice_stale < rsETHPrice_true`, the division yields a larger `rsethAmountToMint` than the depositor is entitled to. The attacker then calls `updateRSETHPrice()` themselves, advancing the price to its true value. When they later redeem via `LRTWithdrawalManager.getExpectedAssetAmount()`, which multiplies by the now-higher `rsETHPrice`: [4](#0-3) 

the attacker receives more underlying ETH/LST than they deposited, capturing yield that should have accrued to pre-existing rsETH holders.

The `_updateRsETHPrice()` internal logic confirms the price is computed from live TVL and written to storage only at call time, with no freshness enforcement at deposit: [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders accumulate yield as the protocol's TVL grows. An attacker who deposits at the stale price and redeems at the updated price extracts a portion of that accrued yield. The profit scales linearly with deposit size and with the magnitude of price staleness (i.e., how long since the last `updateRSETHPrice()` call and how much yield accrued in that window). For a protocol managing hundreds of millions in TVL with daily reward accrual, a single well-timed large deposit can extract thousands of dollars of yield per execution.

---

### Likelihood Explanation

**Medium.**

- `updateRSETHPrice()` is permissionless and observable on-chain; the attacker knows exactly when the price was last updated.
- Reward accrual is continuous and predictable (Ethereum staking APY ~3–4%, EigenLayer rewards on top).
- The attacker does not need to front-run anyone — they deposit, then call `updateRSETHPrice()` themselves in the same or next block.
- The 8-day withdrawal delay (`withdrawalDelayBlocks`) adds friction but does not eliminate profit; if `isInstantWithdrawalEnabled` is active for the asset, the delay is bypassed entirely (minus the instant-withdrawal fee, which may be smaller than the yield captured).
- No special privileges are required; any EOA or contract can execute the full sequence.

---

### Recommendation

1. **Force a price refresh before minting.** Call `_updateRsETHPrice()` (or an equivalent internal read of live TVL) inside `getRsETHAmountToMint()` so the mint calculation always uses the current price, not a cached one.
2. **Alternatively, enforce a price-freshness window.** Record a `lastPriceUpdateTimestamp` and revert deposits if the price is older than a configurable threshold (e.g., 1 hour).
3. **Restrict `updateRSETHPrice()`.** If the price update is intended to be operator-driven, add a role check so external actors cannot trigger it on demand to sandwich their own deposits.

---

### Proof of Concept

```
Setup:
  TVL  = 1 000 ETH, rsETH supply = 1 000, rsETHPrice_stored = 1.00 ETH (last updated 24 h ago)
  True TVL after 24 h of rewards = 1 001 ETH → true price = 1.001 ETH/rsETH

Step 1 — Attacker calls LRTDepositPool.depositETH{value: 100 ETH}(0, ""):
  rsethAmountToMint = (100 × 1e18) / 1.000e18 = 100.000 rsETH   ← uses stale price
  Fair amount                                  = 100 / 1.001 ≈ 99.900 rsETH
  Over-minted                                  ≈ 0.100 rsETH

Step 2 — Attacker calls LRTOracle.updateRSETHPrice():
  New TVL    = 1 001 + 100 = 1 101 ETH
  New supply = 1 000 + 100 = 1 100 rsETH
  New price  = 1 101 / 1 100 ≈ 1.000909 ETH/rsETH

Step 3 — After withdrawal delay, attacker redeems 100.000 rsETH:
  ETH received = 100.000 × 1.000909 ≈ 100.091 ETH

Profit ≈ 0.091 ETH extracted from existing holders' accrued yield.
(Scales to ~$910 per $1 M deposit per 1% price staleness gap.)
```

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-232)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
