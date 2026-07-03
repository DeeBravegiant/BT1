### Title
ETH Deposit Limit Check Missing `amount` Allows Deposit Cap Bypass - (File: `contracts/LRTDepositPool.sol`)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit()` applies an asymmetric deposit-limit check: for ERC20 assets it correctly tests `totalAssetDeposits + amount > limit`, but for ETH it omits `amount` entirely, testing only `totalAssetDeposits > limit`. Any ETH depositor can therefore push the protocol past its configured ETH deposit cap in a single transaction.

### Finding Description

In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit()`, the ETH branch does not add the incoming `amount` to `totalAssetDeposits` before comparing against the configured limit:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for ERC20
}
``` [1](#0-0) 

The function is called from `_beforeDeposit`, which reverts with `MaximumDepositLimitReached` only when the function returns `true`:

```solidity
// contracts/LRTDepositPool.sol  lines 661-663
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [2](#0-1) 

`_beforeDeposit` is invoked by both `depositETH` and `depositAsset`: [3](#0-2) 

**Concrete example**

| State | Value |
|---|---|
| `depositLimitByAsset(ETH)` | 100 ETH |
| `totalAssetDeposits(ETH)` | 99 ETH |
| User calls `depositETH` with `msg.value` | 10 ETH |
| ETH check: `99 > 100` | `false` → deposit allowed |
| Post-deposit total | **109 ETH** (9 ETH over the cap) |

The ERC20 path would have correctly returned `true` (`99 + 10 > 100`) and reverted.

### Impact Explanation

The ETH deposit limit is a protocol-level safety cap. Bypassing it allows:

1. **Excess rsETH minting** — `depositETH` calls `_mintRsETH` after the check passes; more rsETH is minted than the protocol intends to back.
2. **Protocol insolvency risk** — if the cap was sized to match EigenLayer strategy capacity or liquidity constraints, exceeding it can leave the protocol unable to honour redemptions, constituting a path to insolvency.

Impact: **Medium — deposit limit bypass enabling excess rsETH minting and potential protocol insolvency.**

### Likelihood Explanation

The condition is triggered whenever `totalAssetDeposits ≤ depositLimitByAsset` but `totalAssetDeposits + depositAmount > depositLimitByAsset`. This is a normal operating state (pool near its cap). Any unprivileged ETH depositor can trigger it with a single `depositETH` call. No special setup is required.

### Recommendation

Add `amount` to the ETH branch, mirroring the ERC20 logic:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    // unified check for both ETH and ERC20
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept

1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 100 ether`.
2. Protocol accumulates `99 ether` of ETH deposits across all locations counted by `getTotalAssetDeposits`.
3. Alice calls `depositETH{value: 10 ether}(...)`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 10 ether)` evaluates `99 > 100` → `false`.
5. `_mintRsETH` executes; Alice receives rsETH; protocol ETH total becomes 109 ETH — 9 ETH above the configured cap.
6. Repeat with any depositor until the cap is arbitrarily exceeded.

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
