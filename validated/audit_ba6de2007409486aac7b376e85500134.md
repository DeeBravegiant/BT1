### Title
Temporary Freezing of Bridged LST in L1Vault When Asset Is Removed from LRTConfig Before MANAGER Deposits — (`contracts/L1Vault.sol`)

---

### Summary

`L1Vault.depositAssetForL1Vault` delegates to `LRTDepositPool.depositAsset`, which enforces `onlySupportedERC20Token`. If an LST is removed from `LRTConfig.isSupportedAsset` after it has been bridged to `L1Vault` but before the MANAGER calls `depositAssetForL1Vault`, the call reverts with `AssetNotSupported` and the tokens are stuck — because `L1Vault` has no rescue or recovery function.

---

### Finding Description

**Call chain:**

`L1Vault.depositAssetForL1Vault` → `lrtDepositPool.depositAsset(token, ...)` → `onlySupportedERC20Token(asset)` modifier → `lrtConfig.isSupportedAsset(asset)` → reverts `AssetNotSupported` [1](#0-0) [2](#0-1) [3](#0-2) 

**Why `removeSupportedAsset` does not protect against this:**

`LRTConfig.removeSupportedAsset` has a guard that prevents removal when `getTotalAssetDeposits(asset) > maxNegligibleAmount`. However, `getTotalAssetDeposits` only sums balances across `LRTDepositPool`, NDCs, EigenLayer strategies, the converter, and the unstaking vault — it does **not** include the balance sitting in `L1Vault`. [4](#0-3) [5](#0-4) 

So the admin can successfully call `removeSupportedAsset(stETH)` even while stETH is sitting in `L1Vault`, because the guard passes (L1Vault balance is invisible to it).

**No recovery path exists in `L1Vault`:**

`L1Vault` has no `rescueTokens`, `emergencyWithdraw`, or equivalent function. The only functions that move ERC-20 tokens out are `depositAssetForL1Vault` (which reverts for unsupported assets) and `unwrapWstETH`/`unwrapWETH` (hardcoded to specific tokens only). [6](#0-5) 

---

### Impact Explanation

Bridged LST tokens are frozen in `L1Vault` until the asset is re-added to `LRTConfig` via `addNewSupportedAsset`, which requires `TIME_LOCK_ROLE` and introduces governance delay. During this window, the tokens cannot be deposited into the pool or recovered by any other means. This matches **Medium — Temporary freezing of funds**.

---

### Likelihood Explanation

The scenario does not require malicious admin intent. It arises from a legitimate protocol operation (deprecating an LST) where the admin relies on the `removeSupportedAsset` guard to confirm no deposits exist, but the guard silently ignores `L1Vault` balances. The race condition between bridge finality and asset deprecation is realistic in a live protocol managing multiple LSTs.

---

### Recommendation

1. **Add a `rescueTokens` function** to `L1Vault` (restricted to `TIMELOCK_ROLE` or `DEFAULT_ADMIN_ROLE`) so that tokens can be recovered if `depositAssetForL1Vault` is blocked.
2. **Extend `removeSupportedAsset`** to query known `L1Vault` balances (or require them to be zero) before allowing removal, similar to how it checks `getTotalAssetDeposits`.

---

### Proof of Concept

```solidity
// Fork test (local fork, no mainnet)
function test_temporaryFreeze() public {
    // 1. Simulate bridge: send stETH to L1Vault
    deal(address(stETH), address(l1Vault), 10 ether);

    // 2. Admin removes stETH from LRTConfig
    // (passes because L1Vault balance is not in getTotalAssetDeposits)
    vm.prank(admin);
    lrtConfig.removeSupportedAsset(address(stETH), stETHIndex);

    // 3. MANAGER tries to deposit — reverts
    vm.prank(manager);
    vm.expectRevert(ILRTConfig.AssetNotSupported.selector);
    l1Vault.depositAssetForL1Vault(address(stETH));

    // 4. stETH balance unchanged — tokens are frozen
    assertEq(IERC20(stETH).balanceOf(address(l1Vault)), 10 ether);
}
```

### Citations

**File:** contracts/L1Vault.sol (L166-182)
```text
    function depositAssetForL1Vault(address token) external nonReentrant onlyRole(MANAGER_ROLE) {
        UtilLib.checkNonZeroAddress(token);

        uint256 tokenBalance = IERC20(token).balanceOf(address(this));
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(token, tokenBalance);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        // Approve the LRT deposit pool to transfer the token
        IERC20(token).safeIncreaseAllowance(address(lrtDepositPool), tokenBalance);

        lrtDepositPool.depositAsset(token, tokenBalance, rsETHAmountToMint, "");

        emit AssetDepositForL1Vault(token, tokenBalance, rsETHAmountToMint);
    }
```

**File:** contracts/L1Vault.sol (L184-210)
```text
    /// @notice Unwrap wstETH to stETH to be able to mint rsETH
    function unwrapWstETH() external nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 wstETHBalance = IERC20(wstETH).balanceOf(address(this));

        if (wstETHBalance == 0) {
            revert NoWstETHBalance();
        }

        // Unwrap wstETH to stETH
        uint256 stETHAmount = IWstETH(wstETH).unwrap(wstETHBalance);

        emit WstETHUnwrapped(stETHAmount);
    }

    /// @notice Unwrap WETH to ETH to be able to mint rsETH
    function unwrapWETH() external nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 wethBalance = IERC20(WETH).balanceOf(address(this));

        if (wethBalance == 0) {
            revert NoWETHBalance();
        }

        // Unwrap WETH to ETH
        IWETH(WETH).withdraw(wethBalance);

        emit WETHUnwrapped(wethBalance);
    }
```

**File:** contracts/LRTDepositPool.sol (L99-109)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
```

**File:** contracts/LRTDepositPool.sol (L385-397)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
    }
```

**File:** contracts/utils/LRTConfigRoleChecker.sol (L72-80)
```text
    modifier onlySupportedERC20Token(address asset) {
        if (!lrtConfig.isSupportedAsset(asset)) {
            revert ILRTConfig.AssetNotSupported();
        }
        if (asset == LRTConstants.ETH_TOKEN) {
            revert ILRTConfig.ETHNotSupported();
        }
        _;
    }
```

**File:** contracts/LRTConfig.sol (L80-84)
```text
        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }
```
