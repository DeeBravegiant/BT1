Now I have a complete picture of the vulnerability. Let me write the finding.

---

### Title
Attacker Can DOS `depositBridgerAssets` by Donating Tokens, Temporarily Freezing User Withdrawals - (File: `contracts/L2/RsETHTokenWrapper.sol`, `contracts/agETH/AGETHTokenWrapper.sol`)

### Summary
Both `RsETHTokenWrapper` and `AGETHTokenWrapper` compute the maximum collateral the bridger may deposit by comparing the contract's **total** underlying token balance against the wrapper's total supply. An unprivileged attacker can donate (directly transfer) a small amount of the underlying token to either contract, inflating `balanceOfAssetInWrapper` and causing `depositBridgerAssets` to revert. While the bridger's collateral deposit is blocked, users whose wrsETH/agETH was minted via the bridge path (with no underlying tokens yet in the contract) cannot redeem their underlying tokens.

### Finding Description

`maxAmountToDepositBridgerAsset` in both wrappers reads the contract's raw token balance:

```solidity
// RsETHTokenWrapper.sol lines 103-109
uint256 wrsETHSupply = totalSupply();
uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

if (balanceOfAssetInWrapper > wrsETHSupply) return 0;
return wrsETHSupply - balanceOfAssetInWrapper;
```

`depositBridgerAssets` then gates on this value:

```solidity
// RsETHTokenWrapper.sol lines 163-165
if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
    revert CannotDeposit();
}
```

The intended flow for the bridge path is:

1. The bridge calls `mint()` (MINTER_ROLE) to issue wrsETH on L2 — `totalSupply` increases, but the contract holds **zero** underlying tokens.
2. The bridger later calls `depositBridgerAssets()` to deposit the backing altRsETH, making the contract solvent.

Between steps 1 and 2, the contract is undercollateralised: `totalSupply > balanceOfAssetInWrapper`. Any user who tries to call `withdraw()` during this window will receive a revert from `safeTransfer` because the contract has no tokens to send.

An attacker can exploit this window:

- After step 1, `maxAmountToDepositBridgerAsset` returns `totalSupply` (e.g. 100).
- Attacker donates 1 wei of altRsETH directly to the contract (`balanceOfAssetInWrapper` becomes 1).
- `maxAmountToDepositBridgerAsset` now returns 99.
- Bridger calls `depositBridgerAssets(asset, 100)` → `99 < 100` → **reverts with `CannotDeposit`**.
- The attacker can front-run every retry, continuously donating 1 wei to keep the bridger's exact requested amount above the computed cap.

The bridger must recalculate and retry with the reduced cap each time. During the entire griefing period, users holding bridge-minted wrsETH/agETH cannot withdraw their underlying tokens because the contract remains undercollateralised.

The identical pattern exists in `AGETHTokenWrapper`:

```solidity
// AGETHTokenWrapper.sol lines 94-100
uint256 agETHSupply = totalSupply();
uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

if (balanceOfAssetInWrapper > agETHSupply) return 0;
return agETHSupply - balanceOfAssetInWrapper;
```

```solidity
// AGETHTokenWrapper.sol lines 144-146
if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
    revert CannotDeposit();
}
```

### Impact Explanation

**Temporary freezing of funds.** Users who received wrsETH or agETH via the canonical bridge (minted by MINTER_ROLE before the backing collateral is deposited) cannot call `withdraw()` or `withdrawTo()` while the bridger's `depositBridgerAssets` call is being griefed. The `safeTransfer` inside `_withdraw` will revert because the contract holds insufficient underlying tokens. The freeze lasts as long as the attacker continues to front-run the bridger's deposit transactions.

### Likelihood Explanation

**High.** The attack requires only that the attacker hold a trivial amount of the underlying altRsETH or altAgETH token (1 wei is sufficient per front-run). No privileged access, no oracle manipulation, and no complex setup is needed. The vulnerable window (between `mint()` and `depositBridgerAssets()`) is a normal, recurring part of the bridge lifecycle, so the attack surface is always present.

### Recommendation

Track the balance increase rather than the total balance. Record the contract's balance before the bridger's deposit and verify the delta, or maintain an internal accounting variable (`totalBridgerDeposited`) that is incremented only by `depositBridgerAssets` and decremented by `_withdraw`, and use that instead of `balanceOf(address(this))` in `maxAmountToDepositBridgerAsset`.

For example, replace the raw balance read with a tracked variable:

```solidity
// Replace:
uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));
// With:
uint256 balanceOfAssetInWrapper = trackedBalance[_asset]; // updated only on deposit/withdraw
```

### Proof of Concept

1. Bridge (MINTER_ROLE) calls `mint(userA, 100e18)` on `RsETHTokenWrapper`. `totalSupply = 100e18`, contract altRsETH balance = 0.
2. `userA` calls `withdraw(altRsETH, 100e18)` → reverts (contract has 0 altRsETH). User is frozen.
3. Bridger prepares to call `depositBridgerAssets(altRsETH, 100e18)`.
4. Attacker calls `altRsETH.transfer(address(wrsETHWrapper), 1)` (donates 1 wei).
5. `maxAmountToDepositBridgerAsset` now returns `100e18 - 1`.
6. Bridger's `depositBridgerAssets(altRsETH, 100e18)` reverts: `CannotDeposit`.
7. Attacker repeats step 4 on every bridger retry, keeping the computed cap below the bridger's requested amount.
8. `userA` remains unable to withdraw for the duration of the griefing. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
