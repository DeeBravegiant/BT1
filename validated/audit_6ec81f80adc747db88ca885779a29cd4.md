### Title
ETH Deposit Limit Bypass via Missing Amount in Limit Check - (`contracts/LRTDepositPool.sol`)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric check for ETH vs. LST assets. For ETH it checks only whether the *current* total already exceeds the limit, omitting the incoming deposit amount. For LSTs the incoming amount is correctly included. When total ETH deposits are exactly at the limit the check passes, allowing unlimited additional ETH to be deposited and breaking the deposit-cap invariant.

### Finding Description
The function at issue:

```solidity
// contracts/LRTDepositPool.sol
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount NOT included
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount included
}
``` [1](#0-0) 

For ETH the guard is `totalAssetDeposits > limit` (strict greater-than, pre-deposit snapshot). For LSTs it is `totalAssetDeposits + amount > limit` (post-deposit projection). The invariant the protocol intends to enforce is that total ETH deposits must not exceed `depositLimitByAsset[ETH]`.

**Attack path:**

1. Observe `totalAssetDeposits(ETH) = limit - X` for some `X > 0`.
2. Call `depositETH` with `msg.value = X`. The check `(limit - X) > limit` is `false` → deposit succeeds. Now `totalAssetDeposits = limit`.
3. Call `depositETH` again with any `msg.value = Y`. The check `limit > limit` is `false` → deposit succeeds. Now `totalAssetDeposits = limit + Y`, exceeding the cap.

Steps 2 and 3 can be executed in consecutive transactions within the same block, making the window trivially exploitable. `getTotalAssetDeposits` for ETH aggregates balances across the deposit pool, all NDCs, EigenLayer pod shares, the unstaking vault, and the converter: [2](#0-1) 

All of these are on-chain and observable, so the attacker can compute the exact top-up amount needed.

The `_beforeDeposit` call site that invokes this check: [3](#0-2) 

### Impact Explanation
The ETH deposit cap (`depositLimitByAsset[ETH]`) is a protocol-level safety ceiling. Bypassing it allows the protocol to absorb more ETH than intended, potentially over-exposing it to EigenLayer strategies and violating the invariant that `getTotalAssetDeposits(ETH) ≤ depositLimitByAsset[ETH]`. No funds are directly stolen, but the protocol fails to deliver its promised deposit-limit guarantee.

**Impact: Low** — Contract fails to deliver promised returns (deposit cap enforcement), but does not lose value.

### Likelihood Explanation
Any unprivileged depositor can trigger this. The only precondition is that `totalAssetDeposits(ETH)` is exactly at the limit, a state the attacker can engineer themselves with a single preparatory deposit. No special permissions, flash loans, or oracle manipulation are required.

**Likelihood: Medium.**

### Recommendation
Include the incoming `amount` in the ETH branch, matching the LST branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    // unified check for both ETH and LSTs
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
```
depositLimit  = 1_000 ETH
totalDeposits = 900 ETH   (initial state)

// Step 1 – fill to exactly the limit
depositETH{value: 100 ETH}()
  check: 900 > 1000 → false → allowed
  totalDeposits = 1_000 ETH

// Step 2 – bypass: deposit beyond the limit
depositETH{value: 500 ETH}()
  check: 1000 > 1000 → false → allowed   ← BUG
  totalDeposits = 1_500 ETH              ← 50% over cap
``` [1](#0-0) [4](#0-3)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L648-670)
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
