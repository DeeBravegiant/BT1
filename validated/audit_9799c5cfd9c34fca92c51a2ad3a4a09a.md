Audit Report

## Title
Fee Minting in `LRTOracle._updateRsETHPrice` Shares `RSETH.maxMintAmountPerDay` Cap With User Deposits, Enabling Temporary Deposit Freeze - (File: `contracts/LRTOracle.sol`)

## Summary

`LRTOracle.updateRSETHPrice()` is a permissionless public function that may mint protocol fee rsETH to the treasury via `IRSETH.mint()`. This fee mint passes through the same `checkDailyMintLimit` modifier in `RSETH.sol` that governs all user deposit mints. The oracle-level `maxFeeMintAmountPerDay` cap only limits fee mints at the oracle layer; it does not prevent fee mints from consuming quota from `RSETH.maxMintAmountPerDay`, the shared global cap. A sufficiently large fee mint can exhaust the daily cap, causing all subsequent `depositETH` and `depositAsset` calls to revert with `DailyMintLimitExceeded` for up to 24 hours.

## Finding Description

`LRTOracle.updateRSETHPrice()` is callable by any address with no access control: [1](#0-0) 

Inside `_updateRsETHPrice()`, when TVL has grown and the protocol is not paused, a fee is computed and minted as rsETH to the treasury via `IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee)`: [2](#0-1) 

This call routes to `RSETH.mint()`, which unconditionally applies the `checkDailyMintLimit` modifier to every caller: [3](#0-2) 

The modifier checks and increments `currentPeriodMintedAmount` against the single shared `maxMintAmountPerDay`: [4](#0-3) 

User deposits via `LRTDepositPool._mintRsETH()` call the identical `IRSETH.mint()` entry point: [5](#0-4) 

The oracle-level `_checkAndUpdateDailyFeeMintLimit` only prevents the fee from exceeding `maxFeeMintAmountPerDay` at the oracle layer. It does not reserve or protect any portion of `RSETH.maxMintAmountPerDay` for user deposits: [6](#0-5) 

The `isPermanentlyExempt` mapping in `RSETH` only governs transfer blocks via `_enforceNotBlocked`; it has no effect on `checkDailyMintLimit`: [7](#0-6) 

**Exploit path:**
1. Rewards accumulate in the protocol (e.g., staking rewards increase `totalETHInProtocol` above `previousTVL`).
2. `updateRSETHPrice()` is not called for an extended period, allowing the fee to grow proportionally.
3. Any unprivileged EOA calls `updateRSETHPrice()`.
4. The fee mint consumes a large portion (or all) of `RSETH.maxMintAmountPerDay` for the current period.
5. All subsequent calls to `depositETH` and `depositAsset` revert with `DailyMintLimitExceeded` until the 24-hour period resets.

No malicious intent is required; this can occur through normal protocol operation if `maxMintAmountPerDay` is calibrated to expected user deposit volume without accounting for fee mints.

## Impact Explanation

**Medium — Temporary freezing of funds.** User deposits are frozen for up to one 24-hour period. User funds are not lost, but the protocol fails to accept deposits for the duration. This matches the allowed impact class "Medium. Temporary freezing of funds."

## Likelihood Explanation

- `updateRSETHPrice()` requires no privileges; any EOA can call it.
- `protocolFeeInBPS` is a live protocol parameter expected to be non-zero in normal operation.
- Delayed price updates (hours or days) cause accumulated rewards to produce a proportionally larger single-call fee mint.
- `RSETH.maxMintAmountPerDay` and `LRTOracle.maxFeeMintAmountPerDay` are set independently by the LRT manager with no enforced relationship between them. If `maxFeeMintAmountPerDay >= RSETH.maxMintAmountPerDay`, a single fee mint can exhaust the global cap.
- No attacker coordination or victim mistake is required; the condition can arise from ordinary protocol operation.

## Recommendation

Decouple fee minting from the user-deposit daily mint cap. Options include:

1. **Exempt the treasury/oracle from `checkDailyMintLimit`** by adding a role-based or address-based bypass in `RSETH.mint()` for fee mints (analogous to `isPermanentlyExempt` for transfer blocks).
2. **Introduce a separate `RSETH` mint entry point for fee minting** (e.g., `mintFee`) that bypasses or uses a dedicated sub-cap, keeping `checkDailyMintLimit` solely for user-facing mints.
3. **Enforce a protocol invariant** that `maxFeeMintAmountPerDay + expected_user_deposit_volume <= RSETH.maxMintAmountPerDay`, and track fee mints separately within `RSETH` so the user-available quota is never consumed by fee mints.

## Proof of Concept

```solidity
// Foundry test (local deployment)
function test_feeMintExhaustsUserDepositCap() public {
    // 1. Simulate TVL growth (staking rewards)
    vm.deal(address(nodeDelegator), 10 ether);

    // 2. Set RSETH.maxMintAmountPerDay just above the expected fee amount
    //    e.g., fee ≈ 0.05 rsETH → set cap = 0.06 ether
    vm.prank(lrtManager);
    rsETH.setMaxMintAmountPerDay(0.06 ether);

    // 3. Set oracle fee cap to allow the fee mint
    vm.prank(lrtManager);
    lrtOracle.setMaxFeeMintAmountPerDay(0.06 ether);

    // 4. Anyone calls updateRSETHPrice — fee mint consumes ~0.05 rsETH of the 0.06 cap
    lrtOracle.updateRSETHPrice();

    // 5. User attempts to deposit ETH (would mint > 0.01 rsETH)
    vm.deal(user, 1 ether);
    vm.prank(user);
    vm.expectRevert(RSETH.DailyMintLimitExceeded.selector);
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
    // All deposits frozen until the 24-hour period resets
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

**File:** contracts/RSETH.sol (L294-306)
```text
    function _enforceNotBlocked(address account) internal {
        // Addresses that are permanently exempt can never be blocked
        if (isPermanentlyExempt[account]) return;

        // Check if the account has an active transfer block
        uint256 blockedUntil = transfersBlockedUntil[account];
        if (blockedUntil == 0) return;

        if (block.timestamp < blockedUntil) revert TransfersBlocked(account, blockedUntil);

        // Auto-clean up expired block
        delete transfersBlockedUntil[account];
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
