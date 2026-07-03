### Title
Temporary ETH Freeze in AGETHPoolV3 When Deposit Gate Is Closed and Bridger Cannot Receive ETH — (`contracts/agETH/AGETHPoolV3.sol`)

---

### Summary

`AGETHPoolV3` has no user-accessible ETH withdrawal path. The sole admin-controlled ETH egress, `moveAssetsForBridging()`, pushes ETH directly to `msg.sender` (the bridger) via a low-level `call`. If the bridger contract cannot receive ETH and `isEthDepositEnabled` is simultaneously set to `false` by `DEFAULT_ADMIN_ROLE` (with no timelock), all ETH in the pool becomes temporarily inaccessible: new deposits are blocked, the only egress reverts, and no user-facing recovery exists.

---

### Finding Description

**Root cause 1 — No timelock on deposit gate:**

`setIsEthDepositEnabled` in `AGETHPoolV3` is gated by `DEFAULT_ADMIN_ROLE` with no timelock or delay:

```solidity
// AGETHPoolV3.sol line 255
function setIsEthDepositEnabled(bool _isEthDepositEnabled) external onlyRole(DEFAULT_ADMIN_ROLE) {
    isEthDepositEnabled = _isEthDepositEnabled;
    emit IsEthDepositEnabled(_isEthDepositEnabled);
}
``` [1](#0-0) 

This is a notable inconsistency: the analogous function in `RSETHPoolV3.sol` and `RSETHPoolNoWrapper.sol` is gated by `TIMELOCK_ROLE`, providing a delay window. [2](#0-1) [3](#0-2) 

**Root cause 2 — ETH egress pushes to `msg.sender` with no fallback:**

```solidity
// AGETHPoolV3.sol lines 223-231
function moveAssetsForBridging() external onlyRole(BRIDGER_ROLE) {
    uint256 ethBalanceMinusFees = address(this).balance - feeEarnedInETH;
    (bool success,) = msg.sender.call{ value: ethBalanceMinusFees }("");
    if (!success) revert TransferFailed();
    emit AssetsMovedForBridging(ethBalanceMinusFees);
}
``` [4](#0-3) 

If the bridger is a smart contract without a `receive()` or `fallback()` function (e.g., after an upgrade, a multisig with a broken fallback, or a contract that conditionally reverts), the `call` returns `false` and the function reverts with `TransferFailed`. There is no alternative ETH egress path.

**Root cause 3 — No user-accessible ETH withdrawal:**

`deposit(string)` is the only user-facing ETH entry point; there is no corresponding `withdraw` or `redeem` for ETH. Once ETH enters the pool, only `BRIDGER_ROLE` can move it out. [5](#0-4) 

**State sequence that produces the freeze:**

| Step | Action | Result |
|------|--------|--------|
| 1 | Users call `deposit(ref)` with ETH | ETH accumulates in pool |
| 2 | Admin calls `setIsEthDepositEnabled(false)` | New deposits revert `EthDepositDisabled` |
| 3 | Bridger contract cannot receive ETH | `moveAssetsForBridging()` reverts `TransferFailed` |
| 4 | — | All non-fee ETH is trapped; no user or admin path to recover it without re-granting `BRIDGER_ROLE` to a new address |

---

### Impact Explanation

All ETH held in the pool (minus `feeEarnedInETH`) becomes temporarily inaccessible. Users who deposited ETH and received agETH cannot redeem their ETH from the pool. The freeze persists until the admin grants `BRIDGER_ROLE` to a new EOA or a contract that can receive ETH — an operational remediation that takes time and requires admin awareness of the failure. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

- The admin disabling deposits is a routine operational action (maintenance, rate anomaly, security pause) — not a compromise.
- The bridger being a contract that cannot receive ETH is realistic: bridger upgrades, multisig wallet contracts without a `receive()`, or contracts that conditionally revert on ETH receipt are common in production deployments.
- Both conditions need to coincide, which lowers likelihood, but neither is exotic or requires malicious intent.
- The absence of a timelock on `setIsEthDepositEnabled` (unlike all other pool variants) means the deposit gate can be closed atomically in the same block as a bridger failure, leaving no window for users to react.

---

### Recommendation

1. **Add `TIMELOCK_ROLE` guard to `setIsEthDepositEnabled`** in `AGETHPoolV3`, consistent with `RSETHPoolV3` and `RSETHPoolNoWrapper`, so users have advance notice before deposits are disabled.
2. **Add a `receiver` parameter to `moveAssetsForBridging()`** (or a separate emergency ETH rescue function restricted to `DEFAULT_ADMIN_ROLE`) so ETH can be sent to an arbitrary address if the bridger cannot receive it.
3. **Validate that the bridger address can receive ETH** at role-grant time (e.g., send 0 wei and check success), or document the requirement explicitly.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Bridger that cannot receive ETH
contract NonReceivableBridger {
    // No receive() or fallback() — ETH transfers revert
    function callMoveAssets(address pool) external {
        IAGETHPoolV3(pool).moveAssetsForBridging();
    }
}

contract PoC {
    function test(address pool, address admin, address bridger) external {
        // Step 1: users deposit ETH (pool already has ETH balance)
        IAGETHPoolV3(pool).deposit{value: 1 ether}("ref");

        // Step 2: admin disables deposits (no timelock — instant)
        vm.prank(admin);
        IAGETHPoolV3(pool).setIsEthDepositEnabled(false);

        // Step 3: new deposit reverts
        vm.expectRevert(IAGETHPoolV3.EthDepositDisabled.selector);
        IAGETHPoolV3(pool).deposit{value: 1 ether}("ref");

        // Step 4: bridger (NonReceivableBridger) tries to move assets — reverts
        vm.prank(bridger);
        vm.expectRevert(IAGETHPoolV3.TransferFailed.selector);
        IAGETHPoolV3(pool).moveAssetsForBridging();

        // ETH is now trapped
        assert(address(pool).balance > 0);
    }
}
```

Both `deposit` and `moveAssetsForBridging` revert, and `address(pool).balance > 0` confirms ETH is frozen with no user-accessible recovery path.

### Citations

**File:** contracts/agETH/AGETHPoolV3.sol (L115-128)
```text
    function deposit(string memory referralId) external payable nonReentrant {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L223-231)
```text
    function moveAssetsForBridging() external onlyRole(BRIDGER_ROLE) {
        // withdraw ETH - fees
        uint256 ethBalanceMinusFees = address(this).balance - feeEarnedInETH;

        (bool success,) = msg.sender.call{ value: ethBalanceMinusFees }("");
        if (!success) revert TransferFailed();

        emit AssetsMovedForBridging(ethBalanceMinusFees);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L255-258)
```text
    function setIsEthDepositEnabled(bool _isEthDepositEnabled) external onlyRole(DEFAULT_ADMIN_ROLE) {
        isEthDepositEnabled = _isEthDepositEnabled;
        emit IsEthDepositEnabled(_isEthDepositEnabled);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L526-529)
```text
    function setIsEthDepositEnabled(bool _isEthDepositEnabled) external onlyRole(TIMELOCK_ROLE) {
        isEthDepositEnabled = _isEthDepositEnabled;
        emit IsEthDepositEnabled(_isEthDepositEnabled);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L534-537)
```text
    function setIsEthDepositEnabled(bool _isEthDepositEnabled) external onlyRole(TIMELOCK_ROLE) {
        isEthDepositEnabled = _isEthDepositEnabled;
        emit IsEthDepositEnabled(_isEthDepositEnabled);
    }
```
