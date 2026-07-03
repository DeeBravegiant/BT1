### Title
`getRsETHAmountToMint` Does Not Validate Asset Support Status, Returning Misleading Exchange Data for Unsupported Assets - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint` is a public view function that computes how much rsETH a caller would receive for a given asset amount. It does not check whether the asset is actually supported by the protocol. After an asset is removed from the supported list via `LRTConfig.removeSupportedAsset`, its oracle entry in `LRTOracle` is not cleared. Any caller can then invoke `getRsETHAmountToMint` with the removed asset and receive a non-zero, seemingly valid rsETH amount — even though any actual deposit attempt for that asset would revert.

---

### Finding Description

`getRsETHAmountToMint` in `LRTDepositPool.sol` computes the rsETH mint amount purely by querying the oracle:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

It contains no `onlySupportedAsset` or `isSupportedAsset` guard. The oracle check (`onlySupportedOracle`) only verifies that `assetPriceOracle[asset] != address(0)`. [2](#0-1) 

`LRTConfig.removeSupportedAsset` deletes `isSupportedAsset[asset]` and `assetStrategy[asset]`, but does **not** clear `LRTOracle.assetPriceOracle[asset]`: [3](#0-2) 

After removal, `lrtOracle.assetPriceOracle[removedAsset]` remains non-zero. Therefore `getRsETHAmountToMint(removedAsset, amount)` succeeds and returns a non-zero value, falsely implying the asset is depositable.

The actual deposit entry points (`depositETH`, `depositAsset`) are protected by `onlySupportedAsset` / `onlySupportedERC20Token` modifiers and would revert: [4](#0-3) 

So the view function and the state-changing functions are inconsistent: the view says "yes, you can deposit this asset for X rsETH," but the deposit reverts.

Additionally, `LRTOracle.updatePriceOracleFor` allows an admin to set an oracle for any address, including one that is not in the supported asset list, because the non-zero check is only enforced when the asset is already supported: [5](#0-4) 

---

### Impact Explanation

Any user or off-chain integrator calling `getRsETHAmountToMint` with a removed (or never-supported) asset receives a non-zero, plausible-looking rsETH amount. This misleads them into believing a deposit is viable, when in fact the deposit transaction will revert. No funds are lost, but the contract fails to deliver the promised return implied by the view function.

**Impact**: Low — Contract fails to deliver promised returns, but doesn't lose value.

---

### Likelihood Explanation

The condition is triggered whenever:
1. An asset is removed from the supported list via `LRTConfig.removeSupportedAsset` (an admin action that is part of normal protocol lifecycle), **and**
2. Its oracle entry in `LRTOracle` is not manually cleared afterward.

This is a realistic operational scenario. No attacker action is required; any user can then call the public view function with the removed asset address.

**Likelihood**: Low — Requires a prior admin asset-removal event, but no attacker-controlled precondition.

---

### Recommendation

Add an `isSupportedAsset` check inside `getRsETHAmountToMint`, consistent with how the deposit functions are guarded:

```solidity
function getRsETHAmountToMint(address asset, uint256 amount)
    public
    view
    override
    onlySupportedAsset(asset)   // <-- add this
    returns (uint256 rsethAmountToMint)
{
    ...
}
```

Alternatively, ensure `LRTConfig.removeSupportedAsset` also clears the corresponding oracle entry in `LRTOracle` as part of the removal flow.

---

### Proof of Concept

1. Admin adds `tokenX` as a supported asset and sets its oracle in `LRTOracle`.
2. Admin later calls `LRTConfig.removeSupportedAsset(tokenX, idx)`. `isSupportedAsset[tokenX]` is deleted; `LRTOracle.assetPriceOracle[tokenX]` is **not** cleared.
3. Any user calls `LRTDepositPool.getRsETHAmountToMint(tokenX, 1 ether)`. The call succeeds and returns a non-zero rsETH amount (e.g., `0.95 ether`), because `lrtOracle.getAssetPrice(tokenX)` still resolves via the stale oracle entry.
4. The user attempts `depositAsset(tokenX, 1 ether, ...)`. The call reverts with `AssetNotSupported` due to the `onlySupportedERC20Token` modifier.
5. The view function and the deposit function are inconsistent: the view promised a valid exchange, the deposit reverted. [1](#0-0) [6](#0-5) [5](#0-4)

### Citations

**File:** contracts/LRTDepositPool.sol (L99-118)
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
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L40-45)
```text
    modifier onlySupportedOracle(address asset) {
        if (assetPriceOracle[asset] == address(0)) {
            revert AssetOracleNotSupported();
        }
        _;
    }
```

**File:** contracts/LRTOracle.sol (L113-119)
```text
    function updatePriceOracleFor(address asset, address priceOracle) public onlyLRTAdmin {
        if (lrtConfig.isSupportedAsset(asset)) {
            UtilLib.checkNonZeroAddress(priceOracle);
        }
        assetPriceOracle[asset] = priceOracle;
        emit AssetPriceOracleUpdate(asset, priceOracle);
    }
```

**File:** contracts/LRTConfig.sol (L64-93)
```text
    /// @dev Removes a supported asset
    /// @param asset The asset address
    function removeSupportedAsset(
        address asset,
        uint256 tokenIndex
    )
        external
        onlySupportedAsset(asset)
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(asset);

        if (supportedAssetList[tokenIndex] != asset) {
            revert TokenNotFoundError();
        }

        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }

        delete isSupportedAsset[asset];
        delete assetStrategy[asset];
        depositLimitByAsset[asset] = 0;

        supportedAssetList[tokenIndex] = supportedAssetList[supportedAssetList.length - 1];
        supportedAssetList.pop();

        emit RemovedSupportedAsset(asset);
```
