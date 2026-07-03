### Title
Missing Zero-Address Validation for `_to` in `withdrawTo` Allows Accidental Permanent Fund Loss - (File: contracts/L2/RsETHTokenWrapper.sol, contracts/agETH/AGETHTokenWrapper.sol)

---

### Summary

`RsETHTokenWrapper.withdrawTo` and `AGETHTokenWrapper.withdrawTo` accept a caller-supplied `_to` address and pass it directly to the internal `_withdraw` function without validating that `_to != address(0)`. If a user accidentally supplies `address(0)`, their wrapper tokens are burned and the underlying altRsETH/altAgETH tokens are transferred to the zero address, resulting in permanent loss of funds.

---

### Finding Description

Both wrapper contracts expose a public `withdrawTo(address asset, address _to, uint256 _amount)` function that routes to an internal `_withdraw`. The internal function:

1. Burns the caller's wrapper tokens (`_burn(msg.sender, _amount)`)
2. Transfers the underlying asset to `_to` via `safeTransfer`

Neither the public entry point nor the internal function validates `_to != address(0)`.

In `RsETHTokenWrapper._withdraw`: [1](#0-0) 

In `AGETHTokenWrapper._withdraw`: [2](#0-1) 

The public entry points that expose this path to any caller: [3](#0-2) [4](#0-3) 

By contrast, the codebase already uses `UtilLib.checkNonZeroAddress` extensively elsewhere (e.g., in `_addAllowedToken` within `RsETHTokenWrapper`), and the utility is available: [5](#0-4) 

The `depositTo` counterpart is incidentally protected because OpenZeppelin's `_mint` internally reverts on `address(0)`. No such implicit protection exists for `_withdraw` — the safety depends entirely on whether the underlying altRsETH/altAgETH token's `transfer` implementation reverts on zero address. If the underlying token is non-standard or does not enforce this check, the burn is permanent and the transferred tokens are irrecoverably lost.

---

### Impact Explanation

If `_to == address(0)` is passed to `withdrawTo`:
- The caller's wrsETH (or agETH wrapper) tokens are permanently burned.
- The underlying altRsETH/altAgETH tokens are sent to `address(0)` and permanently lost.

This constitutes a **permanent freezing/destruction of user funds**. The wrapper contract itself provides no safety net; it silently delegates the zero-address guard to the underlying token, which may or may not enforce it.

---

### Likelihood Explanation

`withdrawTo` is a permissionless, externally callable function. Any user interacting with the wrapper — e.g., a bridge relayer, a smart contract integration, or a user via a frontend — can accidentally supply `address(0)` as `_to`. This is a realistic user error, especially in programmatic or contract-to-contract calls where the recipient address is computed dynamically and may be uninitialized. The original Sandclock judge confirmed this class of issue as valid: "assets are at risk due to external factors."

---

### Recommendation

Add an explicit zero-address check at the top of `_withdraw` in both contracts, consistent with the existing `UtilLib.checkNonZeroAddress` pattern used throughout the codebase:

```solidity
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    UtilLib.checkNonZeroAddress(_to);   // add this
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
```

Apply the same fix to `AGETHTokenWrapper._withdraw`.

---

### Proof of Concept

1. User holds 100 wrsETH on L2 (backed by 100 altRsETH held in `RsETHTokenWrapper`).
2. User (or a contract on their behalf) calls `withdrawTo(altRsETH, address(0), 100e18)`.
3. `_withdraw` executes: `_burn(msg.sender, 100e18)` — user's wrsETH is gone.
4. `ERC20Upgradeable(altRsETH).safeTransfer(address(0), 100e18)` — if the underlying token does not revert on zero-address transfer, 100 altRsETH are sent to `address(0)` and permanently lost.
5. The user has lost both their wrsETH and the underlying altRsETH with no recourse.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L92-94)
```text
    function withdrawTo(address asset, address _to, uint256 _amount) external {
        _withdraw(asset, _to, _amount);
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

**File:** contracts/agETH/AGETHTokenWrapper.sol (L83-85)
```text
    function withdrawTo(address asset, address _to, uint256 _amount) external {
        _withdraw(asset, _to, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L111-119)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, _to, _amount);
    }
```

**File:** contracts/utils/UtilLib.sol (L11-13)
```text
    function checkNonZeroAddress(address address_) internal pure {
        if (address_ == address(0)) revert ZeroAddressNotAllowed();
    }
```
