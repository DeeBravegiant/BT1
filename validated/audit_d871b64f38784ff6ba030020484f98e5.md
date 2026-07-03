### Title
Fee-on-Transfer Token Support Causes wrsETH Over-Minting and Withdrawal Shortfall - (File: contracts/L2/RsETHTokenWrapper.sol)

### Summary
`RsETHTokenWrapper._deposit()` mints wrsETH equal to the caller-supplied `_amount` parameter without measuring the actual tokens received after `safeTransferFrom`. If any `allowedToken` (altRsETH) exhibits fee-on-transfer behaviour, the wrapper's wrsETH total supply permanently exceeds its altRsETH backing, making the last redeemers unable to withdraw.

### Finding Description
`RsETHTokenWrapper._deposit()` executes the following sequence:

```solidity
// contracts/L2/RsETHTokenWrapper.sol  lines 134-141
function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();

    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

    _mint(_to, _amount);          // ← mints _amount, not actual received
    emit Deposit(_asset, msg.sender, _to, _amount);
}
``` [1](#0-0) 

The contract trusts `_amount` as the received quantity. No before/after `balanceOf` check is performed. If the altRsETH token deducts a transfer fee, the wrapper receives `_amount - fee` but mints `_amount` wrsETH, creating an immediate deficit.

The symmetric `_withdraw()` function burns `_amount` wrsETH and then calls `safeTransfer(to, _amount)`:

```solidity
// contracts/L2/RsETHTokenWrapper.sol  lines 120-128
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();

    _burn(msg.sender, _amount);

    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    ...
}
``` [2](#0-1) 

Because the wrapper holds less altRsETH than the outstanding wrsETH supply, the `safeTransfer` call will revert for the last redeemers once the real balance is exhausted.

The same pattern exists in `LRTDepositPool.depositAsset()`, where `rsethAmountToMint` is computed from the caller-supplied `depositAmount` before the transfer, and rsETH is minted against that figure rather than the actual received amount:

```solidity
// contracts/LRTDepositPool.sol  lines 111-117
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
_mintRsETH(rsethAmountToMint);
``` [3](#0-2) 

### Impact Explanation
**Medium – Temporary (potentially permanent) freezing of funds.**

Every deposit with a fee-on-transfer altRsETH token inflates wrsETH supply relative to the real backing. As the deficit accumulates, later `withdraw` / `withdrawTo` callers receive a revert from `safeTransfer` because the contract balance is insufficient. Funds belonging to those users are frozen inside the wrapper until an external party donates the missing tokens. In the worst case (no donation), the freeze is permanent for the last cohort of holders.

### Likelihood Explanation
**Low-Medium.** The `allowedTokens` whitelist is controlled by `TIMELOCK_ROLE`, so the protocol must explicitly add a fee-on-transfer altRsETH. However, the contract contains no guard that prevents such a token from being added, and cross-chain bridge variants of rsETH (e.g., Stargate-wrapped, Linea-canonical) could in principle carry transfer fees or be upgraded to do so. The `reinitialize` path also allows a second altRsETH to be added:

```solidity
// contracts/L2/RsETHTokenWrapper.sol  lines 47-49
function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
    _addAllowedToken(_altRsETH);
}
``` [4](#0-3) 

### Recommendation
1. **Measure actual received amount** using a before/after balance check in `_deposit`:
   ```solidity
   uint256 before = ERC20Upgradeable(_asset).balanceOf(address(this));
   ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
   uint256 received = ERC20Upgradeable(_asset).balanceOf(address(this)) - before;
   _mint(_to, received);
   ```
2. Apply the same fix to `LRTDepositPool.depositAsset()` — compute `rsethAmountToMint` from the actual received amount, not from `depositAmount`.
3. Alternatively, document and enforce (via an invariant check in `_addAllowedToken`) that only non-fee-on-transfer, non-rebasing tokens may be whitelisted.

### Proof of Concept
1. Admin adds a fee-on-transfer altRsETH token (1% fee) to `allowedTokens` via `addAllowedToken`.
2. Alice calls `deposit(altRsETH, 1000e18)`.
   - Wrapper receives `990e18` altRsETH (1% fee taken).
   - Wrapper mints `1000e18` wrsETH to Alice.
3. Bob calls `deposit(altRsETH, 1000e18)`.
   - Wrapper receives `990e18` altRsETH.
   - Wrapper mints `1000e18` wrsETH to Bob.
4. Wrapper state: `totalSupply = 2000e18 wrsETH`, `altRsETH.balanceOf(wrapper) = 1980e18`.
5. Alice calls `withdraw(altRsETH, 1000e18)` — succeeds, wrapper now holds `980e18`.
6. Bob calls `withdraw(altRsETH, 1000e18)` — **reverts** (`ERC20: transfer amount exceeds balance`). Bob's `1000e18` wrsETH is burned but he receives nothing; his funds are frozen. [1](#0-0) [2](#0-1)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
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

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L111-117)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
```
