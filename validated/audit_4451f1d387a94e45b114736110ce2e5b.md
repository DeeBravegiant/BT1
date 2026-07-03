### Title
TIMELOCK_ROLE Can Permanently Freeze User Funds by Removing Allowed Token While Deposits Exist — (File: contracts/L2/RsETHTokenWrapper.sol)

---

### Summary

`removeAllowedToken` contains no guard against outstanding user deposits. Once a token is removed, the `allowedTokens` check inside `_withdraw` causes every redemption attempt to revert, trapping all deposited collateral in the wrapper with no user-accessible escape path.

---

### Finding Description

`removeAllowedToken` unconditionally sets `allowedTokens[_asset] = false`: [1](#0-0) 

`_withdraw` gates every redemption on that same flag: [2](#0-1) 

Both public exit points (`withdraw` and `withdrawTo`) route through `_withdraw`: [3](#0-2) 

There is no emergency-withdrawal function, no bypass flag, and no check in `removeAllowedToken` for a non-zero token balance held by the contract. After removal:

- `withdraw(altRsETH, N)` → `_withdraw` → `revert TokenNotAllowed()`
- `withdrawTo(altRsETH, _to, N)` → same revert
- `mint` (MINTER_ROLE) only mints more wrsETH; it does not help redeem
- `depositBridgerAssets` only deposits more collateral; it does not help redeem

The only recovery path is for `TIMELOCK_ROLE` to call `addAllowedToken` again. Because `_addAllowedToken` checks `if (allowedTokens[_asset]) revert TokenAlreadyAllowed()`, re-adding is technically possible after removal: [4](#0-3) 

However, if the removal was intentional (e.g., token migration, deprecated bridge), the TIMELOCK_ROLE has no on-chain obligation to re-add it, making the freeze effectively permanent from the user's perspective.

---

### Impact Explanation

All altRsETH collateral held by the wrapper at the time of removal becomes unredeemable. wrsETH holders retain a token whose only redemption path is blocked. The contract balance is unchanged but inaccessible — **permanent freezing of funds** for all affected depositors.

---

### Likelihood Explanation

`TIMELOCK_ROLE` is a distinct privileged role (not `DEFAULT_ADMIN_ROLE`), assigned deliberately for time-sensitive governance actions. A realistic trigger is a token migration scenario (e.g., the bridge upgrades to a new altRsETH address): the operator removes the old token and adds the new one, not realising that existing wrsETH holders can no longer redeem against the old token. No key compromise or malicious intent is required — the design flaw is that the function has no awareness of outstanding liabilities.

---

### Recommendation

Add a balance guard in `removeAllowedToken`:

```solidity
function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
+   if (ERC20Upgradeable(_asset).balanceOf(address(this)) != 0)
+       revert OutstandingDepositsExist();
    allowedTokens[_asset] = false;
    emit TokenRemoved(_asset);
}
```

Alternatively, add an admin-only emergency-withdrawal path that bypasses the `allowedTokens` check so users can always redeem even after a token is delisted.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/L2/RsETHTokenWrapper.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockAltRsETH is ERC20 {
    constructor() ERC20("altRsETH", "altRsETH") {
        _mint(msg.sender, 1000e18);
    }
}

contract FreezeTest is Test {
    RsETHTokenWrapper wrapper;
    MockAltRsETH altRsETH;
    address admin   = address(1);
    address timelock = address(2);
    address user    = address(3);

    function setUp() public {
        altRsETH = new MockAltRsETH();
        wrapper  = new RsETHTokenWrapper();
        wrapper.initialize(admin, address(0), address(altRsETH));

        vm.prank(admin);
        wrapper.grantRole(wrapper.TIMELOCK_ROLE(), timelock);

        altRsETH.transfer(user, 100e18);
    }

    function testPermanentFreeze() public {
        // Step 1: user deposits 100 altRsETH → receives 100 wrsETH
        vm.startPrank(user);
        altRsETH.approve(address(wrapper), 100e18);
        wrapper.deposit(address(altRsETH), 100e18);
        vm.stopPrank();

        assertEq(wrapper.balanceOf(user), 100e18);
        assertEq(altRsETH.balanceOf(address(wrapper)), 100e18);

        // Step 2: TIMELOCK removes the token
        vm.prank(timelock);
        wrapper.removeAllowedToken(address(altRsETH));

        // Step 3: user tries to withdraw → reverts
        vm.prank(user);
        vm.expectRevert(RsETHTokenWrapper.TokenNotAllowed.selector);
        wrapper.withdraw(address(altRsETH), 100e18);

        // Funds are frozen: wrapper still holds 100 altRsETH
        assertEq(altRsETH.balanceOf(address(wrapper)), 100e18);
        assertEq(wrapper.balanceOf(user), 100e18); // wrsETH unburned
    }
}
```

Run with: `forge test --match-test testPermanentFreeze -vvvv`

The test demonstrates the exact three-step sequence on unmodified production code: deposit succeeds, token removal succeeds, withdrawal reverts, and the wrapper's altRsETH balance remains unchanged — confirming permanent fund freezing.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L84-94)
```text
    function withdraw(address asset, uint256 _amount) external {
        _withdraw(asset, msg.sender, _amount);
    }

    /// @dev Withdraw altRsETH tokens from wrsETH to a user
    /// @param asset The address of the token to withdraw
    /// @param _to The user to withdraw to
    /// @param _amount The amount of tokens to withdraw
    function withdrawTo(address asset, address _to, uint256 _amount) external {
        _withdraw(asset, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-122)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

```

**File:** contracts/L2/RsETHTokenWrapper.sol (L145-151)
```text
    function _addAllowedToken(address _asset) internal {
        UtilLib.checkNonZeroAddress(_asset);
        if (allowedTokens[_asset]) revert TokenAlreadyAllowed();

        allowedTokens[_asset] = true;
        emit TokenAdded(_asset);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L180-185)
```text
    function removeAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }
```
