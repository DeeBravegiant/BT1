### Title
`maxAmountToDepositBridgerAsset` Ignores Multi-Token Collateral, Enabling Over-Collateralization and Permanent Fund Lock — (`contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary

`maxAmountToDepositBridgerAsset` computes available deposit capacity for a single token in isolation against `totalSupply`, without summing the balances of all other allowed tokens. When a second allowed token already fully (or partially) backs the outstanding wrsETH supply, the function returns a non-zero allowance for the first token, permitting the bridger to deposit excess collateral that can never be recovered.

---

### Finding Description

`maxAmountToDepositBridgerAsset` is implemented as:

```solidity
function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
    if (!allowedTokens[_asset]) return 0;
    uint256 wrsETHSupply = totalSupply();
    uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));
    if (balanceOfAssetInWrapper > wrsETHSupply) return 0;
    return wrsETHSupply - balanceOfAssetInWrapper;
}
``` [1](#0-0) 

The correct invariant the contract must maintain is:

> `sum(allowedToken[i].balanceOf(wrapper) for all i) <= totalSupply()`

But the function only checks `tokenA.balanceOf(wrapper)` against `totalSupply()`, completely ignoring `tokenB.balanceOf(wrapper)`.

`depositBridgerAssets` enforces no additional check beyond this function:

```solidity
function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
    if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
        revert CannotDeposit();
    }
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    emit BridgerDeposited(_asset, msg.sender, _amount);
}
``` [2](#0-1) 

There is no `recover`, `sweep`, or admin-withdrawal function anywhere in the contract. The only egress path for tokens is `_withdraw`, which burns wrsETH 1:1:

```solidity
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    ...
}
``` [3](#0-2) 

Since `totalSupply()` wrsETH can only redeem `totalSupply()` tokens in aggregate across all assets, any excess collateral deposited via `depositBridgerAssets` is permanently irrecoverable.

---

### Impact Explanation

**Concrete scenario:**

| Step | tokenA balance | tokenB balance | wrsETH totalSupply |
|------|---------------|---------------|-------------------|
| Initial (tokenB bridges 100) | 0 | 100 | 100 |
| `maxAmountToDepositBridgerAsset(tokenA)` returns | — | — | **100** (wrong; should be 0) |
| Bridger calls `depositBridgerAssets(tokenA, 100)` | 100 | 100 | 100 |

After step 3: the wrapper holds 200 tokens of collateral but only 100 wrsETH exists. The 100 excess tokenA are permanently locked — no wrsETH exists to burn and redeem them. The bridger's 100 tokenA (including all future yield accrued on those tokens) is frozen forever.

Impact: **Medium — Permanent freezing of unclaimed yield** (the yield accrued on the excess locked altRsETH tokens is permanently inaccessible). The locked principal itself also constitutes **Critical — Permanent freezing of funds** for the bridger.

---

### Likelihood Explanation

- Requires two allowed tokens, which is an explicitly supported configuration via `reinitialize` and `addAllowedToken`.
- The bridger is expected to call `maxAmountToDepositBridgerAsset` to determine how much to deposit — this is the function's stated purpose.
- No malicious intent is required; a correctly-behaving bridger following the contract's own view function will trigger this.
- The only prerequisite is that both tokens are active and one already partially or fully backs the supply — a normal operational state.

---

### Recommendation

Replace the per-asset balance check with the aggregate collateral across all allowed tokens. Since `allowedTokens` is a mapping (not enumerable), the contract should either:

1. Track an `address[] allowedTokenList` array alongside the mapping and sum all balances in `maxAmountToDepositBridgerAsset`, or
2. Track a single `uint256 totalCollateral` state variable incremented/decremented on every deposit/withdraw/depositBridgerAssets call, and use `totalSupply() - totalCollateral` as the cap.

Additionally, add an admin recovery function (e.g., `recoverExcessCollateral`) to handle any already-locked surplus.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import {RsETHTokenWrapper} from "contracts/L2/RsETHTokenWrapper.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockAltRsETH is ERC20 {
    constructor(string memory name) ERC20(name, name) { _mint(msg.sender, 1000e18); }
}

contract OverCollateralTest is Test {
    RsETHTokenWrapper wrapper;
    MockAltRsETH tokenA;
    MockAltRsETH tokenB;
    address admin = address(1);
    address bridger = address(2);
    address minter = address(3);
    address user = address(4);

    function setUp() public {
        tokenA = new MockAltRsETH("tokenA");
        tokenB = new MockAltRsETH("tokenB");

        vm.startPrank(admin);
        wrapper = new RsETHTokenWrapper();
        wrapper.initialize(admin, bridger, address(tokenA));
        wrapper.reinitialize(address(tokenB)); // adds tokenB
        wrapper.grantRole(wrapper.MINTER_ROLE(), minter);
        wrapper.grantRole(wrapper.TIMELOCK_ROLE(), admin);
        vm.stopPrank();

        // Distribute tokens
        tokenA.transfer(bridger, 200e18);
        tokenB.transfer(bridger, 200e18);
    }

    function test_overCollateralization() public {
        // Step 1: Mint 100 wrsETH (simulating cross-chain mint)
        vm.prank(minter);
        wrapper.mint(user, 100e18);
        assertEq(wrapper.totalSupply(), 100e18);

        // Step 2: Bridger deposits tokenB to fully collateralize
        vm.startPrank(bridger);
        tokenB.approve(address(wrapper), 100e18);
        wrapper.depositBridgerAssets(address(tokenB), 100e18);
        vm.stopPrank();

        // Invariant: total collateral == totalSupply
        uint256 totalCollateral = tokenA.balanceOf(address(wrapper)) + tokenB.balanceOf(address(wrapper));
        assertEq(totalCollateral, wrapper.totalSupply()); // passes: 100 == 100

        // Step 3: maxAmountToDepositBridgerAsset(tokenA) incorrectly returns 100 (should be 0)
        uint256 maxDeposit = wrapper.maxAmountToDepositBridgerAsset(address(tokenA));
        assertEq(maxDeposit, 100e18); // BUG: returns 100, not 0

        // Step 4: Bridger deposits tokenA — succeeds despite full collateralization
        vm.startPrank(bridger);
        tokenA.approve(address(wrapper), 100e18);
        wrapper.depositBridgerAssets(address(tokenA), 100e18);
        vm.stopPrank();

        // Step 5: Invariant broken — 200 collateral for 100 wrsETH
        totalCollateral = tokenA.balanceOf(address(wrapper)) + tokenB.balanceOf(address(wrapper));
        assertEq(totalCollateral, 200e18); // 200 != 100
        assertEq(wrapper.totalSupply(), 100e18);

        // Step 6: 100 tokenA permanently stuck — only 100 wrsETH can ever be burned
        // No recovery function exists in the contract
    }
}
```

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L99-110)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrsETH minted
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L162-170)
```text
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, msg.sender, _amount);
    }
```
