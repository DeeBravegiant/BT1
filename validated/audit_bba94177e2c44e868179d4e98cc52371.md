### Title
Per-Asset Collateral Cap Allows N×totalSupply Tokens Locked Against totalSupply wrsETH, Permanently Freezing (N-1)×totalSupply Tokens — (`contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary

`maxAmountToDepositBridgerAsset` computes a cap using only the balance of the specific asset being deposited, not the aggregate balance of all allowed tokens. When multiple tokens are allowed, the bridger can deposit up to `totalSupply()` of **each** token independently, locking N×totalSupply collateral against only 1×totalSupply redeemable wrsETH. The excess (N-1)×totalSupply tokens are permanently frozen with no recovery path.

---

### Finding Description

`maxAmountToDepositBridgerAsset` is defined as:

```solidity
uint256 wrsETHSupply = totalSupply();
uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));
if (balanceOfAssetInWrapper > wrsETHSupply) return 0;
return wrsETHSupply - balanceOfAssetInWrapper;
``` [1](#0-0) 

The cap is computed **per asset in isolation**. It does not account for the balances of any other allowed tokens already held by the wrapper. The contract explicitly supports multiple allowed tokens:

- `initialize` adds the first token [2](#0-1) 
- `reinitialize` adds a second token [3](#0-2) 
- `addAllowedToken` (TIMELOCK_ROLE) can add further tokens [4](#0-3) 

`depositBridgerAssets` enforces only the per-asset cap before transferring tokens in — it does **not** mint any wrsETH:

```solidity
function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
    if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
        revert CannotDeposit();
    }
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    emit BridgerDeposited(_asset, msg.sender, _amount);
}
``` [5](#0-4) 

`_withdraw` burns exactly `_amount` wrsETH and transfers exactly `_amount` of one specific asset: [6](#0-5) 

There is no sweep, rescue, or aggregate-balance recovery function anywhere in the contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

With two allowed tokens and `totalSupply() = S`:

| Step | tokenA balance | tokenB balance | totalSupply (wrsETH) |
|------|---------------|---------------|----------------------|
| After `mint(S)` | 0 | 0 | S |
| After `depositBridgerAssets(tokenA, S)` | S | 0 | S |
| After `depositBridgerAssets(tokenB, S)` | S | S | S |

The wrapper now holds `2S` tokens. Users collectively hold `S` wrsETH. Burning all `S` wrsETH can redeem at most `S` tokens (of one type). The remaining `S` tokens of the other type are permanently locked — there is no wrsETH left to burn, no admin rescue function, and no other withdrawal path.

---

### Likelihood Explanation

**Medium.** The `BRIDGER_ROLE` is an automated bridge operator, not a human making deliberate choices. The contract's own cap check passes for each token independently, so the bridger is acting within the contract's stated rules. This can occur without malicious intent whenever:
1. The system has two allowed tokens (already the case after `reinitialize`), and
2. The bridger deposits collateral for both tokens in sequence (a normal operational pattern if both tokens are bridged from L1).

No governance capture or key compromise is required — the bridger simply follows the contract's own validation logic.

---

### Recommendation

Replace the per-asset cap with an aggregate cap across all allowed tokens. The correct invariant is:

```
sum(allowedToken[i].balanceOf(wrapper) for all i) + _amount <= totalSupply()
```

This requires either:
- Maintaining an on-chain array of allowed tokens (to iterate over), or
- Tracking a single `totalCollateralDeposited` counter that is incremented in `depositBridgerAssets` and decremented in `_withdraw`.

The simpler fix is a tracked aggregate:

```solidity
uint256 public totalBridgerDeposited;

function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
    if (totalBridgerDeposited + _amount > totalSupply()) revert CannotDeposit();
    totalBridgerDeposited += _amount;
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    emit BridgerDeposited(_asset, msg.sender, _amount);
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "contracts/L2/RsETHTokenWrapper.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockToken is ERC20 {
    constructor(string memory name) ERC20(name, name) {
        _mint(msg.sender, 1000e18);
    }
}

contract WrapperInvariantTest is Test {
    RsETHTokenWrapper wrapper;
    MockToken tokenA;
    MockToken tokenB;
    address admin = address(1);
    address bridger = address(2);
    address minter = address(3);

    function setUp() public {
        tokenA = new MockToken("tokenA");
        tokenB = new MockToken("tokenB");

        wrapper = new RsETHTokenWrapper();
        wrapper.initialize(admin, bridger, address(tokenA));

        // Admin adds tokenB as allowed
        vm.prank(admin);
        // (grant TIMELOCK_ROLE to admin for test)
        wrapper.grantRole(wrapper.TIMELOCK_ROLE(), admin);
        vm.prank(admin);
        wrapper.addAllowedToken(address(tokenB));

        // Grant MINTER_ROLE to minter
        vm.prank(admin);
        wrapper.grantRole(wrapper.MINTER_ROLE(), minter);

        // Fund bridger
        tokenA.transfer(bridger, 200e18);
        tokenB.transfer(bridger, 200e18);
    }

    function test_permanentFreeze() public {
        // Step 1: Mint 100e18 wrsETH (simulates cross-chain mint)
        vm.prank(minter);
        wrapper.mint(address(this), 100e18);
        assertEq(wrapper.totalSupply(), 100e18);

        // Step 2: Bridger deposits tokenA — cap = 100-0 = 100, passes
        vm.startPrank(bridger);
        tokenA.approve(address(wrapper), 100e18);
        wrapper.depositBridgerAssets(address(tokenA), 100e18);

        // Step 3: Bridger deposits tokenB — cap = 100-0 = 100, ALSO passes
        tokenB.approve(address(wrapper), 100e18);
        wrapper.depositBridgerAssets(address(tokenB), 100e18);
        vm.stopPrank();

        // Invariant violated: 200e18 tokens locked, only 100e18 wrsETH to redeem
        uint256 totalLocked = tokenA.balanceOf(address(wrapper)) + tokenB.balanceOf(address(wrapper));
        assertEq(totalLocked, 200e18);
        assertEq(wrapper.totalSupply(), 100e18);

        // Burning all wrsETH can only recover 100e18 of ONE token
        // The other 100e18 is permanently frozen — no recovery path exists
        assertGt(totalLocked, wrapper.totalSupply(), "INVARIANT BROKEN: excess tokens permanently frozen");
    }
}
```

Running this test on unmodified code will pass, confirming the invariant is broken and 100e18 tokens are permanently frozen.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L63-63)
```text
        _addAllowedToken(_altRsETH);
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L103-109)
```text
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
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

**File:** contracts/L2/RsETHTokenWrapper.sol (L174-176)
```text
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }
```
