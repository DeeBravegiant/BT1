The vulnerability is real. Let me trace through the exact code.

**Root cause:** `maxAmountToDepositBridgerAsset` computes the cap as `totalSupply() - balanceOf(_asset)`, but when multiple tokens are allowed, `totalSupply()` reflects wrsETH minted from *all* tokens, while `balanceOf(_asset)` only reflects the one token being queried. The cap is therefore inflated by the balance of every *other* allowed token. [1](#0-0) 

**Concrete state trace:**

| Step | Action | `totalSupply` | `wrapper.tokenB` | `wrapper.tokenA` |
|---|---|---|---|---|
| 1 | `userB.deposit(tokenB, N)` | N | N | 0 |
| 2 | `maxAmountToDepositBridgerAsset(tokenA)` | — | — | — |
| — | = `N - 0 = N` (cap is N, not 0) | — | — | — |
| 3 | `bridger.depositBridgerAssets(tokenA, N)` | N | N | N |
| 4 | `userB.withdraw(tokenB, N)` | 0 | 0 | N |

After step 4: `totalSupply = 0`, `wrapper.tokenA = N`. The bridger's N tokenA is stranded — `withdraw(tokenA, N)` requires burning N wrsETH, but supply is zero. There is no `withdrawBridgerAssets` function. [2](#0-1) 

`depositBridgerAssets` transfers tokens in but never mints wrsETH, so the only exit path for bridger-deposited assets is through `_withdraw`, which requires a wrsETH holder to burn tokens. [3](#0-2) 

The correct cap should be `totalSupply() - sum(balanceOf(all_allowed_tokens))`. The single-token formula is only correct when exactly one token is ever allowed.

---

### Title
Cross-Token Cap Miscalculation Allows Bridger to Deposit Redundant Collateral, Permanently Locking Funds — (`contracts/L2/RsETHTokenWrapper.sol`)

### Summary
`maxAmountToDepositBridgerAsset(_asset)` returns `totalSupply() - balanceOf(_asset)`. When a second allowed token has already backed the full wrsETH supply, this formula returns a non-zero cap for the first token, permitting the bridger to deposit it even though the supply is already fully collateralized. The deposited tokens become permanently unrecoverable because `depositBridgerAssets` does not mint wrsETH, and the only withdrawal path (`_withdraw`) requires burning wrsETH that no longer exists.

### Finding Description
`RsETHTokenWrapper` supports multiple allowed tokens via `addAllowedToken` (TIMELOCK_ROLE). The bridger cap formula at line 109 is:

```solidity
return wrsETHSupply - balanceOfAssetInWrapper;
```

This is only correct when a single token is allowed. With two tokens (tokenA, tokenB):

- `userB` deposits N tokenB → `totalSupply = N`, `wrapper.tokenB = N`
- `maxAmountToDepositBridgerAsset(tokenA)` = `N - 0 = N`
- Bridger calls `depositBridgerAssets(tokenA, N)` → N tokenA enters wrapper, no wrsETH minted
- `userB` withdraws N tokenB → burns N wrsETH, `totalSupply = 0`
- Wrapper now holds N tokenA with zero wrsETH supply; no one can call `withdraw(tokenA, ...)` because it requires burning wrsETH

There is no `withdrawBridgerAssets` or emergency recovery function in the contract.

### Impact Explanation
The bridger's tokenA is permanently locked in the wrapper. Recovery requires an admin to use the `mint` function (MINTER_ROLE) to create synthetic wrsETH for the bridger to burn — an out-of-band administrative action not part of normal operation. Until that occurs, the bridger's funds are frozen.

**Scoped impact: Medium — Temporary freezing of funds** (permanent without admin intervention via `mint`).

### Likelihood Explanation
- `addAllowedToken` is gated by TIMELOCK_ROLE and is already used in `reinitialize`, so multi-token operation is an intended deployment scenario.
- The bridger acts in good faith: it bridges tokenA from L1 to back wrsETH on L2, unaware that the supply is already backed by tokenB.
- No malicious actor is required; the bridger simply follows the cap returned by the contract.

### Recommendation
Replace the single-asset cap formula with one that sums the balances of all allowed tokens:

```solidity
// pseudocode
uint256 totalBacking = sum over all allowedTokens of ERC20(token).balanceOf(address(this));
return wrsETHSupply > totalBacking ? wrsETHSupply - totalBacking : 0;
```

This requires maintaining an enumerable set of allowed tokens (e.g., OpenZeppelin `EnumerableSet`) so the sum can be computed on-chain.

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Foundry test (local fork or unit test)
function test_crossTokenCapMiscalculation() public {
    // Setup: deploy wrapper with tokenA; add tokenB
    wrapper.addAllowedToken(address(tokenB)); // TIMELOCK_ROLE

    // userB deposits N tokenB → mints N wrsETH
    uint256 N = 1000e18;
    tokenB.mint(userB, N);
    vm.prank(userB);
    tokenB.approve(address(wrapper), N);
    vm.prank(userB);
    wrapper.deposit(address(tokenB), N);

    // Cap for tokenA should be 0 (supply already fully backed by tokenB)
    // but the contract returns N
    assertEq(wrapper.maxAmountToDepositBridgerAsset(address(tokenA)), N); // BUG

    // Bridger deposits N tokenA (passes cap check)
    tokenA.mint(bridger, N);
    vm.prank(bridger);
    tokenA.approve(address(wrapper), N);
    vm.prank(bridger);
    wrapper.depositBridgerAssets(address(tokenA), N);

    // userB withdraws tokenB — succeeds, drains tokenB balance
    vm.prank(userB);
    wrapper.withdraw(address(tokenB), N);

    // Invariant: wrapper should hold 0 tokenA (bridger's deposit was redundant)
    // Actual: wrapper holds N tokenA, totalSupply = 0 → permanently locked
    assertEq(tokenA.balanceOf(address(wrapper)), N);   // locked
    assertEq(wrapper.totalSupply(), 0);                // no wrsETH to burn for recovery
}
``` [1](#0-0) [2](#0-1) [4](#0-3)

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

**File:** contracts/L2/RsETHTokenWrapper.sol (L172-176)
```text
    /// @dev Add a token to the allowed tokens list
    /// @param _asset The address of the token to add
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }
```
