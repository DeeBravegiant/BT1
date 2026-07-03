Let me examine the relevant contracts to trace this vulnerability path.

Now I have all the information needed to trace the full call chain. Let me verify the exact lines.

The full call chain is confirmed. Here is the complete analysis:

---

### Title
Manager Can Block `PROTOCOL_TREASURY` Transfers, Permanently Freezing Protocol Fee Yield and Breaking Price Updates - (`contracts/RSETH.sol`, `contracts/LRTOracle.sol`)

### Summary
`RSETH.blockUserTransfers` accepts any address array and sets `transfersBlockedUntil[account] = block.timestamp + 1 days` for every address not in `isPermanentlyExempt`. The `PROTOCOL_TREASURY` address is never automatically added to `isPermanentlyExempt`. When the treasury is blocked, `LRTOracle._updateRsETHPrice` reverts entirely because its unconditional `IRSETH.mint(treasury, fee)` call hits `_enforceNotBlocked(treasury)` → `revert TransfersBlocked`. The manager can refresh the block before it expires, making the freeze indefinite.

### Finding Description

**Step 1 — Manager blocks the treasury**

`RSETH.blockUserTransfers` (line 161–177) iterates the supplied array and skips only addresses where `isPermanentlyExempt[account] == true` or `account == address(0)`. [1](#0-0) 

The `PROTOCOL_TREASURY` address is never added to `isPermanentlyExempt` by any initializer or constructor, so it is blockable. [2](#0-1) 

**Step 2 — Price update tries to mint fee to treasury**

`LRTOracle._updateRsETHPrice` (line 299–308) calls `IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee)` with no `try/catch` whenever `protocolFeeInETH > 0`. [3](#0-2) 

**Step 3 — `mint` enforces the block on the recipient**

`RSETH.mint` calls `_enforceNotBlocked(to)` before `_mint`. [4](#0-3) 

`_enforceNotBlocked` reverts with `TransfersBlocked` when `block.timestamp < transfersBlockedUntil[account]` and the account is not permanently exempt. [5](#0-4) 

**Step 4 — Entire price update reverts**

Because the revert propagates up through `_updateRsETHPrice` with no error handling, both `updateRSETHPrice()` (public, line 87) and `updateRSETHPriceAsManager()` (line 94) revert for the full 24-hour block window. [6](#0-5) 

**Step 5 — Block can be refreshed indefinitely**

`blockUserTransfers` comment explicitly states: *"Re-applying the block before expiry refreshes the hold to `block.timestamp + 1 days`"*. The manager can call it again at any time before expiry, making the freeze permanent. [7](#0-6) 

### Impact Explanation
- Protocol fee rsETH is never minted to the treasury for the duration of the block window (unclaimed yield frozen).
- `updateRSETHPrice()` reverts on every call while the treasury is blocked and TVL has increased, preventing the rsETH price from being updated at all.
- The manager can refresh the block before it expires, making both effects permanent.

Scoped impact: **Medium — Permanent freezing of unclaimed yield**.

### Likelihood Explanation
The `MANAGER` role is a hot operational key used for routine protocol management (blocking suspicious users, updating limits, etc.). The treasury address is not automatically protected. An accidental inclusion of the treasury in a `blockUserTransfers` call (e.g., from an off-chain script that generates a blocklist) is a realistic operational error. A malicious manager can also do this deliberately. No external dependency or second party is required.

### Recommendation
1. **Automatically add `PROTOCOL_TREASURY` to `isPermanentlyExempt`** when it is set in `LRTConfig`, or during `RSETH.reinitialize`.
2. **Add a guard in `blockUserTransfers`** that skips the treasury address (fetched from `lrtConfig`) in addition to the existing `isPermanentlyExempt` check.
3. **Alternatively**, wrap the `mint` call in `_updateRsETHPrice` in a `try/catch` so that a blocked treasury causes fee accrual to be skipped rather than reverting the entire price update.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";

// Fork test (local fork, no public mainnet)
contract TreasuryBlockPoC is Test {
    // Assume addresses are set up via setUp() with deployed contracts
    RSETH rseth;
    LRTOracle oracle;
    address manager;
    address treasury;

    function testTreasuryBlockFreezesFees() public {
        // 1. Manager blocks the treasury
        address[] memory targets = new address[](1);
        targets[0] = treasury;
        vm.prank(manager);
        rseth.blockUserTransfers(targets);

        // 2. Simulate TVL increase (e.g., warp time, increase mock asset price)
        // ...

        // 3. updateRSETHPrice reverts with TransfersBlocked
        vm.expectRevert(
            abi.encodeWithSelector(RSETH.TransfersBlocked.selector, treasury, block.timestamp + 1 days)
        );
        oracle.updateRSETHPrice();

        // 4. Manager refreshes the block before expiry → freeze is permanent
        vm.warp(block.timestamp + 23 hours);
        vm.prank(manager);
        rseth.blockUserTransfers(targets); // refreshes to block.timestamp + 1 days

        // 5. Price update still reverts
        vm.expectRevert(
            abi.encodeWithSelector(RSETH.TransfersBlocked.selector, treasury, block.timestamp + 1 days)
        );
        oracle.updateRSETHPrice();
    }
}
```

### Citations

**File:** contracts/RSETH.sol (L32-34)
```text

    /// @dev Permanently exempt addresses mapping (rsETH transfers for these can never be blocked)
    mapping(address account => bool isExempt) public isPermanentlyExempt;
```

**File:** contracts/RSETH.sol (L156-158)
```text
    /// @notice Block transfers TO and FROM given users for 24 hours
    /// @dev Re-applying the block before expiry refreshes the hold to `block.timestamp + 1 days`
    ///      (i.e. not cumulative; never more than 24h from the latest call). Exempt addresses cannot be blocked.
```

**File:** contracts/RSETH.sol (L161-177)
```text
    function blockUserTransfers(address[] calldata accounts) external onlyLRTManager {
        uint256 blockedUntil = block.timestamp + 1 days;
        uint256 length = accounts.length;

        for (uint256 i = 0; i < length; ++i) {
            address account = accounts[i];

            if (isPermanentlyExempt[account] || account == address(0)) continue;

            uint256 prevBlockedUntil = transfersBlockedUntil[account];

            if (blockedUntil != prevBlockedUntil) {
                transfersBlockedUntil[account] = blockedUntil;
                emit UserTransfersBlocked(account, blockedUntil);
            }
        }
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

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
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
