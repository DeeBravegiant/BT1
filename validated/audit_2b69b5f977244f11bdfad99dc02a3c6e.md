Audit Report

## Title
Uncollateralized `mint()` Temporarily Freezes Bridge Recipients' Funds — (`contracts/agETH/AGETHTokenWrapper.sol`)

## Summary
`AGETHTokenWrapper.mint()` mints wrapper shares to a recipient without requiring any `altAgETH` collateral to be present in the contract. When a user who received shares via the bridge path calls `withdraw()`, the `safeTransfer` in `_withdraw()` reverts due to insufficient contract balance, temporarily freezing their funds. The freeze persists until `BRIDGER_ROLE` calls `depositBridgerAssets()`, with no on-chain deadline enforced.

## Finding Description
`mint()` at L165–167 calls `_mint(_to, _amount)` with no balance or collateral check: [1](#0-0) 

`_withdraw()` at L111–119 burns shares first (`_burn` at L114), then calls `safeTransfer` at L116: [2](#0-1) 

If `ERC20Upgradeable(_asset).balanceOf(address(this)) < _amount`, the `safeTransfer` reverts. Because both the burn and transfer are in the same transaction, the entire call reverts atomically — the user's shares are preserved but the withdrawal fails. The contract's own comment on `depositBridgerAssets` at L138–139 explicitly confirms the intended two-step flow: shares are minted first, collateral is deposited separately afterward: [3](#0-2) 

There is no on-chain enforcement of timing between `mint()` and `depositBridgerAssets()`. `maxAmountToDepositBridgerAsset()` at L90–101 tracks the undercollateralization gap but does not block minting or enforce a deadline: [4](#0-3) 

## Impact Explanation
Every bridge event creates a window during which the recipient holds valid wrapper shares but cannot redeem them for `altAgETH`. The duration is entirely at the discretion of the off-chain bridger operator, with no on-chain upper bound. This is a concrete, reproducible **temporary freezing of funds** (Medium), which is an allowed impact in the program scope.

## Likelihood Explanation
This is the normal operating path for the bridge: the bridge contract (holding `MINTER_ROLE`) mints shares on L2 when a bridge message arrives, and the bridger separately deposits backing tokens. No adversarial action is required — every bridge deposit creates this freeze window. The affected user (Alice) is an unprivileged external user who simply bridged tokens; she does not need any privileged role to be impacted.

## Recommendation
Enforce collateral-before-mint ordering: require `depositBridgerAssets` to be called before or atomically with `mint()`, or restructure the bridge flow so collateral is transferred in the same transaction as minting. Alternatively, add a check in `_withdraw()` that queues withdrawals when the contract is undercollateralized, and enforce an on-chain deadline (e.g., a timestamp after which queued withdrawals can be processed or shares burned as a safety valve).

## Proof of Concept
```solidity
// 1. MINTER_ROLE (bridge contract) mints shares to Alice — zero altAgETH in contract
wrapper.mint(alice, 1e18);

// 2. Alice immediately tries to withdraw
vm.prank(alice);
wrapper.withdraw(altAgETH, 1e18);
// → reverts: ERC20: transfer amount exceeds balance
// Alice's shares are intact but she cannot access her funds

// 3. Only after BRIDGER_ROLE deposits collateral does withdrawal succeed
vm.prank(bridger);
altAgETHToken.approve(address(wrapper), 1e18);
wrapper.depositBridgerAssets(altAgETH, 1e18);

vm.prank(alice);
wrapper.withdraw(altAgETH, 1e18); // succeeds
```

### Citations

**File:** contracts/agETH/AGETHTokenWrapper.sol (L90-101)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrapped agETH minted
        uint256 agETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > agETHSupply) return 0;

        return agETHSupply - balanceOfAssetInWrapper;
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

**File:** contracts/agETH/AGETHTokenWrapper.sol (L138-151)
```text
    /// @dev Legacy function - Deposit for when the agETH is bridged by the
    /// bridger from L1 so as to collateralize already minted agETH on L2
    ///
    /// @param _asset The address of the token to deposit
    /// @param _amount The amount of tokens to deposit
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L165-167)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```
