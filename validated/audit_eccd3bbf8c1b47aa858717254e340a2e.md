### Title
RSETH Daily Mint Limit Blocks `_updateRsETHPrice()` When Protocol Fee Minting Is Required, Causing Temporary Oracle DoS — (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` calls `IRSETH.mint()` to collect protocol fees. That call is gated by RSETH's `checkDailyMintLimit` modifier, which tracks a shared `currentPeriodMintedAmount` counter that accumulates across all rsETH minting — including ordinary user deposits. Once user deposits fill `currentPeriodMintedAmount` to `maxMintAmountPerDay`, every subsequent call to `updateRSETHPrice()` that would generate a non-zero protocol fee reverts, because the price write (`rsETHPrice = newRsETHPrice`) is placed unconditionally after the fee mint with no error isolation. The price oracle is frozen for up to 24 hours.

---

### Finding Description

**Two-layer limit mismatch (the structural analog to the MAXMEM bug):**

`LRTOracle` maintains its own daily fee-mint guard, `_checkAndUpdateDailyFeeMintLimit`, which tracks `currentPeriodMintedFeeAmount` against `maxFeeMintAmountPerDay`. [1](#0-0) 

This oracle-level check passes independently of how much rsETH users have minted that day. However, the actual mint call that follows is routed through `RSETH.mint()`: [2](#0-1) 

`RSETH.mint()` applies a second, independent limit — `checkDailyMintLimit` — that accumulates **all** rsETH minted in the current period, including user deposits: [3](#0-2) 

`currentPeriodMintedAmount` is incremented on every user deposit via `LRTDepositPool._mintRsETH()` → `IRSETH.mint()`. [4](#0-3) 

**The mismatch:** `maxFeeMintAmountPerDay` (oracle-level) and `maxMintAmountPerDay` (RSETH-level) are set independently. The oracle's guard can pass while the RSETH guard fails, because the RSETH guard counts user-deposit minting that the oracle guard ignores entirely. This is structurally identical to the MAXMEM bug: one limit (oracle fee limit) is satisfied, but a second, tighter constraint (RSETH total daily mint) is violated, causing the operation to revert.

**No error isolation around the mint call:** The price write `rsETHPrice = newRsETHPrice` at line 313 is placed after `IRSETH.mint()` with no `try/catch`. If `IRSETH.mint()` reverts, the entire `_updateRsETHPrice()` call reverts and the price is never updated. [5](#0-4) 

---

### Impact Explanation

When `currentPeriodMintedAmount` reaches `maxMintAmountPerDay`:

1. Every call to `updateRSETHPrice()` (public) or `updateRSETHPriceAsManager()` (manager-only) reverts whenever `protocolFeeInETH > 0` — i.e., whenever the protocol has earned rewards. Both functions call the same `_updateRsETHPrice()` with no bypass path.
2. `rsETHPrice` is frozen at its last stored value for up to 24 hours (until `currentPeriodMintedAmount` resets).
3. During the freeze, users depositing at the stale (lower) price receive more rsETH than they are entitled to, diluting existing holders — a theft of unclaimed yield from the holder base.
4. The protocol fee for the period cannot be collected (permanent loss of that period's unclaimed yield).

Impact classification: **Medium — Temporary freezing of unclaimed yield / temporary freezing of funds** (stale price causes incorrect rsETH issuance for up to 24 hours).

---

### Likelihood Explanation

- `updateRSETHPrice()` is a public function; any caller can trigger the revert once the limit is reached.
- An attacker fills `currentPeriodMintedAmount` by depositing assets into `LRTDepositPool`. The attacker receives rsETH in return, so net capital at risk is only price-movement exposure and gas — not a pure burn.
- `remainingDailyMintLimit()` on RSETH is a public view function that lets the attacker precisely measure how much more to deposit. [6](#0-5) 

- The attack is repeatable every 24 hours with the same capital (deposit → wait for period reset → withdraw or repeat).
- Likelihood: **Medium** — requires meaningful capital but is economically rational if the attacker profits from the stale price window.

---

### Recommendation

Decouple the price write from the fee mint. Apply a `try/catch` around `IRSETH.mint()` so that a failed fee mint does not block the price update:

```solidity
if (rsethAmountToMintAsProtocolFee > 0) {
    try IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee) {
        emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
    } catch {
        // fee deferred; price update proceeds
        currentPeriodMintedFeeAmount -= rsethAmountToMintAsProtocolFee; // roll back oracle counter
    }
}
rsETHPrice = newRsETHPrice;
```

Alternatively, move `rsETHPrice = newRsETHPrice` before the fee-minting block so the price is always committed regardless of fee-mint success.

---

### Proof of Concept

1. Read `RSETH.remainingDailyMintLimit()` → returns `R` (remaining capacity).
2. Attacker calls `LRTDepositPool.depositETH{value: R_in_ETH}(...)`, minting exactly `R` rsETH. `currentPeriodMintedAmount` is now equal to `maxMintAmountPerDay`.
3. EigenLayer rewards accrue; `totalETHInProtocol > previousTVL`, so `protocolFeeInETH > 0`.
4. Anyone calls `LRTOracle.updateRSETHPrice()`.
5. Execution reaches `IRSETH.mint(treasury, rsethAmountToMintAsProtocolFee)`.
6. `checkDailyMintLimit` fires: `currentPeriodMintedAmount + rsethAmountToMintAsProtocolFee > maxMintAmountPerDay` → `revert DailyMintLimitExceeded`.
7. The entire `updateRSETHPrice()` call reverts. `rsETHPrice` remains at its stale value.
8. Attacker (or any depositor) deposits additional assets at the stale lower price, receiving excess rsETH.
9. After 24 hours, `currentPeriodMintedAmount` resets and the oracle recovers — but the dilution and missed fee are permanent. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
    }
```

**File:** contracts/LRTOracle.sol (L299-313)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;
```

**File:** contracts/RSETH.sol (L42-56)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
    }
```

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
    }
```

**File:** contracts/RSETH.sol (L265-272)
```text
    function remainingDailyMintLimit() external view returns (uint256) {
        if (maxMintAmountPerDay == 0) return 0;

        // If we're on a new day but no mint has occurred yet, treat currentPeriodMintedAmount as 0
        uint256 effectiveDailyMintAmount = (block.timestamp >= periodStartTime + 1 days) ? 0 : currentPeriodMintedAmount;

        return maxMintAmountPerDay > effectiveDailyMintAmount ? maxMintAmountPerDay - effectiveDailyMintAmount : 0;
    }
```

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```
