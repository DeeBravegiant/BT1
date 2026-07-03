### Title
ETH Deposit Limit Bypass via Missing Deposit Amount in Limit Check — (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric comparison for ETH versus ERC-20 assets. For ERC-20 tokens the incoming deposit amount is included in the comparison (`totalAssetDeposits + amount > limit`), but for ETH the amount is omitted (`totalAssetDeposits > limit`). When the running ETH total equals the configured limit exactly, the check returns `false` and the deposit is accepted, pushing the protocol above its own cap. An unprivileged depositor can engineer this condition atomically in a single transaction.

---

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` at lines 676–682 of `LRTDepositPool.sol` reads:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount included
}
``` [1](#0-0) 

For ETH the guard fires only when the total **already** exceeds the limit. When `totalAssetDeposits == depositLimit` the expression `depositLimit > depositLimit` evaluates to `false`, so `_beforeDeposit` does not revert and `_mintRsETH` issues rsETH for the full deposit amount. [2](#0-1) 

`getTotalAssetDeposits(ETH)` aggregates `address(this).balance`, NDC balances, EigenLayer pod shares, unstaking amounts, converter ETH, and the unstaking vault balance — all of which increase immediately when ETH is deposited. [3](#0-2) 

The deposit limit is stored in `LRTConfig.depositLimitByAsset` and is the sole on-chain cap on how much ETH the protocol accepts. [4](#0-3) 

The public view helper `getAssetCurrentLimit` correctly reports `0` remaining capacity when `totalAssetDeposits == depositLimit`, so off-chain tooling and users see the cap as full — yet `depositETH` still accepts new deposits at that moment. [5](#0-4) 

---

### Impact Explanation

The deposit limit is the protocol's primary risk-management gate for ETH exposure. Bypassing it allows an attacker to mint rsETH backed by ETH that the protocol never intended to hold, diluting the rsETH exchange rate for all holders and expanding protocol risk beyond the configured ceiling. Because rsETH is minted at the current oracle rate for the full over-limit deposit, the attacker receives legitimately valued rsETH while the protocol absorbs the excess ETH risk. This constitutes the contract failing to deliver its promised risk bounds — **Low: contract fails to deliver promised returns** — with potential escalation to protocol insolvency if the limit was sized to bound EigenLayer slashing exposure.

---

### Likelihood Explanation

Any unprivileged depositor can trigger this in a single atomic transaction:

1. Read `depositLimit` and `getTotalAssetDeposits(ETH)` to compute the gap `G = depositLimit − currentTotal`.
2. In one transaction (via a helper contract): deposit `G` wei of ETH (a normal, within-limit deposit that brings the total to exactly `depositLimit`), then immediately deposit any additional amount `Y`.
3. On the second call, `totalAssetDeposits == depositLimit`, the check passes, and `Y` ETH worth of rsETH is minted beyond the cap.

No privileged role, oracle manipulation, or external dependency is required. The only precondition is that `G ≥ minAmountToDeposit`, which is trivially satisfiable when the limit has not yet been reached.

---

### Recommendation

Apply the same inclusive comparison for ETH that is already used for ERC-20 tokens:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This single-line change makes the ETH path consistent with the ERC-20 path and closes the bypass.

---

### Proof of Concept

```
State: depositLimit = 1000 ETH, getTotalAssetDeposits(ETH) = 999 ETH, minAmountToDeposit = 0.1 ETH

Tx (attacker contract, single transaction):
  Step 1 — depositETH{value: 1 ETH}(0, "")
    _checkIfDepositAmountExceedesCurrentLimit:
      totalAssetDeposits = 999 ETH
      999 > 1000  →  false  →  deposit accepted
    After: totalAssetDeposits = 1000 ETH  (exactly at limit)

  Step 2 — depositETH{value: 500 ETH}(0, "")
    _checkIfDepositAmountExceedesCurrentLimit:
      totalAssetDeposits = 1000 ETH
      1000 > 1000  →  false  →  deposit accepted   ← BYPASS
    rsETH minted for 500 ETH at current oracle rate
    After: totalAssetDeposits = 1500 ETH  (50% over limit)

Result: attacker holds rsETH for 500 ETH that the protocol was never supposed to accept.
```

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

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
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

**File:** contracts/LRTConfig.sol (L23-23)
```text
    mapping(address token => uint256 amount) public depositLimitByAsset;
```
