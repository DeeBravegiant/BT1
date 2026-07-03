### Title
Attacker Can Permanently DoS `depositBridgerAssets` by Directly Transferring Tokens to `AGETHTokenWrapper` - (File: contracts/agETH/AGETHTokenWrapper.sol)

---

### Summary

`AGETHTokenWrapper.depositBridgerAssets` relies on `maxAmountToDepositBridgerAsset`, which computes available capacity as `agETHSupply - balanceOf(address(this))`. Because any unprivileged actor can directly transfer even 1 wei of the allowed altAgETH token to the wrapper contract, `balanceOf(address(this))` can be made to exceed `agETHSupply`, causing `maxAmountToDepositBridgerAsset` to return 0 permanently and making every call to `depositBridgerAssets` revert with `CannotDeposit`. There is no recovery function in the contract.

---

### Finding Description

`maxAmountToDepositBridgerAsset` computes the bridger's deposit capacity:

```solidity
// contracts/agETH/AGETHTokenWrapper.sol lines 90-101
function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
    if (!allowedTokens[_asset]) return 0;

    uint256 agETHSupply = totalSupply();
    uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

    if (balanceOfAssetInWrapper > agETHSupply) return 0;   // <-- permanently 0 after attack

    return agETHSupply - balanceOfAssetInWrapper;
}
```

`depositBridgerAssets` gates on this value:

```solidity
// contracts/agETH/AGETHTokenWrapper.sol lines 143-151
function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
    if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
        revert CannotDeposit();
    }
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    emit BridgerDeposited(_asset, _amount);
}
```

The normal deposit path (`_deposit`) keeps the invariant `balanceOfAssetInWrapper == agETHSupply` because every `safeTransferFrom` is paired with a `_mint`. The bridger path is intended to be used only when `agETHSupply > balanceOfAssetInWrapper` (i.e., agETH was minted on L2 via the bridge without a corresponding deposit). However, nothing prevents an external actor from calling `ERC20.transfer(address(agETHWrapper), 1)` directly, which increases `balanceOfAssetInWrapper` without touching `agETHSupply`. Once `balanceOfAssetInWrapper > agETHSupply`, the guard returns 0 for all amounts, and `depositBridgerAssets` is permanently bricked. The contract has no `emergencyWithdraw`, `recoverToken`, or any other mechanism to drain the excess balance. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

`depositBridgerAssets` is the collateralization path used when agETH is bridged from L1 to L2 (minted by `MINTER_ROLE` without a corresponding underlying deposit). If this function is permanently DoS'd, the bridger can never back already-minted agETH with the underlying altAgETH token, leaving the wrapper undercollateralized. Users who hold agETH minted via the bridge path will be unable to redeem it for the underlying asset through `_withdraw` (which calls `safeTransfer` from the contract's balance), because the collateral was never deposited. This constitutes a **permanent freezing of funds** for those agETH holders. [3](#0-2) 

---

### Likelihood Explanation

The attack requires no special permissions and costs only the gas for a single ERC20 `transfer` of 1 wei of the allowed altAgETH token to the wrapper address. It is trivially executable by any token holder at any time, including front-running the first legitimate `depositBridgerAssets` call. The cost is negligible and the effect is permanent with no on-chain recovery path. [4](#0-3) 

---

### Recommendation

Replace the absolute `balanceOf(address(this))` accounting with an internal tracked variable that is incremented only through the controlled deposit paths (`_deposit` and `depositBridgerAssets`) and decremented through `_withdraw`. This mirrors the fix suggested in the reference report (track deposited amounts rather than relying on raw contract balance). Alternatively, add an admin-accessible token recovery function so excess balance can be swept out to restore the invariant.

---

### Proof of Concept

1. agETH is bridged from L1 to L2; `MINTER_ROLE` calls `mint(user, 100e18)` → `agETHSupply = 100e18`, `balanceOfAssetInWrapper = 0`.
2. `maxAmountToDepositBridgerAsset(altAgETH)` returns `100e18 - 0 = 100e18`. Bridger intends to call `depositBridgerAssets(altAgETH, 100e18)`.
3. Attacker front-runs: calls `altAgETH.transfer(address(agETHWrapper), 1)` directly.
4. Now `balanceOfAssetInWrapper = 1`, `agETHSupply = 100e18` — still fine so far.
5. Attacker calls `altAgETH.transfer(address(agETHWrapper), 100e18)` (total sent = `100e18 + 1`).
6. Now `balanceOfAssetInWrapper = 100e18 + 1 > agETHSupply = 100e18`.
7. `maxAmountToDepositBridgerAsset` returns `0`.
8. Every subsequent call to `depositBridgerAssets(altAgETH, any_nonzero_amount)` reverts with `CannotDeposit`.
9. The 100e18 agETH minted on L2 is permanently unbacked; holders cannot redeem the underlying asset. [5](#0-4) [6](#0-5)

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

**File:** contracts/agETH/AGETHTokenWrapper.sol (L143-151)
```text
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, _amount);
    }
```
