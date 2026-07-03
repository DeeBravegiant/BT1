### Title
stETH Rebasing Rewards Permanently Unaccounted in `LRTConverter` — (File: contracts/LRTConverter.sol)

### Summary

`LRTConverter` holds stETH while waiting for operator-initiated unstaking. Because the protocol tracks the converter's stETH contribution to total assets via a fixed ETH-denominated counter (`ethValueInWithdrawal`) set at transfer time, daily Lido rebasing rewards that accrue on stETH sitting in the converter are never reflected in the protocol's total asset accounting. These rewards are permanently excluded from the rsETH price and can be permanently frozen in the contract.

### Finding Description

When stETH is moved from `LRTDepositPool` to `LRTConverter`, `transferAssetFromDepositPool` records the ETH value of the transferred amount at that moment:

```solidity
ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;
``` [1](#0-0) 

This fixed counter is then used as the sole representation of the converter's stETH holdings in the protocol's total asset calculation. In `LRTDepositPool.getAssetDistributionData()`, the stETH balance in the converter is explicitly zeroed out:

```solidity
assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
``` [2](#0-1) 

And `getETHDistributionData()` reads only the static counter:

```solidity
ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
``` [3](#0-2) 

stETH is a rebasing token: Lido's accounting oracle runs daily and increases every holder's balance proportionally. While stETH sits in `LRTConverter` between `transferAssetFromDepositPool` and `unstakeStEth`, the actual `stETH.balanceOf(converter)` grows, but `ethValueInWithdrawal` is never updated. The delta — the rebasing reward — is invisible to `getTotalAssetDeposits`, which drives the rsETH price.

Contrast this with every other location stETH can reside: the deposit pool, NDCs, and the unstaking vault all use live `IERC20(asset).balanceOf(...)` calls, so rebasing is captured automatically there. [4](#0-3) 

Furthermore, when the operator calls `unstakeStEth(amountToUnstake)`, the parameter is operator-supplied and is typically the originally tracked amount, not the rebased balance. The excess stETH (the rebasing reward) remains in `LRTConverter` with no accounting entry and no dedicated recovery path. Even if the operator unstakes the full rebased balance, `ethValueInWithdrawal` is not adjusted upward to reflect the extra stETH being sent to Lido, so the accounting remains broken until `claimStEth` is called and `_sendEthToDepositPool` zeroes out the counter. [5](#0-4) [6](#0-5) 

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

stETH rebasing rewards that accumulate in `LRTConverter` are not counted in `getTotalAssetDeposits`, causing the rsETH exchange rate to be understated for the entire duration stETH is held in the converter. If the operator only unstakes the originally tracked amount (the common case, since `ethValueInWithdrawal` is the reference), the rebasing rewards remain as orphaned stETH in `LRTConverter` with no accounting entry and no mechanism to route them back to the protocol. These rewards are effectively stolen from rsETH holders.

### Likelihood Explanation

**High.** stETH is a first-class supported asset in the protocol (registered under `ST_ETH_TOKEN`). `LRTConverter` is the designated path for unstaking stETH back to ETH. Lido rebasing occurs every ~24 hours unconditionally. Any stETH that transits through `LRTConverter` — which is the normal operational flow — will accumulate unaccounted rewards. No attacker action is required; the loss is automatic and continuous.

### Recommendation

Replace the fixed ETH-value counter approach for stETH in `LRTConverter` with a live balance query, or track stETH in shares (via `IStETH.getSharesByPooledEth` / `IStETH.getPooledEthByShares`) so that rebasing is automatically reflected. Alternatively, when `unstakeStEth` is called, always unstake the full `stETH.balanceOf(address(this))` rather than an operator-supplied amount, and update `ethValueInWithdrawal` to reflect the actual amount sent to the Lido withdrawal queue.

### Proof of Concept

1. Operator calls `LRTDepositPool.transferAssetToNodeDelegator` / `LRTConverter.transferAssetFromDepositPool(stETH, 1000e18)`.
   - `ethValueInWithdrawal` is set to `1000e18 * stETHPrice / 1e18` (e.g., `1000e18` ETH).
2. Lido oracle runs the next day. Every stETH holder's balance increases by ~0.004% (annualised ~1.5% APY).
   - `stETH.balanceOf(converter)` is now `1000.04e18`.
   - `ethValueInWithdrawal` is still `1000e18`.
3. `getTotalAssetDeposits(stETH)` → `getETHDistributionData()` → `ethValueInWithdrawal = 1000e18`. The `0.04e18` stETH reward is not counted. rsETH price is understated.
4. Operator calls `unstakeStEth(1000e18)` (the tracked amount). `0.04e18` stETH remains in `LRTConverter`.
5. After Lido finalises the withdrawal, `claimStEth` is called. ETH goes to the deposit pool. `ethValueInWithdrawal` is zeroed.
6. The `0.04e18` stETH is still in `LRTConverter`, has no accounting entry, and cannot be recovered through any normal protocol flow. It is permanently frozen. [7](#0-6) [5](#0-4) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTConverter.sol (L128-143)
```text
    function transferAssetFromDepositPool(
        address _asset,
        uint256 _amount
    )
        external
        onlySupportedERC20Token(_asset)
        onlyAssetTransferRole
    {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        ethValueInWithdrawal += (_amount * lrtOracle.getAssetPrice(_asset)) / 1e18;

        IERC20(_asset).safeTransferFrom(lrtDepositPoolAddress, address(this), _amount);
    }
```

**File:** contracts/LRTConverter.sol (L170-177)
```text
    function unstakeStEth(uint256 amountToUnstake)
        external
        nonReentrant
        onlyLRTOperator
        withinUnstakeLimits(amountToUnstake)
    {
        _unstakeStEth(amountToUnstake);
    }
```

**File:** contracts/LRTConverter.sol (L252-263)
```text
    function _sendEthToDepositPool(uint256 _amount) internal {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ethValueInWithdrawal > _amount) {
            ethValueInWithdrawal -= _amount;
        } else {
            ethValueInWithdrawal = 0;
        }
        // Send eth to deposit pool
        ILRTDepositPool(lrtDepositPoolAddress).receiveFromLRTConverter{ value: _amount }();
        emit EthTransferred(lrtDepositPoolAddress, _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L444-461)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));

        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);

        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L495-500)
```text
        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;

        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```
