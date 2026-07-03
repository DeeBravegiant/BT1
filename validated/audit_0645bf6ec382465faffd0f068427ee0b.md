### Title
Protocol Fee Charged on Committed Withdrawal Assets Inflates Fee Base, Stealing Yield from rsETH Holders — (File: contracts/LRTOracle.sol)

---

### Summary

In `LRTOracle._updateRsETHPrice()`, the protocol performance fee is computed on the full `totalETHInProtocol`, which includes `assetLyingUnstakingVault` — assets already committed to withdrawers at a fixed price and sitting idle in `LRTUnstakingVault`. When those committed assets appreciate (e.g., stETH rebases while held in the vault), the protocol charges a fee on that appreciation even though the withdrawers locked in their `expectedAssetAmount` at `initiateWithdrawal` time. The appreciation rightfully belongs to remaining rsETH holders but is instead partially captured by the protocol treasury via fee minting.

---

### Finding Description

In `_updateRsETHPrice()`: [1](#0-0) 

```solidity
if (!protocolPaused && totalETHInProtocol > previousTVL) {
    uint256 rewardAmount = totalETHInProtocol - previousTVL;
    protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
}
```

`totalETHInProtocol` is assembled by `_getTotalEthInProtocol()`, which calls `getTotalAssetDeposits(asset)` for every supported asset: [2](#0-1) 

`getTotalAssetDeposits` sums six buckets, including `assetLyingUnstakingVault`: [3](#0-2) 

For LSTs, `assetLyingUnstakingVault` is the raw ERC-20 balance of the `LRTUnstakingVault`: [4](#0-3) 

For ETH, it is the vault's native balance: [5](#0-4) 

The `LRTUnstakingVault` holds assets that operators have moved there to service pending withdrawal requests. Those assets are committed to withdrawers via `assetsCommitted[asset]` in `LRTWithdrawalManager`: [6](#0-5) 

Withdrawers locked in their `expectedAssetAmount` at `initiateWithdrawal` time. They receive exactly that amount when `completeWithdrawal` is called, regardless of any subsequent appreciation: [7](#0-6) 

`previousTVL = rsethSupply * rsETHPrice` includes the rsETH held in the withdrawal manager (transferred there by `initiateWithdrawal` but not yet burned): [8](#0-7) 

So both sides of `rewardAmount = totalETHInProtocol - previousTVL` include the committed assets. When those assets appreciate between price updates (stETH rebase, ETH staking rewards flowing in), the delta is included in `rewardAmount` and the protocol charges `protocolFeeInBPS` on it — even though the withdrawers' payouts are fixed and the appreciation should accrue entirely to remaining rsETH holders.

---

### Impact Explanation

The protocol fee is charged on appreciation of assets that are committed to withdrawers at a fixed price. That appreciation is yield that rightfully belongs to remaining rsETH holders. Instead, a portion is diverted to the protocol treasury via rsETH fee minting, diluting all rsETH holders (including the withdrawal manager's pending rsETH). Withdrawers are unaffected (they receive their fixed `expectedAssetAmount`), but remaining rsETH holders receive less yield than they are entitled to.

**Impact: High — Theft of unclaimed yield from rsETH holders.**

---

### Likelihood Explanation

This occurs on every `updateRSETHPrice()` call when:
1. Assets are present in `LRTUnstakingVault` (routine during any withdrawal processing period), and
2. The TVL has increased since the last update (routine during stETH rebase or ETH staking reward accrual).

`updateRSETHPrice()` is a public, permissionless function: [9](#0-8) 

This is a continuous, ongoing condition during normal protocol operation whenever withdrawals are in flight.

**Likelihood: Medium** — requires assets in the unstaking vault (common) and TVL growth (routine).

---

### Recommendation

Subtract the ETH-denominated value of `assetLyingUnstakingVault` from `totalETHInProtocol` before computing `rewardAmount`, so the fee base only reflects appreciation of actively staked assets. Concretely, in `_getTotalEthInProtocol()` (or in `_updateRsETHPrice()` before the fee calculation), compute a separate `committedAssetsInETH` sum across all supported assets and deduct it:

```solidity
uint256 rewardAmount = (totalETHInProtocol - committedAssetsInETH) - previousTVL;
```

This mirrors the fix applied in RibbonVault (using `lockedBalanceSansPending` for the performance fee) and ensures fees are only charged on assets that are actively earning for the protocol.

---

### Proof of Concept

1. Protocol state: 1 000 ETH TVL, 1 000 rsETH supply, rsETH price = 1 ETH. 100 ETH worth of stETH is staked.
2. User calls `initiateWithdrawal(stETH, 100 rsETH)`. rsETH is transferred to the withdrawal manager (not burned). `assetsCommitted[stETH] = 100 stETH`. `expectedAssetAmount = 100 stETH` is locked.
3. Operator calls `transferAssetToLRTUnstakingVault(stETH, 100 stETH)`. 100 stETH now sits in `LRTUnstakingVault`.
4. stETH rebases: 100 stETH → 100.1 stETH. `totalETHInProtocol` increases by 0.1 ETH.
5. Anyone calls `updateRSETHPrice()`:
   - `totalETHInProtocol = 1 000.1 ETH` (includes 100.1 stETH in unstaking vault)
   - `previousTVL = 1 000 rsETH × 1 ETH = 1 000 ETH`
   - `rewardAmount = 0.1 ETH`
   - `protocolFeeInETH = 0.1 ETH × 10% = 0.01 ETH` (example 10% fee)
   - 0.01 ETH worth of rsETH minted to treasury
6. The withdrawer still receives exactly 100 stETH (their fixed `expectedAssetAmount`). The 0.1 stETH rebase gain belonged to remaining rsETH holders, but 10% of it (0.01 ETH) was taken as a protocol fee on assets that were not earning for the protocol — they were committed to a withdrawer at a fixed price. [10](#0-9) [11](#0-10) [12](#0-11)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L233-234)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L244-250)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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

**File:** contracts/LRTDepositPool.sol (L458-461)
```text
        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);

        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L495-496)
```text
        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;
```

**File:** contracts/LRTWithdrawalManager.sol (L162-176)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L730-737)
```text
                }
            }
        }

        _transferAsset(asset, user, request.expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
```
