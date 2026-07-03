### Title
Single `maxNegligibleAmount` Threshold Applied Across All Assets Without Decimal Normalization - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool` and `LRTConfig` each store a single `maxNegligibleAmount` value that is compared directly against raw token balances for every supported asset, regardless of each token's decimals. This is the exact decimal-precision inconsistency described in the reference report: a fixed threshold calibrated for one decimal scale silently misbehaves for tokens at a different scale.

### Finding Description
`LRTDepositPool` declares two decimal-agnostic thresholds:

```solidity
uint256 public minAmountToDeposit;   // line 30
uint256 public maxNegligibleAmount;  // line 36
```

`_checkResidueLSTBalance` iterates over every supported LST and compares each one's raw balance against the single `maxNegligibleAmount`:

```solidity
// LRTDepositPool.sol lines 638-644
assetBalance = IERC20(supportedAssets[i]).balanceOf(nodeDelegatorAddress)
    + INodeDelegator(nodeDelegatorAddress).getAssetBalance(supportedAssets[i]);
assetBalance += INodeDelegator(nodeDelegatorAddress).getAssetUnstaking(supportedAssets[i]);

if (assetBalance > maxNegligibleAmount) {
    revert NodeDelegatorHasAssetBalance(supportedAssets[i], assetBalance);
}
```

`LRTConfig.removeSupportedAsset` applies the same pattern:

```solidity
// LRTConfig.sol line 82
if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
    revert CannotRemoveAssetWithDeposits(asset);
}
```

And `_beforeDeposit` enforces `minAmountToDeposit` uniformly:

```solidity
// LRTDepositPool.sol line 657
if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
    revert InvalidAmountToDeposit();
}
```

None of these comparisons fetch or apply the token's `decimals()`. The protocol's `addNewSupportedAsset` imposes no decimal restriction, so any ERC-20 can be added.

### Impact Explanation
Suppose `maxNegligibleAmount` is set to `1e15` (calibrated as "negligible" for 18-decimal LSTs, ≈ 0.001 ETH). If a 6-decimal token is later added as a supported asset:

- **NodeDelegator removal bypass**: A NodeDelegator holding `1e6` raw units of the 6-decimal token (= 1 full token, potentially worth significant value) satisfies `1e6 > 1e15 → false`, so `_checkResidueLSTBalance` does not revert. The NodeDelegator is removed from the queue while still holding that token balance. Those funds are stranded in the dequeued NodeDelegator and excluded from the protocol's TVL accounting, causing rsETH to be over-collateralized on paper but under-collateralized in practice.

- **Asset removal bypass**: `removeSupportedAsset` passes the same check, allowing an asset with real user deposits to be deleted from the registry while deposits remain locked in NodeDelegators.

- **Deposit blocking (inverse direction)**: If `minAmountToDeposit` is calibrated for a 6-decimal token (e.g., `1e4`), depositing an 18-decimal token requires only `1e4` raw units (= `0.00000000000001` ETH), effectively removing the minimum deposit guard entirely.

### Likelihood Explanation
The current supported assets (stETH, ETHx) are both 18-decimal, so the bug is dormant today. However, `addNewSupportedAsset` is callable by `TIME_LOCK_ROLE` with no decimal restriction, and the protocol is explicitly designed to expand its asset list. Any future addition of a non-18-decimal LST (e.g., a rebasing token with 6 or 8 decimals) immediately activates the inconsistency without any code change. The threshold miscalibration would not be obvious to an operator setting `maxNegligibleAmount` once for the whole system.

### Recommendation
Normalize raw balances to a common precision (e.g., 18 decimals) before comparing against the threshold, or maintain a per-asset negligible amount mapping. For example:

```solidity
uint256 normalizedBalance = assetBalance * (10 ** (18 - IERC20Metadata(asset).decimals()));
if (normalizedBalance > maxNegligibleAmount) { ... }
```

Apply the same normalization in `removeSupportedAsset` and `_beforeDeposit`.

### Proof of Concept
1. Admin calls `addNewSupportedAsset(tokenX, depositLimit)` where `tokenX` is a 6-decimal token.
2. Admin sets `maxNegligibleAmount = 1e15` (appropriate for 18-decimal tokens, representing ~0.001 ETH).
3. Users deposit `tokenX`; a NodeDelegator accumulates `5e6` raw units (= 5 tokenX, e.g., worth $5 if tokenX ≈ $1).
4. Admin calls `removeNodeDelegatorContractFromQueue(nodeDelegator)`.
5. `_checkResidueLSTBalance` evaluates `5e6 > 1e15` → `false` → no revert.
6. The NodeDelegator is removed from the queue. The 5 tokenX remain in the dequeued contract, excluded from `getTotalAssetDeposits`, and inaccessible through normal protocol flows — a permanent fund freeze for those depositors. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L29-36)
```text
    uint256 public maxNodeDelegatorLimit;
    uint256 public minAmountToDeposit;

    mapping(address => uint256) public isNodeDelegator; // 0: not a node delegator, 1: is a node delegator
    address[] public nodeDelegatorQueue;

    /// @notice maximum amount that can be ignored
    uint256 public maxNegligibleAmount;
```

**File:** contracts/LRTDepositPool.sol (L636-644)
```text
            }

            assetBalance = IERC20(supportedAssets[i]).balanceOf(nodeDelegatorAddress)
                + INodeDelegator(nodeDelegatorAddress).getAssetBalance(supportedAssets[i]);
            assetBalance += INodeDelegator(nodeDelegatorAddress).getAssetUnstaking(supportedAssets[i]);

            if (assetBalance > maxNegligibleAmount) {
                revert NodeDelegatorHasAssetBalance(supportedAssets[i], assetBalance);
            }
```

**File:** contracts/LRTDepositPool.sol (L655-659)
```text
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```

**File:** contracts/LRTConfig.sol (L80-84)
```text
        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }
```
