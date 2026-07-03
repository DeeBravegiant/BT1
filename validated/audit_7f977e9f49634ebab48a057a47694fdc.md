### Title
Upgradeable Stader Proxy Dependency in `EthXPriceOracle` Can Permanently Break rsETH Price Updates, Enabling Yield Theft via Stale Price - (File: contracts/oracles/EthXPriceOracle.sol)

---

### Summary

`EthXPriceOracle` hard-codes a call to `IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).getExchangeRate()` — a contract the variable name itself acknowledges is a proxy. If Stader upgrades the implementation behind that proxy in a breaking way, every call to `LRTOracle.updateRSETHPrice()` will revert permanently, freezing the stored `rsETHPrice` at a stale value. Because `LRTDepositPool.getRsETHAmountToMint()` divides by the stale (lower) `rsETHPrice`, subsequent depositors receive more rsETH than they are entitled to, diluting existing holders and constituting theft of unclaimed yield.

---

### Finding Description

`EthXPriceOracle.getAssetPrice()` unconditionally delegates to the Stader `ETHXStakePoolsManager` proxy:

```solidity
// contracts/oracles/EthXPriceOracle.sol  line 51
return IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).getExchangeRate();
``` [1](#0-0) 

The address stored in `ethXStakePoolsManagerProxyAddress` is explicitly a proxy (the variable name and the `initialize2()` call that reads `staderConfig()` from it confirm this). There is no version check, no fallback, and no freeze mechanism.

`LRTOracle._getTotalEthInProtocol()` iterates **every** supported asset and calls `getAssetPrice()` for each:

```solidity
// contracts/LRTOracle.sol  lines 336-343
for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
    address asset = supportedAssets[assetIdx];
    uint256 assetER = getAssetPrice(asset);          // reverts if EthX oracle reverts
    uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
    totalETHInProtocol += totalAssetAmt.mulWad(assetER);
    ...
}
``` [2](#0-1) 

`_getTotalEthInProtocol()` is called exclusively from `_updateRsETHPrice()`, which backs both the public `updateRSETHPrice()` and the manager-only `updateRSETHPriceAsManager()`: [3](#0-2) 

If the Stader proxy upgrade causes `getExchangeRate()` to revert (removed selector, changed ABI, or explicit revert), the entire price-update path is permanently bricked. The stored `rsETHPrice` variable is never written again.

`LRTDepositPool.getRsETHAmountToMint()` then uses this frozen value as the denominator for every subsequent deposit:

```solidity
// contracts/LRTDepositPool.sol  line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

Because real TVL continues to grow (staking rewards accrue) while `rsETHPrice` is frozen at the pre-upgrade value, the denominator is lower than the true exchange rate. Every new depositor therefore mints more rsETH than their proportional share warrants, diluting all existing holders.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders continuously earn staking rewards that are reflected in a rising `rsETHPrice`. Once the price update is bricked, that accrued yield is silently redistributed to new depositors who mint rsETH at the stale (cheaper) rate. The longer the oracle remains broken, the larger the dilution. There is no on-chain mechanism to recover without an admin manually replacing the oracle contract — a window that could span days or weeks.

---

### Likelihood Explanation

**Low-Medium.**

Stader's `ETHXStakePoolsManager` is a known upgradeable proxy on mainnet. Protocol upgrades are routine for actively developed LST protocols. A breaking change (renamed function, changed return semantics, or removal of `getExchangeRate()`) is plausible during a major Stader protocol revision. No governance vote or attacker action within the rsETH protocol is required — the trigger is entirely external but realistic.

---

### Recommendation

1. **Freeze EthX interactions on upgrade detection**: Implement an upgrade-detection hook (compare stored implementation address against the current proxy implementation slot before each call). If a change is detected, pause EthX-related operations and emit an alert, analogous to the [MakerDAO TUSD adapter pattern](https://github.com/makerdao/dss-deploy/blob/7394f6555daf5747686a1b29b2f46c6b2c64b061/src/join.sol#L322).

2. **Wrap the external call in a try/catch**: In `EthXPriceOracle.getAssetPrice()`, catch a revert from `getExchangeRate()` and revert with a descriptive error. In `LRTOracle._getTotalEthInProtocol()`, handle a per-asset oracle failure gracefully (e.g., skip the asset and emit an event) rather than reverting the entire price update.

3. **Decouple price-update failure from deposit availability**: Store per-asset prices independently so that a single broken oracle does not block the global `rsETHPrice` update for all other assets.

---

### Proof of Concept

**Step-by-step:**

1. Stader upgrades the `ETHXStakePoolsManager` proxy implementation, removing or renaming `getExchangeRate()`.
2. Any account (including an unprivileged user) calls `LRTOracle.updateRSETHPrice()`.
3. Execution reaches `_getTotalEthInProtocol()` → `getAssetPrice(ethxAddress)` → `EthXPriceOracle.getAssetPrice()` → `IETHXStakePoolsManager(...).getExchangeRate()` → **reverts** (no such selector).
4. The entire `updateRSETHPrice()` call reverts. `rsETHPrice` remains at its last written value (e.g., `1.05e18`).
5. Over the next weeks, staking rewards push the true exchange rate to `1.08e18`, but `rsETHPrice` stays at `1.05e18`.
6. A new depositor calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
7. `getRsETHAmountToMint` computes: `(100e18 * stETH_price) / 1.05e18` instead of the correct `/ 1.08e18`.
8. The depositor receives ~2.86% more rsETH than entitled, at the expense of all existing holders' accrued yield. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/oracles/EthXPriceOracle.sol (L19-52)
```text
contract EthXPriceOracle is IPriceFetcher, Initializable {
    /// @dev deprecated
    address public ethXStakePoolsManagerProxyAddress;
    address public ethxAddress;

    error InvalidAsset();

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /// @dev Initializes the contract
    /// @param ethXStakePoolsManagerProxyAddress_ ETHX address
    function initialize(address ethXStakePoolsManagerProxyAddress_) external initializer {
        UtilLib.checkNonZeroAddress(ethXStakePoolsManagerProxyAddress_);
        ethXStakePoolsManagerProxyAddress = ethXStakePoolsManagerProxyAddress_;
    }

    function initialize2() external reinitializer(2) {
        address staderConfigProxyAddress = IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).staderConfig();
        ethxAddress = IStaderConfig(staderConfigProxyAddress).getETHxToken();
    }

    /// @notice Fetches Asset/ETH exchange rate
    /// @param asset the asset for which exchange rate is required
    /// @return assetPrice exchange rate of asset
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != ethxAddress) {
            revert InvalidAsset();
        }

        return IETHXStakePoolsManager(ethXStakePoolsManagerProxyAddress).getExchangeRate();
    }
```

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-232)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

```

**File:** contracts/LRTOracle.sol (L331-349)
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
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
