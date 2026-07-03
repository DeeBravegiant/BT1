### Title
Unbounded NDC Loop in Deposit Path Enables Gas-Limit DoS — (`contracts/LRTDepositPool.sol`)

### Summary

Every call to `depositETH` or `depositAsset` synchronously iterates over the entire `nodeDelegatorQueue`, making three external calls per NDC. Because `maxNodeDelegatorLimit` is an admin-settable, unbounded integer with no gas-cost ceiling, a sufficiently large queue causes the deposit path to exceed the block gas limit, permanently freezing deposits.

### Finding Description

The call chain is:

```
depositETH / depositAsset
  └─ _beforeDeposit
       └─ _checkIfDepositAmountExceedesCurrentLimit
            └─ getTotalAssetDeposits
                 └─ getAssetDistributionData  (LST path)
                 └─ getETHDistributionData    (ETH path)
```

Both distribution functions contain an unbounded `for` loop over `nodeDelegatorQueue`:

**LST path** — `getAssetDistributionData` (lines 447–456):
```solidity
for (uint256 i; i < ndcsCount;) {
    assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);          // external call 1
    assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);   // external call 2
    assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset); // external call 3
    unchecked { ++i; }
}
```

**ETH path** — `getETHDistributionData` (lines 484–491):
```solidity
for (uint256 i; i < ndcsCount;) {
    ethLyingInNDCs += nodeDelegatorQueue[i].balance;
    ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();    // external call 2
    ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(...); // external call 3
    unchecked { ++i; }
}
```

Each of the three external calls per NDC is expensive:

- `IERC20.balanceOf`: ~2,700 gas (cold slot)
- `getAssetBalance` → `NodeDelegatorHelper.getAssetBalance` → `DelegationManager.getWithdrawableShares()`: ~30,000–50,000 gas (EigenLayer storage reads)
- `getAssetUnstaking` → `DelegationManager.getQueuedWithdrawals()` + inner loop over queued withdrawals: ~30,000–50,000 gas

Estimated total per NDC: **~62,000–102,000 gas**

| NDC count | Estimated gas | vs. 30 M block limit |
|-----------|--------------|----------------------|
| 10 (default) | ~0.6–1 M | safe |
| 50 | ~3.1–5.1 M | safe |
| 300 | ~18.6–30.6 M | at the limit |
| 400 | ~24.8–40.8 M | **exceeds limit** |

`maxNodeDelegatorLimit` is initialized to 10 but is freely raisable by the admin via `updateMaxNodeDelegatorLimit` with no upper bound:

```solidity
function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
    if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
        revert InvalidMaximumNodeDelegatorLimit();
    }
    maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
    ...
}
```

Once the admin raises the limit and fills the queue (a legitimate operational scaling decision), **any unprivileged depositor** calling `depositETH` or `depositAsset` triggers the full loop. There is no way for the depositor to avoid it, and no way for the protocol to recover without removing NDCs (which itself requires each NDC to have zero balances).

### Impact Explanation

When `nodeDelegatorQueue.length` grows large enough (~300–400 entries), every deposit transaction runs out of gas and reverts. Because the loop is embedded in the mandatory `_beforeDeposit` check, there is no alternative code path. Deposits are permanently frozen until the admin removes NDCs — but removal requires each NDC to have zero EigenLayer balances, which may be impossible if assets are actively staked.

**Impact: Medium — Unbounded gas consumption / temporary-to-permanent freezing of the deposit function.**

### Likelihood Explanation

The precondition is an admin raising `maxNodeDelegatorLimit` to a large value for legitimate scaling reasons (e.g., supporting hundreds of validators). This is a plausible operational decision. The admin may not be aware of the gas implications. Once the queue is large enough, the freeze is triggered by any ordinary depositor with no special privileges.

### Recommendation

1. **Cap `maxNodeDelegatorLimit`** at a safe maximum (e.g., 50) that keeps worst-case gas well below the block limit.
2. **Cache the total** in a storage variable updated on deposit/withdrawal rather than recomputing it on every deposit.
3. Alternatively, **paginate** the NDC loop or move the distribution computation off-chain and pass a signed result on-chain.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fork test (local/private testnet only)
import "forge-std/Test.sol";

contract GasDoSTest is Test {
    LRTDepositPool pool;
    // ... setup: deploy pool, raise maxNodeDelegatorLimit to 400,
    //            deploy & register 400 mock NDCs

    function test_depositGasExceedsBlockLimit() public {
        uint256 gasBefore = gasleft();
        try pool.depositETH{value: 1 ether}(0, "") {} catch {}
        uint256 gasUsed = gasBefore - gasleft();
        // Assert gas used approaches or exceeds 30_000_000
        assertGt(gasUsed, 25_000_000, "gas approaching block limit");
    }
}
```

---

**File references:**

`depositETH` / `depositAsset` entry points: [1](#0-0) 

`_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit`: [2](#0-1) 

Unbounded loop in `getAssetDistributionData`: [3](#0-2) 

Unbounded loop in `getETHDistributionData`: [4](#0-3) 

`updateMaxNodeDelegatorLimit` — no upper bound: [5](#0-4) 

`getAssetUnstaking` — nested EigenLayer loop per NDC: [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-118)
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

    /// @notice helps user stake LST to the protocol
    /// @param asset LST asset address to stake
    /// @param depositAmount LST asset amount to stake
    /// @param minRSETHAmountExpected Minimum amount of rseth to receive
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

**File:** contracts/LRTDepositPool.sol (L290-297)
```text
    function updateMaxNodeDelegatorLimit(uint256 maxNodeDelegatorLimit_) external onlyLRTAdmin {
        if (maxNodeDelegatorLimit_ < nodeDelegatorQueue.length) {
            revert InvalidMaximumNodeDelegatorLimit();
        }

        maxNodeDelegatorLimit = maxNodeDelegatorLimit_;
        emit MaxNodeDelegatorLimitUpdated(maxNodeDelegatorLimit);
    }
```

**File:** contracts/LRTDepositPool.sol (L446-456)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L482-493)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }
```

**File:** contracts/LRTDepositPool.sol (L648-682)
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
    }

    /// @notice checks if deposit amount exceeds current limit
    /// @param asset Asset address
    /// @param amount Asset amount
    /// @return bool true if deposit amount exceeds current limit
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```

**File:** contracts/NodeDelegator.sol (L405-427)
```text
    function getAssetUnstaking(address asset) external view returns (uint256 amount) {
        (IDelegationManager.Withdrawal[] memory queuedWithdrawals, uint256[][] memory withdrawalShares) =
            _getDelegationManager().getQueuedWithdrawals(address(this));

        for (uint256 withdrawalIndex = 0; withdrawalIndex < queuedWithdrawals.length; withdrawalIndex++) {
            IDelegationManager.Withdrawal memory withdrawal = queuedWithdrawals[withdrawalIndex];

            for (uint256 strategyIndex = 0; strategyIndex < withdrawal.strategies.length; strategyIndex++) {
                IStrategy strategy = withdrawal.strategies[strategyIndex];

                address strategyAsset = address(strategy) == address(lrtConfig.beaconChainETHStrategy())
                    ? LRTConstants.ETH_TOKEN
                    : address(strategy.underlyingToken());

                if (strategyAsset != asset) continue;

                uint256 sharesToUnstake = withdrawalShares[withdrawalIndex][strategyIndex];
                amount += strategyAsset == LRTConstants.ETH_TOKEN
                    ? sharesToUnstake
                    : strategy.sharesToUnderlyingView(sharesToUnstake);
            }
        }
    }
```
