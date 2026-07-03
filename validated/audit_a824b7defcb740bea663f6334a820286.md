### Title
RSETH Pause State Bleeds Into `unlockQueue`, Temporarily Freezing All Pending Withdrawal Unlocks — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.unlockQueue` calls `IRSETH.burnFrom(address(this), rsETHBurned)` to destroy the rsETH held by the withdrawal manager. `RSETH.burnFrom` carries a `whenNotPaused` modifier. Because RSETH can be paused independently of `LRTWithdrawalManager`, any pause of the RSETH token contract — whether targeted or via `LRTConfig.pauseAll` — causes every `unlockQueue` call to revert, freezing all pending withdrawal unlocks until RSETH is unpaused by LRTAdmin.

---

### Finding Description

**Root cause — `RSETH.burnFrom` is gated by `whenNotPaused`:**

`RSETH.burnFrom` at line 245 carries the `whenNotPaused` modifier: [1](#0-0) 

**`unlockQueue` calls `burnFrom` unconditionally (when `rsETHBurned != 0`):** [2](#0-1) 

**RSETH can be paused independently of `LRTWithdrawalManager`:**

`RSETH.pause()` is callable by any holder of `PAUSER_ROLE` directly on the RSETH contract: [3](#0-2) 

`LRTConfig.pauseAll` also pauses RSETH independently — and notably, it pauses `lrtWithdrawalManager` too, but the ordering is irrelevant because `unlockQueue` has its own `whenNotPaused` guard that would block it in that case. The critical scenario is a **targeted pause of RSETH alone** while `LRTWithdrawalManager` remains unpaused: [4](#0-3) 

**The exact revert path:**

```
PAUSER_ROLE → RSETH.pause()
operator    → LRTWithdrawalManager.unlockQueue(asset, ...)
               └─ _unlockWithdrawalRequests(...)   // succeeds, computes rsETHBurned
               └─ IRSETH.burnFrom(address(this), rsETHBurned)
                    └─ whenNotPaused modifier → revert "Pausable: paused"
```

At the point of revert, `_unlockWithdrawalRequests` has already updated in-memory state (request amounts, `assetsCommitted` decrements, `unlockedWithdrawalsCount` increments) but none of it is committed because the whole transaction reverts. No state is corrupted, but no withdrawal can be unlocked.

---

### Impact Explanation

All rsETH deposited by users during `initiateWithdrawal` is held by `LRTWithdrawalManager`: [5](#0-4) 

Until `unlockQueue` succeeds, those requests remain in the locked queue. `completeWithdrawal` requires the request to be unlocked (`nextLockedNonce` advanced): [6](#0-5) 

So while RSETH is paused, users cannot receive their underlying ETH/LST assets. This is a **temporary freezing of funds** (Medium impact) lasting until LRTAdmin calls `RSETH.unpause()`.

---

### Likelihood Explanation

The PAUSER_ROLE is a live operational role used for emergency response. Pausing RSETH directly (without pausing `LRTWithdrawalManager`) is a realistic scenario — e.g., a suspected mint exploit triggers a targeted RSETH pause while the team wants withdrawals to continue. The design does not prevent this asymmetric pause state, and no code path in `unlockQueue` checks or handles RSETH's pause state before calling `burnFrom`.

---

### Recommendation

Add a try/catch around the `burnFrom` call, or — preferably — restructure the design so that the rsETH burn is not required for the unlock step. One approach: track the rsETH to burn separately and allow a dedicated `burnAccumulatedRsETH()` function that can be called once RSETH is unpaused, decoupling the unlock accounting from the token burn. Alternatively, document that RSETH must never be paused independently of `LRTWithdrawalManager` and enforce this in `pauseAll` by always pausing both together.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Foundry fork test (local fork or Anvil)
import "forge-std/Test.sol";

contract UnlockQueuePausePoC is Test {
    LRTWithdrawalManager withdrawalManager = LRTWithdrawalManager(<deployed_addr>);
    RSETH rsETH = RSETH(<rseth_addr>);
    address pauser = <pauser_role_holder>;
    address operator = <operator_role_holder>;
    address asset = <supported_asset>;

    function testUnlockQueueFrozenWhenRsETHPaused() public {
        // 1. Ensure there is at least one pending withdrawal request
        //    (set up via initiateWithdrawal in setUp or assume existing state)

        // 2. Pause RSETH only (LRTWithdrawalManager remains unpaused)
        vm.prank(pauser);
        rsETH.pause();
        assertTrue(rsETH.paused());
        assertFalse(withdrawalManager.paused());

        // 3. Operator attempts to unlock the queue — must revert
        vm.prank(operator);
        vm.expectRevert("Pausable: paused");
        withdrawalManager.unlockQueue(
            asset,
            type(uint256).max,
            0, 0,
            type(uint256).max, type(uint256).max
        );

        // 4. Unpause RSETH — unlockQueue now succeeds
        vm.prank(<lrt_admin>);
        rsETH.unpause();

        vm.prank(operator);
        withdrawalManager.unlockQueue(
            asset,
            type(uint256).max,
            0, 0,
            type(uint256).max, type(uint256).max
        ); // succeeds
    }
}
```

### Citations

**File:** contracts/RSETH.sol (L184-186)
```text
    function pause() external onlyRole(LRTConstants.PAUSER_ROLE) {
        _pause();
    }
```

**File:** contracts/RSETH.sol (L245-248)
```text
    function burnFrom(address account, uint256 amount) external onlyRole(LRTConstants.BURNER_ROLE) whenNotPaused {
        _enforceNotBlocked(account);
        _burn(account, amount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L305-305)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

**File:** contracts/LRTWithdrawalManager.sol (L707-707)
```text
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTConfig.sol (L262-271)
```text
    function pauseAll() external onlyRole(LRTConstants.PAUSER_ROLE) {
        IPausable lrtDepositPool = IPausable(getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable lrtWithdrawalManager = IPausable(getContract(LRTConstants.LRT_WITHDRAW_MANAGER));
        IPausable lrtOracle = IPausable(getContract(LRTConstants.LRT_ORACLE));
        IPausable rsETHContract = IPausable(rsETH);

        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!lrtWithdrawalManager.paused()) lrtWithdrawalManager.pause();
        if (!lrtOracle.paused()) lrtOracle.pause();
        if (!rsETHContract.paused()) rsETHContract.pause();
```
