### Title
Arbitrary Alt-rsETH Swap via Multi-Token 1:1 Wrapper Allows Fund Theft - (File: contracts/L2/RsETHTokenWrapper.sol)

### Summary
`RsETHTokenWrapper` accepts multiple allowed alt-rsETH tokens and treats all of them as 1:1 equivalent to `wrsETH`. When two or more alt-rsETH tokens are allowed and they trade at different market prices, any unprivileged user can deposit the cheaper token and immediately withdraw the more expensive token, extracting the price difference from the wrapper's reserves.

### Finding Description
`RsETHTokenWrapper` maintains a mapping `allowedTokens` that can hold multiple alternative rsETH tokens. The `reinitialize` function explicitly adds a second allowed token, confirming the multi-token design is intentional. [1](#0-0) [2](#0-1) 

The public `deposit` and `withdraw` functions (and their `*To` variants) allow any caller to freely choose which allowed token to deposit and which to withdraw:

```solidity
// _deposit: accepts any allowedToken, mints wrsETH 1:1
function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    _mint(_to, _amount);
}

// _withdraw: accepts any allowedToken, burns wrsETH 1:1
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
}
``` [3](#0-2) [4](#0-3) 

There is no check that the deposited token and the withdrawn token are the same, and no oracle-based valuation — the contract blindly assumes all allowed tokens are worth exactly 1 wrsETH. Different bridge variants of rsETH (e.g., a LayerZero OFT rsETH vs. a Stargate-bridged rsETH) routinely trade at different prices on secondary markets, especially during bridge stress events.

The `depositBridgerAssets` function is the mechanism by which the bridger collateralizes the wrapper with a specific alt-rsETH token after wrsETH has been pre-minted by the pool. This means the wrapper will hold a specific alt-rsETH token that an attacker can target. [5](#0-4) 

### Impact Explanation
**Critical — Direct theft of user funds.**

An attacker deposits `N` units of the cheaper alt-rsETH token (Token A, market price `P_A`) and withdraws `N` units of the more expensive alt-rsETH token (Token B, market price `P_B > P_A`). Profit per round trip = `N * (P_B - P_A)`. The attack is limited only by the wrapper's balance of Token B and can be amplified with flash loans. The stolen value comes directly from the wrapper's reserves, which represent collateral backing wrsETH held by legitimate users — causing insolvency of the wrapper.

### Likelihood Explanation
**High.** The multi-token design is explicitly implemented (the `reinitialize` function adds a second allowed token). Different bridge variants of the same underlying asset routinely diverge in price on secondary markets. The attack requires no special role, no flash loan (though it amplifies profit), and no complex setup — just holding any amount of the cheaper alt-rsETH token. The entry path is the fully public, unguarded `deposit` and `withdraw` functions.

### Recommendation
Enforce that a user can only withdraw the same token type they deposited, or introduce a per-token accounting system that tracks how much of each token was deposited and limits withdrawals to that same token. Alternatively, restrict the wrapper to a single allowed token at a time, or use an oracle to value each token before minting/burning wrsETH.

```solidity
// Option: track per-user per-token deposits
mapping(address user => mapping(address token => uint256 balance)) public userTokenBalance;

function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    userTokenBalance[_to][_asset] += _amount;
    _mint(_to, _amount);
}

function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    require(userTokenBalance[msg.sender][_asset] >= _amount, "wrong token");
    userTokenBalance[msg.sender][_asset] -= _amount;
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
}
```

### Proof of Concept
Assume:
- `altRsETH_A` (Token A) is allowed in the wrapper, market price = 0.98 ETH
- `altRsETH_B` (Token B) is allowed in the wrapper (added via `reinitialize`), market price = 1.00 ETH
- The wrapper holds 1000 Token B (deposited by the bridger via `depositBridgerAssets`)

Attack steps:
1. Attacker buys 1000 Token A on the open market for 980 ETH.
2. Attacker calls `deposit(altRsETH_A, 1000e18)` → receives 1000 wrsETH.
3. Attacker calls `withdraw(altRsETH_B, 1000e18)` → burns 1000 wrsETH, receives 1000 Token B.
4. Attacker sells 1000 Token B for 1000 ETH.
5. **Net profit: 20 ETH** (2% of position), extracted from the wrapper's reserves.

The wrapper now holds 1000 Token A (worth 980 ETH) instead of 1000 Token B (worth 1000 ETH), creating a 20 ETH shortfall that makes wrsETH under-collateralized. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L24-24)
```text
    mapping(address allowedToken => bool isAllowed) public allowedTokens;
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L47-49)
```text
    function reinitialize(address _altRsETH) external reinitializer(2) onlyRole(DEFAULT_ADMIN_ROLE) {
        _addAllowedToken(_altRsETH);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L69-94)
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

    /// @dev Withdraw altRseth tokens from wrsETH
    /// @param asset The address of the token to withdraw
    /// @param _amount The amount of tokens to withdraw
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

**File:** contracts/L2/RsETHTokenWrapper.sol (L145-151)
```text
    function _addAllowedToken(address _asset) internal {
        UtilLib.checkNonZeroAddress(_asset);
        if (allowedTokens[_asset]) revert TokenAlreadyAllowed();

        allowedTokens[_asset] = true;
        emit TokenAdded(_asset);
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
