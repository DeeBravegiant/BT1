### Title
Rogue EigenLayer Strategy Can Become Unremovable and Freeze All Deposits and Oracle Updates - (File: contracts/LRTConfig.sol)

---

### Summary

`LRTConfig.updateAssetStrategy()` calls `IStrategy(assetStrategy[asset]).userUnderlyingView(ndcs[i])` on the **currently registered** strategy without a try/catch. If that strategy reverts on `userUnderlyingView()` (due to a bug or upgrade), the admin can never replace it. Simultaneously, `NodeDelegatorHelper.getAssetBalance()` calls `IStrategy(strategy).sharesToUnderlyingView(withdrawableShare)` without a try/catch, which propagates into every deposit, oracle update, and NDC-removal path. A strategy that reverts on either of these view calls permanently freezes the protocol.

---

### Finding Description

**Root cause 1 — unremovable strategy (`LRTConfig.sol` lines 151–166):**

```solidity
if (assetStrategy[asset] != address(0)) {
    address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);
    address[] memory ndcs = ILRTDepositPool(depositPool).getNodeDelegatorQueue();
    uint256 length = ndcs.length;
    for (uint256 i = 0; i < length;) {
        uint256 ndcBalance = IStrategy(assetStrategy[asset]).userUnderlyingView(ndcs[i]); // ← bare call, no try/catch
        if (ndcBalance > 0) {
            revert CannotUpdateStrategyAsItHasFundsNDCFunds(ndcs[i], ndcBalance);
        }
        unchecked { ++i; }
    }
}
```

If the registered strategy reverts on `userUnderlyingView()`, `updateAssetStrategy()` always reverts, making the broken strategy permanently irremovable.

**Root cause 2 — frozen deposit/oracle path (`NodeDelegatorHelper.sol` lines 31–39):**

```solidity
function getAssetBalance(ILRTConfig lrtConfig, address asset) internal view returns (uint256) {
    address strategy = lrtConfig.assetStrategy(asset);
    if (strategy == address(0)) { return 0; }
    uint256 withdrawableShare = getWithdrawableShare(lrtConfig, IStrategy(strategy));
    return IStrategy(strategy).sharesToUnderlyingView(withdrawableShare); // ← bare call, no try/catch
}
```

`getAssetBalance()` is called without any error handling from:

- `LRTDepositPool.getAssetDistributionData()` → `getTotalAssetDeposits()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `depositAsset()` / `depositETH()` — **all user deposits revert**
- `LRTOracle._getTotalEthInProtocol()` → `updateRSETHPrice()` — **oracle updates revert**
- `LRTDepositPool._checkResidueLSTBalance()` → `_removeNodeDelegatorContractFromQueue()` — **NDC removal reverts**

---

### Impact Explanation

A strategy that reverts on `sharesToUnderlyingView()` causes every call to `getTotalAssetDeposits()` to revert. Because `depositAsset()` and `depositETH()` both call `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()`, **all user deposits are permanently frozen**. Because `updateRSETHPrice()` calls `_getTotalEthInProtocol()` → `getTotalAssetDeposits()`, **the rsETH price oracle can no longer be updated**, which in turn blocks any deposit that checks `rsETHPrice`. Because `updateAssetStrategy()` itself calls `userUnderlyingView()` on the old strategy without a try/catch, **the broken strategy cannot be replaced**, leaving the protocol in a permanently frozen state. This matches the Critical impact tier: permanent freezing of funds.

---

### Likelihood Explanation

EigenLayer strategies are upgradeable proxy contracts. A governance-approved upgrade that introduces a revert in `sharesToUnderlyingView()` or `userUnderlyingView()` — whether through a bug, a storage-layout collision, or an intentional change in interface — is a realistic scenario. The LRT-rsETH protocol has no defensive coding (try/catch, force-update flag) to handle this case. The trigger does not require any privileged LRT-rsETH role to act maliciously; it only requires the external strategy contract to revert on a view call.

---

### Recommendation

1. **Add a `force` flag to `updateAssetStrategy()`** (analogous to the H-1 recommendation for `removePlugin()`):

```solidity
function updateAssetStrategy(address asset, address strategy, bool force) external onlyRole(DEFAULT_ADMIN_ROLE) onlySupportedAsset(asset) {
    ...
    if (!force && assetStrategy[asset] != address(0)) {
        for (uint256 i = 0; i < length;) {
            uint256 ndcBalance = IStrategy(assetStrategy[asset]).userUnderlyingView(ndcs[i]);
            if (ndcBalance > 0) revert CannotUpdateStrategyAsItHasFundsNDCFunds(ndcs[i], ndcBalance);
            unchecked { ++i; }
        }
    }
    ...
}
```

2. **Wrap `sharesToUnderlyingView` in `NodeDelegatorHelper.getAssetBalance()` with a try/catch**, returning 0 or a sentinel value on revert, so that a broken strategy degrades gracefully rather than freezing the entire deposit and oracle path.

---

### Proof of Concept

1. Admin registers EigenLayer strategy S for asset stETH via `updateAssetStrategy`.
2. EigenLayer upgrades strategy S; the new implementation reverts on both `userUnderlyingView()` and `sharesToUnderlyingView()`.
3. Admin calls `updateAssetStrategy(stETH, newStrategy)` → loops over NDCs → calls `S.userUnderlyingView(ndc)` → **reverts**. Strategy S is now permanently locked in.
4. Any user calls `depositAsset(stETH, ...)` → `_checkIfDepositAmountExceedesCurrentLimit` → `getTotalAssetDeposits` → `getAssetDistributionData` → `INodeDelegator(ndc).getAssetBalance(stETH)` → `NodeDelegatorHelper.getAssetBalance` → `S.sharesToUnderlyingView(...)` → **reverts**. All stETH deposits are frozen.
5. Anyone calls `updateRSETHPrice()` → `_getTotalEthInProtocol()` → `getTotalAssetDeposits(stETH)` → same revert chain → **oracle frozen**.
6. No recovery path exists: the strategy cannot be replaced, deposits cannot proceed, and the oracle cannot update. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTConfig.sol (L151-166)
```text
        if (assetStrategy[asset] != address(0)) {
            // get ndcs
            address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);
            address[] memory ndcs = ILRTDepositPool(depositPool).getNodeDelegatorQueue();

            uint256 length = ndcs.length;
            for (uint256 i = 0; i < length;) {
                uint256 ndcBalance = IStrategy(assetStrategy[asset]).userUnderlyingView(ndcs[i]);
                if (ndcBalance > 0) {
                    revert CannotUpdateStrategyAsItHasFundsNDCFunds(ndcs[i], ndcBalance);
                }

                unchecked {
                    ++i;
                }
            }
```

**File:** contracts/NodeDelegatorHelper.sol (L31-39)
```text
    function getAssetBalance(ILRTConfig lrtConfig, address asset) internal view returns (uint256) {
        address strategy = lrtConfig.assetStrategy(asset);
        if (strategy == address(0)) {
            return 0;
        }
        uint256 withdrawableShare = getWithdrawableShare(lrtConfig, IStrategy(strategy));

        return IStrategy(strategy).sharesToUnderlyingView(withdrawableShare);
    }
```

**File:** contracts/LRTDepositPool.sol (L447-451)
```text
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);
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

**File:** contracts/LRTOracle.sol (L331-348)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
```
