### Title
Donation Attack on `AGETHTokenWrapper` Permanently Blocks Bridger Collateralization via `depositBridgerAssets` — (`contracts/agETH/AGETHTokenWrapper.sol`)

---

### Summary

`maxAmountToDepositBridgerAsset` computes available bridger deposit capacity using a live `balanceOf` call rather than internal accounting. Any holder of altAgETH can send as little as 1 wei directly to the wrapper contract, inflating `balanceOfAssetInWrapper` above `agETHSupply` and causing `maxAmountToDepositBridgerAsset` to return 0. Because `depositBridgerAssets` gates on this return value, the bridger is permanently unable to add collateral via that path, and no on-chain recovery function exists.

---

### Finding Description

`maxAmountToDepositBridgerAsset` reads the wrapper's altAgETH balance directly from the token contract: [1](#0-0) 

```solidity
uint256 agETHSupply = totalSupply();
uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

if (balanceOfAssetInWrapper > agETHSupply) return 0;

return agETHSupply - balanceOfAssetInWrapper;
```

`depositBridgerAssets` enforces this cap strictly: [2](#0-1) 

```solidity
function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
    if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
        revert CannotDeposit();
    }
```

Because `balanceOf` is a live read, any ERC-20 `transfer` directly to the wrapper address — without calling any wrapper function — inflates `balanceOfAssetInWrapper` without minting any agETH, making `balanceOfAssetInWrapper > agETHSupply` and forcing the return value to 0.

**Why no self-healing exists:**

- `_withdraw` burns agETH and transfers altAgETH out, reducing both `supply` and `balance` by the same delta — the surplus 1 wei remains.
- `deposit` mints new agETH 1:1 with deposited altAgETH — both sides increase equally, surplus persists.
- `mint` (MINTER_ROLE) can increase `supply` without touching `balance`, which is the only workaround, but it introduces uncollateralized agETH into circulation and requires privileged admin action.
- **No `recoverTokens` or equivalent function exists in the contract.** [3](#0-2) 

---

### Impact Explanation

The `depositBridgerAssets` function is the sole mechanism for the bridger to back agETH that was minted on L2 (via `mint`) without collateral. If it is blocked:

1. The wrapper remains undercollateralized.
2. Users holding agETH minted on L2 without backing cannot redeem for altAgETH if the wrapper balance is insufficient.
3. The bridger's collateralization duty cannot be fulfilled on-chain without privileged workarounds.

This constitutes **temporary freezing of funds** (Medium) — the agETH minted on L2 without backing is effectively frozen until admin intervention (minting extra uncollateralized agETH to shift the ratio, then depositing).

---

### Likelihood Explanation

- **Cost to attacker**: 1 wei of altAgETH (negligible).
- **Permissions required**: None — any altAgETH holder can call `altAgETH.transfer(wrapperAddress, 1)`.
- **Repeatability**: The attacker can re-donate after each admin recovery attempt, making sustained griefing trivially cheap.
- **Precondition**: The wrapper must be at or near full collateralization (`balanceOfAssetInWrapper >= agETHSupply`), which is the normal operating state after the bridger has done its job.

---

### Recommendation

Replace the live `balanceOf` read with an internal accounting variable that is only updated through controlled wrapper functions (`_deposit`, `_withdraw`, `depositBridgerAssets`). Direct ERC-20 transfers to the contract address will then have no effect on the computed cap.

Additionally, add a privileged `recoverExcessTokens` function that allows the admin to drain any surplus balance (i.e., `balanceOf(address(this)) - internalBalance`) to prevent donated tokens from permanently skewing the accounting.

---

### Proof of Concept

```solidity
// Fork test (local/private testnet)
function test_donationBlocksBridger() public {
    // Setup: agETHSupply == balanceOfAssetInWrapper (fully collateralized)
    // e.g., 100e18 agETH minted, 100e18 altAgETH in wrapper

    uint256 supplyBefore = wrapper.totalSupply();
    uint256 balBefore = altAgETH.balanceOf(address(wrapper));
    assertEq(supplyBefore, balBefore); // fully collateralized

    // Attacker donates 1 wei directly (no wrapper function called)
    vm.prank(attacker);
    altAgETH.transfer(address(wrapper), 1);

    // maxAmountToDepositBridgerAsset now returns 0
    assertEq(wrapper.maxAmountToDepositBridgerAsset(address(altAgETH)), 0);

    // Bridge mints 10e18 agETH on L2 (supply now > balance by 10e18 - 1)
    vm.prank(minter);
    wrapper.mint(user, 10e18);

    // Bridger tries to deposit 10e18 to collateralize — can only deposit 10e18 - 1
    // But attacker donates 10e18 wei to block completely:
    vm.prank(attacker);
    altAgETH.transfer(address(wrapper), 10e18);

    // depositBridgerAssets reverts for any positive amount
    vm.prank(bridger);
    vm.expectRevert(AGETHTokenWrapper.CannotDeposit.selector);
    wrapper.depositBridgerAssets(address(altAgETH), 1);
}
```

### Citations

**File:** contracts/agETH/AGETHTokenWrapper.sol (L94-100)
```text
        uint256 agETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > agETHSupply) return 0;

        return agETHSupply - balanceOfAssetInWrapper;
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L143-146)
```text
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L153-168)
```text
    /// Dont' allow to add other tokens at the moment. Only allow the altAgETH token as set in the initialize function

    /// @dev Remove a token from the allowed tokens list
    /// @param _asset The address of the token to remove
    function removeAllowedToken(address _asset) external onlyRole(DEFAULT_ADMIN_ROLE) {
        allowedTokens[_asset] = false;
        emit TokenRemoved(_asset);
    }

    /// @dev Mint agETH tokens on L2
    /// @param _to The address to mint the tokens to
    /// @param _amount The amount of tokens to mint
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
}
```
