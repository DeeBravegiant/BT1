Audit Report

## Title
Cross-Asset Withdrawal Enables Direct Theft of Higher-Value Tokens from Wrapper - (File: contracts/L2/RsETHTokenWrapper.sol)

## Summary
`RsETHTokenWrapper` supports multiple allowed alt-rsETH tokens but tracks only a unified `wrsETH` balance with no record of which asset was deposited. Any holder of `wrsETH` can freely withdraw any allowed asset, regardless of what they deposited. If two allowed tokens temporarily diverge in price, an attacker can deposit the cheaper token and withdraw the more expensive one, directly stealing funds from honest depositors.

## Finding Description
The contract maintains `mapping(address allowedToken => bool isAllowed) public allowedTokens` and is explicitly designed to hold multiple tokens: `reinitialize` adds a second alt-rsETH token, and `addAllowedToken` (TIMELOCK_ROLE) can add further tokens. [1](#0-0) [2](#0-1) 

`_deposit` transfers any allowed asset in and mints `wrsETH` with no record of which asset was used: [3](#0-2) 

`_withdraw` burns `wrsETH` and transfers any allowed asset out, with the only guard being `allowedTokens[_asset]` and sufficient `wrsETH` balance: [4](#0-3) 

There is no `mapping(depositor => asset)` or any per-asset accounting. The existing `allowedTokens` check is necessary but not sufficient — it confirms the asset is listed, but does not enforce that the withdrawn asset matches the deposited asset. The `_burn` at line 123 only verifies the caller holds enough `wrsETH`, not how it was obtained.

## Impact Explanation
**Critical — Direct theft of user funds.**

When two allowed tokens diverge in price (e.g., tokenA at 0.95, tokenB at 1.00), an attacker deposits N tokenA (cheap), receives N `wrsETH`, then withdraws N tokenB (expensive). The N tokenB is taken directly from the pool of honest tokenB depositors, who are left holding `wrsETH` redeemable only for the now-depleted tokenB balance or the cheaper tokenA. This is a concrete, immediate loss of principal for honest users — not unclaimed yield, not a temporary freeze, but direct theft of at-rest funds.

## Likelihood Explanation
**Medium.**

The precondition — two tokens listed simultaneously — is a normal, intended protocol state, not an attack. The `reinitialize` function exists specifically to add a second alt-rsETH token, and `addAllowedToken` is a routine governance operation. Once two tokens are listed, the attack requires zero privileges: any external account holding `wrsETH` or the cheaper alt-rsETH can execute it atomically in a single transaction. Bridge-wrapped alt-rsETH tokens on L2 are subject to temporary price divergence during bridge congestion, liquidity events, or oracle delays. The SECURITY.md exclusion for "depegging of an external stablecoin" does not apply — alt-rsETH tokens are liquid staking token wrappers, not stablecoins. [5](#0-4) 

## Recommendation
Track per-user, per-asset deposit balances and enforce that withdrawals use the same asset:

```solidity
mapping(address user => mapping(address asset => uint256 amount)) public depositedAsset;

function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    depositedAsset[_to][_asset] += _amount;
    _mint(_to, _amount);
    emit Deposit(_asset, msg.sender, _to, _amount);
}

function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    require(depositedAsset[msg.sender][_asset] >= _amount, "wrong asset");
    depositedAsset[msg.sender][_asset] -= _amount;
    _burn(msg.sender, _amount);
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
```

Alternatively, issue separate wrapper tokens per allowed asset so cross-asset redemption is structurally impossible.

## Proof of Concept
**Preconditions:** TIMELOCK_ROLE has called `addAllowedToken(tokenB)` so both `tokenA` and `tokenB` are in `allowedTokens`. `tokenA` trades at 0.95 ETH, `tokenB` at 1.00 ETH. The wrapper holds 1000 `tokenB` from honest depositors.

```
1. Attacker acquires 100 tokenA for 95 ETH on the open market.
2. Attacker calls wrapper.deposit(tokenA, 100e18)
   → _deposit: allowedTokens[tokenA] == true ✓
   → transfers 100 tokenA into wrapper
   → mints 100 wrsETH to attacker
3. Attacker calls wrapper.withdraw(tokenB, 100e18)
   → _withdraw: allowedTokens[tokenB] == true ✓
   → burns 100 wrsETH from attacker
   → transfers 100 tokenB to attacker
4. Attacker sells 100 tokenB for 100 ETH.
   Net profit: 5 ETH. Honest tokenB depositors are short 100 tokenB.
```

**Foundry invariant test plan:** Deploy wrapper with two mock ERC20 tokens both in `allowedTokens`. Fund honest users who deposit only tokenB. Fuzz attacker deposits of tokenA followed by tokenB withdrawals. Assert invariant: `tokenB.balanceOf(wrapper) >= sum of tokenB deposited by honest users`. The invariant will break whenever tokenA and tokenB are treated as non-equivalent. [4](#0-3) [3](#0-2)

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

**File:** contracts/L2/RsETHTokenWrapper.sol (L172-176)
```text
    /// @dev Add a token to the allowed tokens list
    /// @param _asset The address of the token to add
    function addAllowedToken(address _asset) external onlyRole(TIMELOCK_ROLE) {
        _addAllowedToken(_asset);
    }
```
