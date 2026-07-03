### Title
`LRTOracle.updateRSETHPrice()` DoS when `RSETH` global daily mint limit is exhausted by user deposits - (File: contracts/LRTOracle.sol)

---

### Summary

`RSETH.mint()` enforces a single shared `checkDailyMintLimit` that counts **all** rsETH minted in a 24-hour window — both user deposits via `LRTDepositPool` and protocol-fee minting via `LRTOracle`. When the daily cap is exhausted by ordinary depositors, the fee-mint call inside `LRTOracle._updateRsETHPrice()` reverts, making `updateRSETHPrice()` uncallable for the remainder of the day and leaving the rsETH/ETH exchange rate stale.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` computes a protocol fee whenever TVL grows, then mints the fee as rsETH to the `PROTOCOL_TREASURY`:

```solidity
// contracts/LRTOracle.sol  lines 299-308
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
    if (rsethAmountToMintAsProtocolFee > 0) {
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
        emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
    }
}
``` [1](#0-0) 

`IRSETH.mint()` is `RSETH.mint()`, which carries the `checkDailyMintLimit` modifier:

```solidity
// contracts/RSETH.sol  lines 229-240
function mint(address to, uint256 amount)
    external
    onlyRole(LRTConstants.MINTER_ROLE)
    whenNotPaused
    checkDailyMintLimit(amount)   // <-- shared global counter
{
    _enforceNotBlocked(to);
    _mint(to, amount);
}
``` [2](#0-1) 

The modifier checks and increments `currentPeriodMintedAmount` against `maxMintAmountPerDay`:

```solidity
// contracts/RSETH.sol  lines 42-56
modifier checkDailyMintLimit(uint256 amount) {
    if (block.timestamp >= periodStartTime + 1 days) {
        currentPeriodMintedAmount = 0;
        periodStartTime = getCurrentPeriodStartTime();
    }
    if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
        revert DailyMintLimitExceeded(...);
    }
    currentPeriodMintedAmount += amount;
    _;
}
``` [3](#0-2) 

`LRTDepositPool._mintRsETH()` calls the exact same `RSETH.mint()` for every user deposit:

```solidity
// contracts/LRTDepositPool.sol  lines 686-690
function _mintRsETH(uint256 rsethAmountToMint) private {
    address rsethToken = lrtConfig.rsETH();
    IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
}
``` [4](#0-3) 

Because both paths share the same `currentPeriodMintedAmount` counter, user deposits consume the same budget that the oracle relies on for fee minting. Once the cap is reached, the oracle's `IRSETH.mint(treasury, ...)` call reverts, propagating the revert all the way up through `_updateRsETHPrice()` and out of the public `updateRSETHPrice()`.

---

### Impact Explanation

`updateRSETHPrice()` is the sole mechanism for refreshing the rsETH/ETH exchange rate stored in `rsETHPrice`. A stale price directly affects the rsETH amount minted to every subsequent depositor (`getRsETHAmountToMint` reads `rsETHPrice`), and any withdrawal valuation that depends on the oracle. The DoS lasts until the 24-hour window resets — a **temporary freezing** of correct price discovery and, by extension, of fair rsETH issuance.

Impact classification: **Medium — temporary freezing of funds / contract fails to deliver promised returns.**

---

### Likelihood Explanation

`updateRSETHPrice()` is a permissionless public function intended to be called regularly (e.g., by keepers). Any depositor who collectively or individually pushes `currentPeriodMintedAmount` to `maxMintAmountPerDay` triggers the condition. The attacker receives rsETH in return for their deposit, so the cost is the opportunity cost of capital, not a direct loss. On a chain with high deposit activity or a conservatively set `maxMintAmountPerDay`, this condition can be reached without any special privilege.

---

### Recommendation

Decouple the oracle's fee-minting budget from the user-deposit mint counter. The simplest fix is to give `LRTOracle` a separate minter path that bypasses `checkDailyMintLimit` (or has its own independent counter), so that exhausting the user-deposit cap cannot prevent protocol-fee minting and price updates. Alternatively, catch the `DailyMintLimitExceeded` revert inside `_updateRsETHPrice()` and continue with the price update while skipping the fee mint for that period, rather than reverting the entire call.

---

### Proof of Concept

1. `maxMintAmountPerDay` in `RSETH` is set to some finite value `M` (e.g., 1 000 rsETH).
2. Attacker (or organic users) calls `LRTDepositPool.depositETH{value: X}()` repeatedly until `currentPeriodMintedAmount == M`. Each call routes through `_mintRsETH → RSETH.mint → checkDailyMintLimit`, incrementing the shared counter.
3. TVL has grown since the last price update, so `protocolFeeInETH > 0` when `updateRSETHPrice()` is next called.
4. `_updateRsETHPrice()` reaches `IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee)`.
5. `RSETH.mint` executes `checkDailyMintLimit(rsethAmountToMintAsProtocolFee)`: `currentPeriodMintedAmount + fee > M` → `revert DailyMintLimitExceeded(...)`.
6. The revert bubbles up; `updateRSETHPrice()` reverts. The stored `rsETHPrice` is not updated for the rest of the 24-hour window.
7. All subsequent deposits use the stale price, minting incorrect rsETH amounts to users. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L299-308)
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

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```
