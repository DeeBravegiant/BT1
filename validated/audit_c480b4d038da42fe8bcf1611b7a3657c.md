### Title
ETH Deposit Limit Bypass Due to Missing Deposit Amount in Limit Check - (File: contracts/LRTDepositPool.sol)

### Summary

`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool.sol` uses an incorrect comparison for ETH deposits: it checks whether the **current** total already exceeds the limit, but never adds the incoming deposit amount to the comparison. For all LST assets the check correctly includes `+ amount`, but for ETH it is omitted. Any unprivileged depositor can therefore deposit an arbitrary amount of ETH past the configured cap in a single transaction.

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` is the sole guard that enforces the per-asset deposit cap before minting rsETH:

```solidity
// contracts/LRTDepositPool.sol  L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount NOT included
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for LSTs
}
``` [1](#0-0) 

For ETH the function returns `true` (i.e., "limit exceeded → revert") only when `totalAssetDeposits` **already** exceeds the limit before the new deposit is counted. The incoming `amount` is never added. For every LST the analogous line correctly uses `totalAssetDeposits + amount`.

The check is called unconditionally from `_beforeDeposit`, which is called by the public `depositETH` entry point:

```solidity
// L648-L663
function _beforeDeposit(...) private view returns (uint256 rsethAmountToMint) {
    ...
    if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
        revert MaximumDepositLimitReached();
    }
    ...
}
``` [2](#0-1) 

`depositETH` is the public, permissionless entry point: [3](#0-2) 

**Concrete example**

| Variable | Value |
|---|---|
| `depositLimitByAsset(ETH)` | 100 ETH |
| `totalAssetDeposits` (before tx) | 99 ETH |
| `amount` (user sends) | 10 000 ETH |

Check executed: `99 > 100` → `false` → no revert → 10 000 ETH accepted and rsETH minted.

After the deposit `getTotalAssetDeposits` returns 10 099 ETH — 100× the intended cap — while the same deposit of 10 000 ETH in any LST would have correctly reverted.

### Impact Explanation

The deposit limit is the protocol's primary TVL risk-management control. Bypassing it allows:

1. **Unbounded rsETH minting** beyond the intended cap, diluting yield for existing holders because the excess ETH cannot be immediately deployed to EigenLayer strategies.
2. **Node-delegator / EigenLayer strategy overflow**: `transferETHToNodeDelegator` and downstream EigenLayer calls are not designed to handle unbounded inflows; excess ETH sits idle in the pool, reducing the effective yield backing rsETH.
3. **Protocol risk controls nullified**: the cap exists to limit concentration risk; its bypass undermines the security model.

Impact: **Low–Medium** — contract fails to deliver promised returns (yield dilution, risk-cap bypass). No direct theft of existing user funds, but the protocol's stated deposit ceiling is completely unenforceable for ETH.

### Likelihood Explanation

- The entry path (`depositETH`) is public and requires no special role.
- The attacker only needs enough ETH to exceed the limit.
- No front-running, governance capture, or external dependency is required.
- The bug is deterministic and reproducible on every call where `totalAssetDeposits ≤ depositLimit`.

Likelihood: **High** — any depositor can trigger this at will.

### Recommendation

Add the incoming `amount` to the ETH branch, matching the LST branch:

```diff
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
-       return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
+       return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept

1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 100 ether`.
2. Protocol already has `totalAssetDeposits(ETH) = 99 ether`.
3. Attacker calls `depositETH{value: 10_000 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `99 > 100` → `false` → no revert.
5. `_mintRsETH` mints rsETH proportional to 10 000 ETH.
6. `getTotalAssetDeposits(ETH)` now returns 10 099 ETH — 100× the configured cap.
7. Repeat with any LST to confirm the LST path correctly reverts at step 4. [1](#0-0)

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

**File:** contracts/LRTDepositPool.sol (L648-663)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

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
