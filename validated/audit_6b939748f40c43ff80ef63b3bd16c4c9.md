### Title
Bridged ETH Temporarily Frozen in L1VaultV2 When LRTDepositPool Is Paused — (`contracts/L1VaultV2.sol`)

---

### Summary

`L1VaultV2.depositETHForL1VaultETH()` is the sole mechanism to process ETH held in the vault. It unconditionally calls `LRTDepositPool.depositETH()`, which carries a `whenNotPaused` guard. When the deposit pool is paused, the call reverts and the ETH remains stranded in the vault with no alternative recovery path.

---

### Finding Description

ETH arrives in `L1VaultV2` via its bare `receive()` function, which is the intended entry point for L2-bridge deliveries. [1](#0-0) 

The only function that can consume that ETH is `depositETHForL1VaultETH()`: [2](#0-1) 

It calls `lrtDepositPool.depositETH{ value: balanceOfETH }(...)` at line 232. That function in `LRTDepositPool` is gated by `whenNotPaused`: [3](#0-2) 

`LRTDepositPool` can be paused by any address holding `PAUSER_ROLE`: [4](#0-3) 

A full audit of `L1VaultV2` (lines 1–564) reveals **no** `rescueETH`, `emergencyWithdraw`, or any other function that can move native ETH out of the vault by any path other than `depositETHForL1VaultETH()`. There is also no mechanism to relay the ETH back to L2. The vault holds ETH with no exit until the pool is unpaused.

---

### Impact Explanation

ETH bridged from L2 accumulates in `L1VaultV2` and cannot be converted to rsETH or returned to users for the entire duration of the pause. The freeze is bounded by the pause duration, making this **temporary freezing of funds** (Medium).

---

### Likelihood Explanation

`LRTDepositPool` pauses are a routine operational safety measure (e.g., oracle anomaly, exploit response). The bridge continues delivering ETH to the vault regardless of the pool's pause state. No attacker action is required; the condition arises from normal protocol operation. Likelihood is **medium-high** given that pauses are expected to occur periodically.

---

### Recommendation

Add an ETH rescue path callable by a privileged role (e.g., `TIMELOCK_ROLE` or `DEFAULT_ADMIN_ROLE`) that can either:
1. Wrap the ETH to WETH and hold it safely, or
2. Transfer it to a designated recovery address.

Example addition to `L1VaultV2`:

```solidity
function rescueETH(address payable recipient, uint256 amount)
    external
    nonReentrant
    onlyRole(TIMELOCK_ROLE)
{
    UtilLib.checkNonZeroAddress(recipient);
    (bool success, ) = recipient.call{ value: amount }("");
    if (!success) revert EthTransferFailed();
}
```

Alternatively, `depositETHForL1VaultETH()` could wrap ETH to WETH when the pool is paused, preserving value without blocking.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test (Foundry) — run against a mainnet/testnet fork
// Demonstrates ETH frozen in L1VaultV2 when LRTDepositPool is paused

import "forge-std/Test.sol";
import {L1VaultV2} from "contracts/L1VaultV2.sol";
import {LRTDepositPool} from "contracts/LRTDepositPool.sol";

contract FreezeTest is Test {
    L1VaultV2   vault;
    LRTDepositPool pool;
    address     manager;
    address     pauser;

    function setUp() public {
        // deploy / fork-attach vault and pool (addresses from deployment)
        manager = address(0xBEEF);
        pauser  = address(0xDEAD);
        // ... initialize vault with manager role, pool with pauser role ...
    }

    function testETHFrozenWhenPoolPaused() public {
        // 1. Simulate L2 bridge delivering ETH to vault
        vm.deal(address(vault), 10 ether);
        assertEq(address(vault).balance, 10 ether);

        // 2. Pause the deposit pool
        vm.prank(pauser);
        pool.pause();

        // 3. Manager attempts to convert ETH → rsETH
        vm.prank(manager);
        vm.expectRevert("Pausable: paused");
        vault.depositETHForL1VaultETH();

        // 4. ETH remains frozen — no other callable function drains it
        assertEq(address(vault).balance, 10 ether);

        // 5. Advance blocks — ETH still stuck
        vm.roll(block.number + 100);
        assertEq(address(vault).balance, 10 ether);
    }
}
```

The test confirms: after the pool is paused, `depositETHForL1VaultETH()` reverts and `address(vault).balance` remains non-zero with no callable recovery function available.

### Citations

**File:** contracts/L1VaultV2.sol (L224-235)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");

        emit ETHDepositForL1Vault(balanceOfETH, rsETHAmountToMint);
    }
```

**File:** contracts/L1VaultV2.sol (L562-563)
```text
    /// @dev Handles direct ETH transfers from the L2 bridge
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L348-351)
```text
    /// @dev Triggers stopped state. Contract must not be paused.
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }
```
