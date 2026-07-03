### Title
Fee-on-Transfer Token Deposit Mints Excess `wrsETH`, Permanently Undercollateralizing the Wrapper - (File: `contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary
`RsETHTokenWrapper._deposit` mints `_amount` of `wrsETH` based on the caller-supplied nominal amount without measuring the actual tokens received. If a fee-on-transfer (or rebasing-down) token is added to `allowedTokens`, every deposit silently mints more `wrsETH` than the collateral received, growing a deficit that permanently freezes the last withdrawers' funds.

---

### Finding Description
`_deposit` performs a `safeTransferFrom` for `_amount` and immediately mints exactly `_amount` of `wrsETH`:

```solidity
// contracts/L2/RsETHTokenWrapper.sol  lines 134-141
function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();

    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

    _mint(_to, _amount);          // ← mints nominal amount, not actual received
    emit Deposit(_asset, msg.sender, _to, _amount);
}
``` [1](#0-0) 

There is no balance-before / balance-after measurement. For a fee-on-transfer token the contract receives `_amount − fee` tokens but issues `_amount` `wrsETH`, creating an immediate shortfall. The `_withdraw` path burns `_amount` `wrsETH` and transfers `_amount` tokens back:

```solidity
// contracts/L2/RsETHTokenWrapper.sol  lines 120-128
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);   // ← transfers nominal amount
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
``` [2](#0-1) 

Because withdrawals transfer the full nominal amount, early redeemers drain the real collateral faster than `wrsETH` is burned, leaving later redeemers unable to withdraw.

The same pattern exists in `AGETHTokenWrapper._deposit`: [3](#0-2) 

And in `RSETHPoolV3.deposit(address,uint256,string)`, which uses the nominal `amount` to compute and mint `wrsETH`: [4](#0-3) 

And in `LRTDepositPool.depositAsset`, which computes `rsethAmountToMint` from `depositAmount` before the transfer and mints that amount regardless of what is actually received: [5](#0-4) 

---

### Impact Explanation
**Permanent freezing of funds (Medium → Critical depending on token value).**

The wrapper's invariant `totalSupply(wrsETH) == balanceOf(underlying)` is broken on every deposit of a fee-on-transfer token. Each deposit inflates `wrsETH` supply relative to real collateral. Early redeemers withdraw successfully; the last holders of `wrsETH` find the contract insolvent and their tokens permanently frozen. If the fee-on-transfer token is a high-value LST variant, the frozen amount can be substantial.

---

### Likelihood Explanation
**Low-to-Medium.** A fee-on-transfer token must first be added to `allowedTokens` by `TIMELOCK_ROLE` via `addAllowedToken`. This is a legitimate governance action — the admin may not know the token carries a transfer tax (e.g., some bridged LST variants, rebasing tokens, or tokens with protocol fees). Once such a token is listed, **any unprivileged user** can call the public `deposit` / `depositTo` functions to exploit the accounting gap. No front-running or special timing is required after listing. [6](#0-5) [7](#0-6) 

---

### Recommendation
Replace the fixed-amount mint with a balance-delta measurement:

```solidity
function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();

    uint256 balanceBefore = ERC20Upgradeable(_asset).balanceOf(address(this));
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    uint256 received = ERC20Upgradeable(_asset).balanceOf(address(this)) - balanceBefore;

    _mint(_to, received);          // mint only what was actually received
    emit Deposit(_asset, msg.sender, _to, received);
}
```

Apply the same fix to `AGETHTokenWrapper._deposit`, `RSETHPoolV3.deposit`, and `LRTDepositPool.depositAsset`. Additionally, document in the token-addition governance process that fee-on-transfer tokens require explicit review before being listed.

---

### Proof of Concept

1. Admin calls `addAllowedToken(feeToken)` where `feeToken` charges a 1 % transfer fee.
2. Alice calls `deposit(feeToken, 100e18)`.
   - `safeTransferFrom` moves `100e18` from Alice; contract receives `99e18` (1 % fee burned).
   - `_mint(Alice, 100e18)` — Alice holds `100e18 wrsETH`; contract holds `99e18 feeToken`.
3. Bob calls `deposit(feeToken, 100e18)`.
   - Contract receives another `99e18`; Bob is minted `100e18 wrsETH`.
   - State: `198e18 feeToken` in contract, `200e18 wrsETH` outstanding.
4. Alice calls `withdraw(feeToken, 100e18)`.
   - Burns `100e18 wrsETH`, transfers `100e18 feeToken` to Alice.
   - State: `98e18 feeToken` in contract, `100e18 wrsETH` outstanding (Bob's).
5. Bob calls `withdraw(feeToken, 100e18)`.
   - Attempts to transfer `100e18 feeToken` — contract only holds `98e18` → **reverts**.
   - Bob's `100e18 wrsETH` is permanently frozen; `2e18 feeToken` is unrecoverable.

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L69-79)
```text
    function deposit(address asset, uint256 _amount) external {
        _deposit(asset, msg.sender, _amount);
    }

    /// @dev Deposit altRsETH for wrsETH to a user
    /// @param asset The address of the token to deposit
    /// @param _to The user to send the XERC20 to
    /// @param _amount The amount of tokens to deposit
    function depositTo(address asset, address _to, uint256 _amount) external {
        _deposit(asset, _to, _amount);
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

**File:** contracts/L2/RsETHTokenWrapper.sol (L174-176)
```text
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L125-131)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, _to, _amount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L284-292)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/LRTDepositPool.sol (L111-117)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
```
