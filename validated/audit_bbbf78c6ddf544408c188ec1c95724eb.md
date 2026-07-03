### Title
Donated tokens inflate `getTotalAssetDeposits` and permanently block legitimate depositors from depositing — (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.getAssetDistributionData` measures the pool's asset balance using raw `IERC20.balanceOf` / `address(this).balance` calls. Any tokens sent directly to the contract (without going through `depositAsset` / `depositETH`) inflate the apparent total deposits. Because `_checkIfDepositAmountExceedesCurrentLimit` compares this inflated total against the configured deposit cap, a low-cost donation can push the apparent total over the cap and cause every subsequent legitimate `depositAsset` / `depositETH` call to revert with `MaximumDepositLimitReached`.

---

### Finding Description

`getAssetDistributionData` computes the deposit-pool slice of total deposits as the raw on-chain balance:

```solidity
// contracts/LRTDepositPool.sol  line 444
assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

and for ETH:

```solidity
// contracts/LRTDepositPool.sol  line 480
ethLyingInDepositPool = address(this).balance;
``` [1](#0-0) [2](#0-1) 

`getTotalAssetDeposits` sums all locations including `assetLyingInDepositPool`:

```solidity
// contracts/LRTDepositPool.sol  lines 385-397
return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer
        + assetLyingInConverter + assetLyingUnstakingVault);
``` [3](#0-2) 

`_checkIfDepositAmountExceedesCurrentLimit` then compares this total against the cap:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [4](#0-3) 

`_beforeDeposit` (called by both `depositAsset` and `depositETH`) reverts if this check returns `true`:

```solidity
// contracts/LRTDepositPool.sol  lines 661-663
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [5](#0-4) 

The contract exposes a bare `receive()` for ETH and accepts any ERC-20 transfer:

```solidity
// contracts/LRTDepositPool.sol  line 58
receive() external payable { }
``` [6](#0-5) 

Because there is no separate accounting variable tracking only protocol-originated deposits, a direct token transfer to the contract is indistinguishable from a legitimate deposit in the balance-based accounting.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

An attacker who donates a quantity of tokens equal to `depositLimitByAsset(asset) − currentTotalDeposits` (or any amount that pushes the sum over the cap) causes every subsequent `depositAsset` / `depositETH` call to revert with `MaximumDepositLimitReached`. The deposit functionality is frozen until governance raises the cap or the donated tokens are somehow removed from the accounting. Because the donated tokens are counted in `getTotalAssetDeposits`, moving them to a NodeDelegator does not help — they are still counted in `assetLyingInNDCs`. The only remediation available to the protocol is to raise the deposit limit, which may not be desirable.

---

### Likelihood Explanation

**Low-Medium.**

- No special role or permission is required; any address can call `IERC20(asset).transfer(address(lrtDepositPool), amount)` or send ETH directly.
- The attacker permanently loses the donated tokens (no recovery path), so the attack is economically irrational for profit but trivially executable as a griefing attack.
- Deposit limits are typically set close to the current TVL (e.g., `100_000 ether` per asset as seen in `LRTConfig.initialize`), so the donation amount needed to trigger the freeze can be small relative to the protocol's TVL. [7](#0-6) 

---

### Recommendation

Introduce an explicit internal accounting variable (e.g., `depositedAmount[asset]`) that is incremented only inside `depositAsset` / `depositETH` and decremented on withdrawals. Replace the raw `balanceOf` / `address(this).balance` calls in `getAssetDistributionData` with this tracked variable for the deposit-pool slice. This mirrors the fix described in the referenced report ("Donated token can inflate contract balance") and eliminates the ability of external token transfers to affect deposit-limit accounting.

---

### Proof of Concept

```
1. Protocol state: depositLimitByAsset(stETH) = 100_000e18,
                   getTotalAssetDeposits(stETH) = 99_999e18.

2. Attacker executes:
       IERC20(stETH).transfer(address(lrtDepositPool), 1e18);
   Cost: 1 stETH (~$3 500 at current prices).

3. Now IERC20(stETH).balanceOf(address(lrtDepositPool)) increases by 1e18.
   getTotalAssetDeposits(stETH) returns 100_000e18.

4. Any user calls:
       lrtDepositPool.depositAsset(stETH, anyAmount, 0, "");

5. _beforeDeposit → _checkIfDepositAmountExceedesCurrentLimit:
       totalAssetDeposits + amount = 100_000e18 + anyAmount > 100_000e18  → true
   → revert MaximumDepositLimitReached();

6. All stETH deposits are frozen. Moving the donated stETH to a NodeDelegator
   keeps it in getTotalAssetDeposits (assetLyingInNDCs), so the freeze persists.
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

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
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

**File:** contracts/LRTConfig.sol (L56-57)
```text
        _addNewSupportedAsset(stETH, 100_000 ether);
        _addNewSupportedAsset(ethX, 100_000 ether);
```
