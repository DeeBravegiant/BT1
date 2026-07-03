### Title
ETH Donation to `LRTDepositPool` Inflates `address(this).balance`, Blocking All New ETH Deposits via Deposit Limit DOS - (File: contracts/LRTDepositPool.sol)

---

### Summary

An unprivileged attacker can send ETH directly to `LRTDepositPool` via its open `receive()` function. Because `getETHDistributionData()` uses `address(this).balance` as the canonical measure of ETH held in the pool, the donated ETH inflates `getTotalAssetDeposits(ETH_TOKEN)`. If the inflated total exceeds the configured deposit limit, every subsequent call to `depositETH()` reverts with `MaximumDepositLimitReached`, permanently blocking new ETH deposits until an admin manually raises the limit.

---

### Finding Description

`LRTDepositPool` exposes an unrestricted ETH receiver:

```solidity
receive() external payable { }
``` [1](#0-0) 

`getETHDistributionData()` reads `address(this).balance` directly as the pool's ETH balance:

```solidity
ethLyingInDepositPool = address(this).balance;
``` [2](#0-1) 

This value propagates through `getTotalAssetDeposits(ETH_TOKEN)` → `_checkIfDepositAmountExceedesCurrentLimit`:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
}
``` [3](#0-2) 

Because the ETH check uses `>` (not `+ amount >`), a single wei above the limit blocks **all** ETH deposits regardless of size. `depositETH()` calls `_beforeDeposit`, which calls `_checkIfDepositAmountExceedesCurrentLimit` and reverts with `MaximumDepositLimitReached`:

```solidity
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [4](#0-3) 

Critically, the donated ETH cannot be silently removed from the protocol's accounting. Moving it to a `NodeDelegator` keeps it in `ethLyingInNDCs`; moving it to `LRTUnstakingVault` keeps it in `ethLyingInUnstakingVault`. Both are summed in `getTotalAssetDeposits`:

```solidity
ethLyingInNDCs += nodeDelegatorQueue[i].balance;
...
ethLyingInUnstakingVault = lrtUnstakingVault.balance;
``` [5](#0-4) 

The only remediation available to the admin is to raise `depositLimitByAsset(ETH_TOKEN)` in `LRTConfig`.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but does not lose value.**

All new ETH deposits via `depositETH()` are blocked for as long as the donated ETH keeps `totalAssetDeposits` above the limit. Existing user funds are not frozen; withdrawals remain unaffected. No ETH is stolen. The attacker sacrifices the donated ETH (it enters the protocol permanently), and the admin must respond by increasing the deposit cap.

---

### Likelihood Explanation

**Medium.** The attack requires no special role, no front-running, and no external dependency. Any address can call `LRTDepositPool.receive()` with a small ETH amount. The attack is practical whenever `getTotalAssetDeposits(ETH_TOKEN)` is within a small margin of `depositLimitByAsset(ETH_TOKEN)` — a common operational state as the protocol approaches its cap. The attacker's only cost is the donated ETH.

---

### Recommendation

Replace the raw `address(this).balance` read in `getETHDistributionData()` with an internal accounting variable (e.g., `totalETHDeposited`) that is incremented only on legitimate `depositETH()` calls and decremented on transfers out. This mirrors the pattern already used for ERC-20 assets (`IERC20(asset).balanceOf(address(this))` is similarly vulnerable for tokens, but ETH is the most easily donated asset).

---

### Proof of Concept

1. Observe that `getTotalAssetDeposits(LRTConstants.ETH_TOKEN)` returns a value `D` close to `lrtConfig.depositLimitByAsset(ETH_TOKEN)` = `L`.
2. Attacker sends `L - D + 1 wei` directly to `LRTDepositPool` (no function call needed; `receive()` accepts it).
3. `address(this).balance` increases by `L - D + 1 wei`.
4. `getETHDistributionData()` now returns `ethLyingInDepositPool = D + (L - D + 1) = L + 1`.
5. `getTotalAssetDeposits(ETH_TOKEN)` returns `L + 1 > L`.
6. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, any_amount)` returns `true`.
7. Every call to `depositETH(...)` reverts with `MaximumDepositLimitReached`.
8. The donated ETH cannot be purged from the accounting without admin intervention to raise the deposit limit. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
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
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L485-496)
```text
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

**File:** contracts/LRTDepositPool.sol (L678-680)
```text
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
```
