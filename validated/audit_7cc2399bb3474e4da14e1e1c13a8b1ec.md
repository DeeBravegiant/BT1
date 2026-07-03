### Title
Stale `rsETHPrice` Used Alongside Live Asset Price in `getExpectedAssetAmount` Causes Incorrect Withdrawal Amounts — (`contracts/LRTWithdrawalManager.sol`)

### Summary
`LRTWithdrawalManager::getExpectedAssetAmount` computes withdrawal amounts by combining `lrtOracle.rsETHPrice()` — a stored state variable updated only when a manager calls `updateRSETHPriceAsManager()` — with `lrtOracle.getAssetPrice(asset)`, which fetches a live Chainlink price at call time. When LST prices drop but `rsETHPrice` has not yet been refreshed, the ratio is inflated and users receive more underlying assets than their rsETH is actually worth, draining protocol funds.

### Finding Description

`LRTWithdrawalManager::getExpectedAssetAmount` (line 593) computes:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

`lrtOracle.rsETHPrice()` is a stored state variable in `LRTOracle` (line 28). It is only updated when `updateRSETHPriceAsManager()` is explicitly called by an LRT manager (line 94–96). Its value is derived from the total ETH value of all protocol assets **at the time of the last update**.

`lrtOracle.getAssetPrice(asset)` (line 156–158) always fetches the live price from the registered Chainlink feed at call time.

These two values are computed at different points in time and can diverge materially. If an LST (e.g., stETH) depegs after the last `rsETHPrice` update, `rsETHPrice` remains at the old (higher) value while `getAssetPrice(asset)` reflects the new (lower) value. The ratio `rsETHPrice / getAssetPrice(asset)` is then larger than it should be, causing `underlyingToReceive` to be overstated.

This stale value is consumed directly and without recalculation in `instantWithdrawal` (line 228):

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
...
unstakingVault.redeem(asset, assetAmountUnlocked);
```

Assets are immediately transferred to the caller at the inflated amount. There is no subsequent recalculation or price-check guard on the output of `getExpectedAssetAmount` in this path.

The same stale value is also used in `initiateWithdrawal` (line 168) to set `request.expectedAssetAmount`. Although `_calculatePayoutAmount` (line 833–834) takes the minimum of the stored amount and a recalculated amount at unlock time, if `rsETHPrice` is still stale when `unlockQueue` is called, both values are inflated and the minimum provides no protection.

### Impact Explanation

An attacker who observes that `rsETHPrice` is stale (higher than the current implied value) and that an LST price has dropped can call `instantWithdrawal` to receive more underlying assets than their rsETH is worth. The excess comes directly from other depositors' funds, constituting a theft of protocol assets.

**Example:** Protocol holds 100 stETH backing 100 rsETH. `rsETHPrice = 1.0 ETH`, `getAssetPrice(stETH) = 1.0 ETH`. stETH depegs to 0.9 ETH. `rsETHPrice` is not yet updated. Attacker calls `instantWithdrawal(stETH, 10e18)`. `getExpectedAssetAmount` returns `10e18 × 1.0e18 / 0.9e18 ≈ 11.11e18 stETH`. Attacker receives 11.11 stETH (worth ~10 ETH at current prices) for 10 rsETH that is now only worth ~9 ETH. The ~1 ETH surplus is extracted from remaining depositors.

**Impact classification:** High — theft of protocol funds belonging to other depositors.

### Likelihood Explanation

Medium. `rsETHPrice` has no on-chain automatic update trigger; it is updated only when an LRT manager explicitly calls `updateRSETHPriceAsManager()`. During LST depeg events or rapid market moves, there is always a non-zero window between the price change and the manager's update transaction. An attacker monitoring on-chain prices can exploit this window. The `instantWithdrawal` path requires `isInstantWithdrawalEnabled[asset]` to be set by the manager, but the `initiateWithdrawal` path is always open and exploitable if `rsETHPrice` remains stale through the unlock window.

### Recommendation

Replace the use of the stored `lrtOracle.rsETHPrice()` in `getExpectedAssetAmount` with a live computation of the rsETH/ETH rate derived from the same inputs used in `_updateRsETHPrice` (i.e., `totalETHInProtocol / rsETHSupply`). This ensures both the numerator and denominator of the withdrawal ratio are evaluated at the same point in time, eliminating the inconsistency. Alternatively, require that `rsETHPrice` be updated atomically within the same transaction before any withdrawal amount is computed.

### Proof of Concept

1. Protocol state: 100 stETH in protocol, 100 rsETH supply. `rsETHPrice = 1.0e18`, `getAssetPrice(stETH) = 1.0e18`.
2. stETH depegs: Chainlink feed for stETH updates to `0.9e18`. `rsETHPrice` is **not** updated by the manager yet.
3. Attacker holds 10 rsETH (current fair value: ~9 ETH at the depegged rate).
4. Attacker calls `instantWithdrawal(stETH, 10e18, "")`.
5. `getExpectedAssetAmount(stETH, 10e18)` computes: `10e18 × 1.0e18 / 0.9e18 = 11.111...e18`.
6. Protocol burns 10 rsETH from attacker and redeems 11.11 stETH from the unstaking vault.
7. Attacker receives 11.11 stETH worth ~10 ETH, having surrendered rsETH worth only ~9 ETH.
8. The ~1.11 stETH surplus (~1 ETH) is extracted from remaining depositors' backing.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L212-235)
```text
    function instantWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
        onlyInstantWithdrawalAllowed(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
        if (IERC20(lrtConfig.rsETH()).balanceOf(msg.sender) < rsETHUnstaked) revert NotEnoughRsETH();
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L26-29)
```text
    mapping(address asset => address priceOracle) public override assetPriceOracle;

    uint256 public override rsETHPrice;
    uint256 public pricePercentageLimit;
```

**File:** contracts/LRTOracle.sol (L91-96)
```text
    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L152-158)
```text
    /// @notice Provides Asset/ETH exchange rate
    /// @dev reads from priceFetcher interface which may fetch price from any supported oracle
    /// @param asset the asset for which exchange rate is required
    /// @return assetPrice exchange rate of asset
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```
