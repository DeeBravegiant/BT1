Let me check the deposit pool to see if user deposits call `IRSETH.mint()` and accumulate against the same `RSETH.maxMintAmountPerDay` limit.

### Title
Shared `RSETH.maxMintAmountPerDay` Limit Allows User Deposits to Block Protocol Fee Minting — (`contracts/RSETH.sol`, `contracts/LRTOracle.sol`)

---

### Summary

`RSETH.mint()` enforces a single `checkDailyMintLimit` modifier that applies to **all** callers with `MINTER_ROLE` — both user deposits (via `LRTDepositPool._mintRsETH`) and protocol fee mints (via `LRTOracle._updateRsETHPrice`). When user deposits exhaust `maxMintAmountPerDay` within a 24-hour window, any subsequent call to `updateRSETHPrice()` that attempts to mint fee rsETH to the treasury will revert at the `RSETH.mint()` call, causing the price update to fail entirely.

---

### Finding Description

**Two separate daily mint limit systems exist but interact through a shared gate:**

1. `LRTOracle._checkAndUpdateDailyFeeMintLimit()` — an oracle-level fee cap (`maxFeeMintAmountPerDay`) checked before the mint call.
2. `RSETH.checkDailyMintLimit` modifier — a token-level cap (`maxMintAmountPerDay`) applied to every `RSETH.mint()` call regardless of caller.

**User deposit path:**
`LRTDepositPool.depositETH()` → `_mintRsETH()` → `IRSETH.mint(msg.sender, amount)` → `checkDailyMintLimit` accumulates `currentPeriodMintedAmount`. [1](#0-0) 

**Fee mint path:**
`LRTOracle._updateRsETHPrice()` → `_checkAndUpdateDailyFeeMintLimit(feeAmount)` (passes) → `IRSETH.mint(treasury, feeAmount)` → `checkDailyMintLimit` **reverts** if `currentPeriodMintedAmount + feeAmount > maxMintAmountPerDay`. [2](#0-1) [3](#0-2) 

When `IRSETH.mint()` reverts at line 306 of `LRTOracle.sol`, the entire `_updateRsETHPrice()` transaction reverts — `rsETHPrice` is never updated (line 313), and the fee is never minted.

---

### Impact Explanation

The impact is **temporary freezing of unclaimed yield**, not permanent. Because `rsETHPrice` is not updated on revert, the next successful `updateRSETHPrice()` call (after the 24-hour window resets `currentPeriodMintedAmount`) recalculates the fee using the same old `rsETHPrice`, so the deferred TVL growth is captured in the cumulative `rewardAmount`. The fee is **deferred**, not permanently destroyed.

The question's "permanent freezing" characterization is overstated. The fee for the blocked period is rolled into the next successful price update. However, if an actor consistently fills `maxMintAmountPerDay` every day, fee minting is indefinitely deferred — approaching permanent freezing in practice.

The real design flaw is that a single token-level cap (`RSETH.maxMintAmountPerDay`) is shared between user-facing deposits and privileged protocol fee minting, with no priority or reservation for the latter. [4](#0-3) 

---

### Likelihood Explanation

- Requires `maxMintAmountPerDay` to be set to a finite value (not the default 0, which would break all minting).
- Requires user deposit volume to reach the daily cap before `updateRSETHPrice()` is called.
- No privileged access needed; any depositor can trigger this organically or deliberately.
- Likelihood is **low-to-medium**: depends on how conservatively `maxMintAmountPerDay` is configured relative to protocol deposit volume.

---

### Recommendation

Reserve capacity for fee minting within `RSETH.maxMintAmountPerDay` by either:
1. Exempting the `LRTOracle` contract (as `MINTER_ROLE` holder) from the `checkDailyMintLimit` modifier, using a separate fee-mint path with only `_checkAndUpdateDailyFeeMintLimit` as the gate.
2. Or tracking user-deposit mints and fee mints separately in `RSETH`, so the fee mint is never blocked by user deposit volume.

---

### Proof of Concept

```solidity
// Preconditions:
// - maxMintAmountPerDay = 1000 ether (finite)
// - periodStartTime is within current 24h window

// Step 1: Depositor fills the daily cap
lrtDepositPool.depositETH{value: largeAmount}(0, ""); 
// RSETH.currentPeriodMintedAmount == maxMintAmountPerDay

// Step 2: Staking rewards accrue (TVL increases), triggering fee
// Step 3: Anyone calls updateRSETHPrice()
lrtOracle.updateRSETHPrice();
// Execution reaches LRTOracle.sol:303 — _checkAndUpdateDailyFeeMintLimit passes
// Execution reaches LRTOracle.sol:306 — IRSETH.mint(treasury, feeAmount)
// RSETH.sol:50 — currentPeriodMintedAmount + feeAmount > maxMintAmountPerDay
// => reverts with DailyMintLimitExceeded
// rsETHPrice NOT updated; treasury balance unchanged for this period
```

The fee is deferred (not permanently lost) to the next day's `updateRSETHPrice()` call, but the period's fee mint is blocked and `rsETHPrice` stalls until the window resets. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
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
