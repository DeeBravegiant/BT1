### Title
Deposit Limit DoS via Direct Token Donation to `LRTDepositPool` - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.getTotalAssetDeposits` measures asset totals using raw `balanceOf` / `address(this).balance`. An unprivileged attacker can donate LST tokens or ETH directly to the contract, artificially inflating the measured total past the configured deposit limit, permanently blocking all new deposits until an admin raises the limit.

### Finding Description

`_checkIfDepositAmountExceedesCurrentLimit` gates every call to `depositAsset` and `depositETH`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [1](#0-0) 

`getTotalAssetDeposits` delegates to `getAssetDistributionData`, which for LST assets reads:

```solidity
assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
``` [2](#0-1) 

And for ETH reads:

```solidity
ethLyingInDepositPool = address(this).balance;
``` [3](#0-2) 

Both values are fully attacker-controlled: the contract exposes an unrestricted `receive()` for ETH, and any ERC-20 holder can `transfer` tokens directly to the contract address. [4](#0-3) 

Once the attacker donates enough to push `totalAssetDeposits` above `depositLimitByAsset`, every subsequent call to `depositAsset` or `depositETH` reverts with `MaximumDepositLimitReached`. [5](#0-4) 

The donated tokens are irrecoverable by the attacker (no sweep function exists for them), but the DoS persists until an admin calls `updateAssetDepositLimit` to raise the cap — at which point the attacker can donate again. [6](#0-5) 

### Impact Explanation

All new deposits of the targeted LST asset (or ETH) are blocked for every user. This is a **temporary freezing of funds** (Medium): no existing funds are lost, but the protocol's primary deposit path is rendered non-functional until admin intervention. Because the attacker can repeat the donation after each admin remediation, the effective DoS can be sustained indefinitely at the cost of the donated tokens.

### Likelihood Explanation

**Medium.** The attacker must hold enough of the targeted LST (or ETH) to close the gap between current deposits and the deposit limit. When the protocol is operating near its cap (a normal condition as limits fill up), the required donation can be negligibly small — even 1 wei of ETH suffices for the ETH path once `totalAssetDeposits` already equals `depositLimitByAsset`. No special role or permission is required; any token holder can execute this.

### Recommendation

Track deposited amounts in an explicit internal accounting variable rather than relying on raw `balanceOf` / `address(this).balance`. Alternatively, subtract any "airdropped" surplus before comparing against the limit:

```solidity
// For LST assets, use a tracked deposit mapping instead of balanceOf
mapping(address asset => uint256 deposited) public trackedDeposits;
```

Increment `trackedDeposits[asset]` only inside `depositAsset` and decrement it only on authorised outflows (`transferAssetToNodeDelegator`, etc.), so that unsolicited transfers have no effect on the limit check.

### Proof of Concept

1. Suppose `depositLimitByAsset[stETH] = 100_000e18` and `getTotalAssetDeposits(stETH)` currently returns `99_999e18`.
2. Attacker calls `stETH.transfer(address(lrtDepositPool), 1e18 + 1)`.
3. `getTotalAssetDeposits(stETH)` now returns `100_000e18 + 1`.
4. Any user calling `depositAsset(stETH, any_amount, ...)` hits `_checkIfDepositAmountExceedesCurrentLimit` → `totalAssetDeposits + amount > depositLimit` → `revert MaximumDepositLimitReached()`.
5. The same attack applies to ETH: `(bool ok,) = address(lrtDepositPool).call{value: 1 wei}("")` when the ETH total already equals the limit causes `totalAssetDeposits > depositLimit` to be true, blocking `depositETH` for all users.

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
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

**File:** contracts/LRTConfig.sol (L123-133)
```text
    function updateAssetDepositLimit(
        address asset,
        uint256 depositLimit
    )
        external
        onlyRole(LRTConstants.MANAGER)
        onlySupportedAsset(asset)
    {
        depositLimitByAsset[asset] = depositLimit;
        emit AssetDepositLimitUpdate(asset, depositLimit);
    }
```
