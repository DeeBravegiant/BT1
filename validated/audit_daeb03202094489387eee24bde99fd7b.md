### Title
Direct Token Transfer to `LRTDepositPool` Inflates `getTotalAssetDeposits`, Permanently Blocking Deposits — (File: `contracts/LRTDepositPool.sol`)

### Summary
An unprivileged attacker can send ETH or LST tokens directly to the `LRTDepositPool` contract. Because `getTotalAssetDeposits` is computed from live token balances rather than an internal accounting ledger, the artificially inflated balance causes `_checkIfDepositAmountExceedesCurrentLimit` to return `true`, reverting every subsequent call to `depositETH` or `depositAsset` with `MaximumDepositLimitReached`. The attacker can repeat the transfer whenever the admin raises the deposit limit, making the block effectively permanent at negligible cost.

### Finding Description

`getTotalAssetDeposits` aggregates actual on-chain balances across the protocol:

- For ETH it calls `getETHDistributionData`, which reads `address(this).balance` directly.
- For LST tokens it reads `IERC20(asset).balanceOf(address(this))` directly. [1](#0-0) [2](#0-1) 

These raw balances feed into the deposit-limit guard:

```solidity
// ETH path
return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));

// LST path
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
``` [3](#0-2) 

`LRTDepositPool` exposes a bare `receive()` function, so any EOA or contract can push ETH into it without going through `depositETH`. LST tokens can be sent via a plain ERC-20 `transfer`. Neither path mints rsETH, so the protocol's internal accounting is not updated, but the raw balance—and therefore `totalAssetDeposits`—rises. [4](#0-3) 

When `_beforeDeposit` is subsequently called by a legitimate depositor, `_checkIfDepositAmountExceedesCurrentLimit` reads the inflated balance and reverts: [5](#0-4) 

### Impact Explanation

Every call to `depositETH` and `depositAsset` is blocked for the targeted asset. Users cannot enter the protocol. Because the attacker can repeat the transfer immediately after the admin raises the deposit limit, the block can be maintained indefinitely. This constitutes **temporary (and practically indefinite) freezing of the deposit functionality**, matching the "Medium — Temporary freezing of funds" tier in the allowed scope.

### Likelihood Explanation

The attack is cheap when the deposit limit is near its cap (a common operational state for a live protocol). The attacker spends only the marginal amount of tokens needed to push `totalAssetDeposits` past the limit. No special role or permission is required; any external address can call `receive()` or `IERC20.transfer`. The attack is repeatable on every admin response.

### Recommendation

Replace live-balance reads in `getTotalAssetDeposits` / `getAssetDistributionData` with an internal accounting variable that is incremented only through the official deposit path and decremented through the official withdrawal path. Alternatively, exclude the deposit pool's own balance from the limit check and instead track deposits via a dedicated `totalDeposited[asset]` mapping that is updated atomically inside `depositETH` and `depositAsset`.

### Proof of Concept

1. Protocol has `depositLimitByAsset(stETH) = 1000 stETH` and `totalAssetDeposits(stETH) = 999.9 stETH`.
2. Attacker calls `stETH.transfer(address(lrtDepositPool), 0.2 stETH)` directly.
3. `IERC20(stETH).balanceOf(address(lrtDepositPool))` increases by 0.2 stETH; no rsETH is minted.
4. `getTotalAssetDeposits(stETH)` now returns `1000.1 stETH`.
5. Any user calling `depositAsset(stETH, amount, ...)` hits `_checkIfDepositAmountExceedesCurrentLimit` → `1000.1 + amount > 1000` → `true` → `revert MaximumDepositLimitReached`.
6. Admin raises limit to `1001 stETH`; attacker sends another `0.2 stETH`; deposits are blocked again. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
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

**File:** contracts/LRTDepositPool.sol (L440-461)
```text
        if (asset == LRTConstants.ETH_TOKEN) {
            return getETHDistributionData();
        }

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

**File:** contracts/LRTDepositPool.sol (L467-500)
```text
    function getETHDistributionData()
        public
        view
        override
        returns (
            uint256 ethLyingInDepositPool,
            uint256 ethLyingInNDCs,
            uint256 ethStakedInEigenLayer,
            uint256 ethUnstakingFromEigenLayer,
            uint256 ethLyingInConverter,
            uint256 ethLyingInUnstakingVault
        )
    {
        ethLyingInDepositPool = address(this).balance;

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

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;

        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
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
