The key question is whether the fee mint in `LRTOracle._updateRsETHPrice()` and user deposit mints in `LRTDepositPool` share the same `RSETH.maxMintAmountPerDay` cap. Let me verify the exact call chain.

### Title
Fee Minting in `LRTOracle._updateRsETHPrice` Shares `RSETH.maxMintAmountPerDay` Cap With User Deposits, Enabling Temporary Deposit Freeze - (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is a permissionless public function. When called, it may mint protocol fee rsETH to the treasury via `IRSETH.mint()`. This fee mint goes through the same `checkDailyMintLimit` modifier in `RSETH.sol` that governs all user deposit mints. There is no coordination between fee minting and user deposit minting against the shared `RSETH.maxMintAmountPerDay` cap. A sufficiently large fee mint can exhaust the daily cap, causing all subsequent `LRTDepositPool.depositETH` and `depositAsset` calls to revert with `DailyMintLimitExceeded` for up to 24 hours.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is callable by anyone: [1](#0-0) 

Inside `_updateRsETHPrice()`, when TVL has grown and the protocol is not paused, a protocol fee is computed and minted as rsETH to the treasury: [2](#0-1) 

This call to `IRSETH.mint()` passes through `RSETH.mint()`, which carries the `checkDailyMintLimit` modifier: [3](#0-2) 

The modifier checks and increments `currentPeriodMintedAmount` against `maxMintAmountPerDay`: [4](#0-3) 

User deposits via `LRTDepositPool.depositETH()` → `_mintRsETH()` also call `IRSETH.mint()` through the same modifier: [5](#0-4) 

Although `LRTOracle` has its own separate fee-specific daily cap (`maxFeeMintAmountPerDay` / `_checkAndUpdateDailyFeeMintLimit`), this only limits how much fee can be minted at the oracle level. It does **not** prevent the fee mint from consuming quota from `RSETH.maxMintAmountPerDay`, which is the shared global cap that also governs user deposit mints. [6](#0-5) 

There is no mechanism to reserve a portion of `RSETH.maxMintAmountPerDay` for user deposits versus fee minting.

---

### Impact Explanation

If a fee mint consumes all or most of `RSETH.maxMintAmountPerDay` in a given 24-hour period, all subsequent calls to `LRTDepositPool.depositETH()` and `depositAsset()` will revert with `DailyMintLimitExceeded` until the period resets. This constitutes **temporary freezing of all user deposits** for up to one 24-hour period. User funds are not lost, but the protocol fails to deliver its core promised function (accepting deposits) for the duration.

---

### Likelihood Explanation

- `updateRSETHPrice()` is permissionless — any EOA can call it.
- `protocolFeeInBPS` is a live protocol parameter; it is non-zero in normal operation.
- If `updateRSETHPrice()` is not called frequently (e.g., delayed for hours or days), accumulated rewards produce a proportionally larger fee mint in a single call.
- `RSETH.maxMintAmountPerDay` is set by the LRT manager as a safety cap, likely calibrated to expected user deposit volume, not to the sum of user deposits plus fee mints.
- The combination of a delayed price update and a high-reward period can produce a fee mint that saturates the daily cap without any malicious action.

---

### Recommendation

Decouple fee minting from the user-deposit daily mint cap. Options include:

1. **Exempt the treasury address from `checkDailyMintLimit`** (similar to `isPermanentlyExempt` for transfer blocks), so fee mints do not consume user deposit quota.
2. **Introduce a separate `RSETH.mint` entry point for fee minting** that bypasses or uses a dedicated sub-cap, while keeping the existing `checkDailyMintLimit` solely for user-facing mints.
3. **Reserve a portion of `maxMintAmountPerDay`** for fee minting by tracking fee mints separately within `RSETH` and subtracting them from the user-available quota.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Foundry fork/local test (no public mainnet)
// Setup: deploy protocol, configure protocolFeeInBPS > 0,
//        set RSETH.maxMintAmountPerDay just above expected fee amount.

function test_feeMintExhaustsUserDepositCap() public {
    // 1. Simulate TVL growth so a fee will be minted
    //    (e.g., send ETH rewards to NodeDelegator so totalETHInProtocol > previousTVL)
    vm.deal(address(nodeDelegator), 10 ether); // simulate staking rewards

    // 2. Set maxMintAmountPerDay to just above the expected fee amount
    //    Fee = rewardAmount * protocolFeeInBPS / 10000 / rsETHPrice
    //    e.g., if fee ≈ 0.05 rsETH, set maxMintAmountPerDay = 0.06 ether
    vm.prank(lrtManager);
    rsETH.setMaxMintAmountPerDay(0.06 ether);

    // 3. Anyone calls updateRSETHPrice — fee mint consumes ~0.05 rsETH of the 0.06 cap
    lrtOracle.updateRSETHPrice();

    // 4. User attempts to deposit ETH (would mint > 0.01 rsETH)
    vm.deal(user, 1 ether);
    vm.prank(user);
    vm.expectRevert(
        abi.encodeWithSelector(RSETH.DailyMintLimitExceeded.selector, ...)
    );
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
    // Deposits are frozen until the 24-hour period resets
}
```

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
