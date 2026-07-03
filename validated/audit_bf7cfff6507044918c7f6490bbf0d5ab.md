### Title
Donation Attack via `balanceOf` in `getAssetDistributionData` Blocks Deposits - (File: `contracts/LRTDepositPool.sol`)

---

### Summary

An unprivileged attacker can donate LST tokens or ETH directly to `LRTDepositPool` to inflate `getTotalAssetDeposits()`, which relies on `IERC20(asset).balanceOf(address(this))` and `address(this).balance`. This inflated total can push the reported deposits above the configured deposit limit, causing all subsequent deposits for that asset to revert with `MaximumDepositLimitReached`, effectively blocking the deposit path.

---

### Finding Description

`getAssetDistributionData()` computes the amount of each asset held in the protocol using live `balanceOf` and native balance reads: [1](#0-0) 

```solidity
assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
// ...
assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
```

For ETH: [2](#0-1) 

```solidity
ethLyingInDepositPool = address(this).balance;
```

These values feed directly into `getTotalAssetDeposits()`: [3](#0-2) 

Which is then used in `_checkIfDepositAmountExceedesCurrentLimit()`: [4](#0-3) 

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This check is enforced in `_beforeDeposit()`, which is called by both `depositETH()` and `depositAsset()`: [5](#0-4) 

```solidity
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
```

Because `LRTDepositPool` has an open `receive()` function: [6](#0-5) 

```solidity
receive() external payable { }
```

Any attacker can send ETH or transfer LST tokens directly to the contract address without going through any deposit logic, inflating the balance without minting rsETH or updating any internal accounting state variable.

---

### Impact Explanation

**Impact: Medium — Temporary freezing of funds (deposit path).**

Once the attacker's donation pushes `getTotalAssetDeposits(asset)` above `depositLimitByAsset(asset)`, every call to `depositETH()` or `depositAsset()` for that asset reverts. Legitimate users cannot deposit until an admin increases the deposit limit. The donated tokens cannot be recovered by the attacker (they are permanently donated), but the DoS persists until admin intervention. The `getAssetCurrentLimit()` view function also returns 0, misleading integrators and front-ends. [7](#0-6) 

---

### Likelihood Explanation

**Likelihood: Medium.**

- The attack requires no special permissions — any address can call `IERC20.transfer()` to `LRTDepositPool` or send ETH via `receive()`.
- The cost to the attacker is the donated tokens (no recovery possible), making it a griefing attack.
- The attack is most effective when `totalAssetDeposits` is already close to `depositLimitByAsset`, which is a normal operational state as the protocol approaches capacity.
- The deposit limit is a finite configured value, so the required donation amount is bounded and potentially small if the limit is nearly reached.

---

### Recommendation

- **Short term:** Track asset balances using internal state variables (e.g., `assetBalance[asset]`) that are incremented only on legitimate deposits and decremented on withdrawals/transfers. Replace `IERC20(asset).balanceOf(address(this))` and `address(this).balance` in `getAssetDistributionData()` with these tracked variables.
- **Long term:** Add a sweep/recovery function (admin-only) to handle accidentally or maliciously donated tokens, and add invariant tests that verify `totalAssetDeposits` cannot be inflated by direct transfers.

---

### Proof of Concept

```
1. Assume depositLimitByAsset(stETH) = 10,000 stETH
   and current getTotalAssetDeposits(stETH) = 9,999 stETH (1 stETH remaining capacity)

2. Attacker calls:
   stETH.transfer(address(lrtDepositPool), 2 stETH)
   // No rsETH is minted; no internal accounting is updated

3. Now getTotalAssetDeposits(stETH) returns 10,001 stETH
   (because assetLyingInDepositPool = IERC20(stETH).balanceOf(address(lrtDepositPool)) = 2 stETH extra)

4. Any user calling depositAsset(stETH, amount, ...) now hits:
   _checkIfDepositAmountExceedesCurrentLimit(stETH, amount)
   → totalAssetDeposits + amount = 10,001 + amount > 10,000
   → revert MaximumDepositLimitReached()

5. All stETH deposits are blocked until admin increases the deposit limit.

For ETH:
   attacker sends ETH via: (bool ok,) = address(lrtDepositPool).call{value: X}("")
   → address(this).balance increases
   → getETHDistributionData() returns inflated ethLyingInDepositPool
   → depositETH() reverts for all users
```

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

**File:** contracts/LRTDepositPool.sol (L444-448)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));

        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L660-663)
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
