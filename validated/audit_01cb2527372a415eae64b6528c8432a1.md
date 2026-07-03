### Title
Protocol deposit freeze when `rsETHPrice` is set to zero — (`contracts/LRTOracle.sol` / `contracts/LRTDepositPool.sol`)

---

### Summary

When `totalETHInProtocol` drops to zero while `rsethSupply` is non-zero, `LRTOracle._updateRsETHPrice()` sets `rsETHPrice = 0`. Because `pricePercentageLimit` defaults to `0` (never set in `initialize()`), the downside-protection branch that would pause the protocol and abort the price update is skipped. Once `rsETHPrice == 0`, every call to `LRTDepositPool.getRsETHAmountToMint()` reverts with a division-by-zero, permanently freezing all new deposits until the state is manually corrected.

---

### Finding Description

**Step 1 — `rsETHPrice` is set to zero.**

`LRTOracle._updateRsETHPrice()` computes the new price as:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [1](#0-0) 

If `totalETHInProtocol == 0` and `rsethSupply > 0`, `newRsETHPrice` evaluates to `0`.

The downside-protection guard that follows is:

```solidity
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    ...
    return;   // rsETHPrice is NOT updated
}
``` [2](#0-1) 

`pricePercentageLimit` is **never initialised** in `initialize()`: [3](#0-2) 

Its default value is therefore `0`, making `pricePercentageLimit > 0` false. The guard is skipped, execution falls through, and the final assignment executes:

```solidity
rsETHPrice = newRsETHPrice;   // = 0
``` [4](#0-3) 

**Step 2 — All deposits revert.**

`LRTDepositPool.getRsETHAmountToMint()` divides by `rsETHPrice` with no zero-guard:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [5](#0-4) 

With `rsETHPrice == 0` this is a Solidity 0.8 division-by-zero panic revert. Every call path that reaches this line — `depositETH()` and `depositAsset()` — is blocked: [6](#0-5) 

**Step 3 — `updateRSETHPrice()` is publicly callable.**

Any unprivileged address can call `updateRSETHPrice()` at any time: [7](#0-6) 

An attacker can therefore trigger the price-zeroing update the moment `totalETHInProtocol` reaches zero, locking the protocol before the team can react.

**How `totalETHInProtocol` reaches zero while `rsethSupply > 0`:**

`_getTotalEthInProtocol()` sums `totalAssetAmt.mulWad(assetER)` across all supported assets: [8](#0-7) 

`getTotalAssetDeposits()` aggregates balances from the deposit pool, NDCs, EigenLayer strategies (`getAssetBalance` / `getEffectivePodShares`), the unstaking vault, and the converter: [9](#0-8) 

EigenLayer slashing (now live post-ELIP-002) can reduce `sharesToUnderlyingView` and `getWithdrawableShare` to zero for all staked assets. If all liquid balances are simultaneously zero (e.g., fully deployed to EigenLayer and fully slashed), `totalETHInProtocol == 0` while outstanding rsETH holders have not yet burned their shares — the exact analogue of the Carapace `totalSTokenUnderlying == 0 / totalSupply > 0` state.

---

### Impact Explanation

**Medium — Temporary freezing of funds (new deposits).**

Once `rsETHPrice == 0`, `depositETH()` and `depositAsset()` revert for every caller. The protocol cannot accept new capital. Existing rsETH holders can still queue withdrawals (the withdrawal path uses `rsETHPrice` multiplicatively, not as a divisor), but the deposit side is completely frozen until an admin either (a) sets `pricePercentageLimit` to a non-zero value and calls `updateRSETHPriceAsManager()` with a corrected TVL, or (b) directly injects assets to restore `totalETHInProtocol > 0` and re-runs the price update.

---

### Likelihood Explanation

**Low-Medium.**

Two conditions must coincide:

1. `pricePercentageLimit == 0` — this is the **default state** because `initialize()` never sets it. Any deployment where the admin has not explicitly called `setPricePercentageLimit()` is vulnerable.
2. `totalETHInProtocol` drops to zero while `rsethSupply > 0` — requires a severe slashing event or a complete drain of all liquid and staked balances. With EigenLayer slashing now enabled on mainnet, this is a realistic (if low-probability) tail risk.

Because `updateRSETHPrice()` is permissionless, an attacker can race to call it the moment the condition is met, before the team can set `pricePercentageLimit` or pause the oracle.

---

### Recommendation

1. **Guard against zero price**: In `_updateRsETHPrice()`, add an explicit check before writing `rsETHPrice`:
   ```solidity
   if (newRsETHPrice == 0) revert ZeroRsETHPrice();
   ```
2. **Initialise `pricePercentageLimit`**: Set a sensible non-zero default (e.g., `1e16` = 1 %) in `initialize()` so the downside-protection branch is always active.
3. **Guard the divisor in `getRsETHAmountToMint`**: Add `if (lrtOracle.rsETHPrice() == 0) revert ZeroRsETHPrice();` before the division.

---

### Proof of Concept

```
State:
  rsethSupply  = 1_000 ether   (users hold rsETH)
  totalETHInProtocol = 0       (all assets slashed / drained)
  pricePercentageLimit = 0     (never set by admin — default)
  rsETHPrice (stored) = 1 ether (last valid price)

1. Anyone calls LRTOracle.updateRSETHPrice().
2. rsethSupply > 0  → early-return branch skipped.
3. newRsETHPrice = (0 - 0).divWad(1_000e18) = 0.
4. isPriceDecreaseOffLimit = (0 > 0) && ... = false  → no pause, no return.
5. rsETHPrice = 0  ← stored.

6. User calls LRTDepositPool.depositETH{value: 1 ether}(0, "").
7. _beforeDeposit → getRsETHAmountToMint:
     rsethAmountToMint = (1e18 * assetPrice) / rsETHPrice
                       = (1e18 * assetPrice) / 0
                       → Panic: division by zero  ← REVERT

All deposits are now frozen.
```

### Citations

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
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

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-669)
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

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
