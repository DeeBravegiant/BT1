### Title
Unbounded Nested-Loop Gas Consumption in Public `updateRSETHPrice()` Freezes the Price Oracle - (File: `contracts/LRTOracle.sol`)

### Summary
`LRTOracle.updateRSETHPrice()` is a public, permissionless function. Its internal call chain performs three nested iterations — over supported assets, over node delegators, and over EigenLayer queued withdrawals — with no upper-bound guard on total gas. As the protocol accumulates more supported assets, more NodeDelegator contracts, and more queued EigenLayer withdrawals, the function's gas cost grows unboundedly. Once it exceeds the block gas limit the price oracle can no longer be updated, freezing deposits and withdrawals that depend on a live `rsETHPrice`.

### Finding Description

`updateRSETHPrice()` is declared `public whenNotPaused` with no role restriction: [1](#0-0) 

It delegates to `_updateRsETHPrice()`, which calls `_getTotalEthInProtocol()`: [2](#0-1) 

`_getTotalEthInProtocol()` loops over every entry in `supportedAssetList` and, for each asset, calls `ILRTDepositPool.getTotalAssetDeposits(asset)`.

`getTotalAssetDeposits()` calls `getAssetDistributionData()`, which loops over every entry in `nodeDelegatorQueue`: [3](#0-2) 

For each NDC it calls `INodeDelegator.getAssetUnstaking(asset)`, which fetches **all** queued EigenLayer withdrawals and iterates over them with a nested loop: [4](#0-3) 

The total gas complexity is **O(assets × NDCs × queued\_withdrawals\_per\_NDC)**. Each dimension is:

| Dimension | Controlled by | Bound |
|---|---|---|
| `supportedAssets` | Admin (`TIME_LOCK_ROLE`) | No hard cap |
| `nodeDelegatorQueue` | Admin | `maxNodeDelegatorLimit` (default 10, updatable) |
| EigenLayer queued withdrawals | Operator (`initiateUnstaking`) | `maxUncompletedWithdrawalCount` (per vault) |

Even at modest values (e.g., 5 assets × 10 NDCs × 50 queued withdrawals = 2 500 iterations, each involving multiple `SLOAD`s and external calls), the function can consume millions of gas. There is no per-call gas budget check or iteration cap inside `updateRSETHPrice()`.

The same nested loop is also traversed on every user deposit via `depositETH()` / `depositAsset()` → `_checkIfDepositAmountExceedesCurrentLimit()` → `getTotalAssetDeposits()`: [5](#0-4) 

### Impact Explanation

If `updateRSETHPrice()` reverts out-of-gas, `rsETHPrice` is never updated. All downstream logic that reads `lrtOracle.rsETHPrice()` — including `getRsETHAmountToMint()` for deposits and `getExpectedAssetAmount()` for withdrawals — operates on a stale price. In the worst case the function becomes permanently uncallable at the current block gas limit, effectively freezing the oracle and halting normal protocol operation. This maps to **Medium — Unbounded gas consumption** and **Medium — Temporary freezing of funds**.

### Likelihood Explanation

The protocol is designed to support multiple LSTs and multiple NodeDelegator contracts. Operators legitimately call `initiateUnstaking()` repeatedly during normal restaking operations, accumulating queued withdrawals. No malicious actor is required; ordinary protocol growth is sufficient to push gas consumption past the block limit. The function is callable by any address, so any user can trigger the OOG revert once the state is large enough.

### Recommendation

1. **Cache `getQueuedWithdrawals` once per NDC** rather than calling it once per `(NDC, asset)` pair inside `getAssetDistributionData`.
2. **Introduce a hard cap** on `maxNodeDelegatorLimit` and `maxUncompletedWithdrawalCount` that is validated against a gas budget estimate.
3. **Restrict `updateRSETHPrice()`** to a keeper/operator role, or add a paginated variant so the update can be split across multiple transactions.
4. **Separate the accounting view** (`_getTotalEthInProtocol`) from the price-update write path so that a gas-heavy read does not block the write.

### Proof of Concept

Call trace for a single `updateRSETHPrice()` invocation with 5 assets, 10 NDCs, and 50 queued withdrawals per NDC:

```
updateRSETHPrice()
└─ _updateRsETHPrice()
   └─ _getTotalEthInProtocol()          // LRTOracle.sol:336 — loop over 5 assets
      └─ getTotalAssetDeposits(asset)   // × 5
         └─ getAssetDistributionData()  // LRTDepositPool.sol:447 — loop over 10 NDCs
            └─ getAssetUnstaking(asset) // × 5 × 10 = 50 calls
               └─ getQueuedWithdrawals()// EigenLayer external call
               └─ inner loop × 50      // NodeDelegator.sol:409-426
```

Total inner iterations: 5 × 10 × 50 = **2 500**, each with multiple cold `SLOAD`s and external calls. At ~2 100 gas per cold `SLOAD` and ~700 gas per warm call, this easily exceeds 5 000 000 gas — approaching or exceeding Ethereum's block gas limit for a single transaction.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
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
