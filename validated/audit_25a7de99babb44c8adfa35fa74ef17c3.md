### Title
Admin Can Change `rsETH` Address via `LRTConfig#setRSETH()` Mid-Operation, Permanently Freezing User Deposited Assets — (File: contracts/LRTConfig.sol)

---

### Summary

`LRTConfig.setRSETH()` allows the `DEFAULT_ADMIN_ROLE` to replace the rsETH token address at any time with no guards on existing token holders or pending withdrawal state. If called between a user's deposit and their withdrawal initiation, the user's old rsETH tokens become unrecognized by `LRTWithdrawalManager.initiateWithdrawal()`, permanently freezing their deposited assets in the protocol.

---

### Finding Description

`LRTConfig` stores the rsETH token address in a mutable state variable `rsETH` that is freely updatable by the admin: [1](#0-0) 

There are no checks on outstanding rsETH supply, pending withdrawal requests, or existing token holders before the swap takes effect.

This address is read **dynamically at call time** by `LRTWithdrawalManager.initiateWithdrawal()`: [2](#0-1) 

If the admin changes `rsETH` to `rsETHv2` (a legitimate migration), the function now attempts `safeTransferFrom(rsETHv2, msg.sender, ...)`. A user holding old rsETH tokens has zero rsETHv2, so the call reverts and they cannot initiate any withdrawal.

The same breakage affects `unlockQueue()`, which burns rsETH from the withdrawal manager's own balance: [3](#0-2) 

After the address change, `burnFrom` targets rsETHv2, but the withdrawal manager holds old rsETH tokens from users who already queued withdrawals. This causes `unlockQueue()` to revert, blocking the entire withdrawal queue for all users.

The root cause is structurally identical to the Hubble `InsuranceFund#syncDeps()` finding: a single privileged call atomically replaces a critical token address with no invariant check that the contract's existing obligations (user balances, queued withdrawals) remain satisfiable under the new address.

---

### Impact Explanation

**Permanent freezing of funds.** Users who deposited stETH, ETHx, or ETH and received old rsETH tokens cannot redeem their assets after `setRSETH()` is called. Their deposited assets remain locked across `LRTDepositPool` and `NodeDelegator` contracts with no on-chain recovery path absent further admin intervention. Users who had already queued withdrawals are additionally blocked because `unlockQueue()` itself reverts. [4](#0-3) 

---

### Likelihood Explanation

Low-to-medium. The admin must call `setRSETH()` during a legitimate migration scenario (e.g., deploying rsETHv2). The function carries no supply or pending-withdrawal guard, so any such migration executed while users hold old rsETH or have queued withdrawals triggers the freeze. The original Hubble M-15 report was accepted at Medium severity for the identical pattern in `syncDeps()`. [5](#0-4) 

---

### Recommendation

1. **Make `rsETH` immutable** after initialization, removing `setRSETH()` entirely.
2. If migration must be supported, add a supply-zero guard before the swap:

```solidity
function setRSETH(address rsETH_) external onlyRole(DEFAULT_ADMIN_ROLE) {
    UtilLib.checkNonZeroAddress(rsETH_);
    uint256 _supply = IRSETH(rsETH).totalSupply();
    rsETH = IRSETH(rsETH_);
    require(IRSETH(rsETH_).totalSupply() >= _supply, "rsETH supply mismatch");
    emit SetRSETH(rsETH_);
}
```

This mirrors the recommended fix from the original Hubble report: verify that the post-change state is at least as solvent as the pre-change state before committing.

---

### Proof of Concept

1. Alice deposits 100 stETH via `LRTDepositPool.depositAsset()` and receives 95 old rsETH tokens. [6](#0-5) 

2. Admin calls `LRTConfig.setRSETH(rsETHv2)`. [1](#0-0) 

3. Alice calls `LRTWithdrawalManager.initiateWithdrawal(stETH, 95e18, "")`.

4. The function executes:
   ```solidity
   IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), 95e18);
   // lrtConfig.rsETH() now returns rsETHv2
   // Alice holds 0 rsETHv2 → reverts
   ``` [2](#0-1) 

5. Alice's 100 stETH is permanently locked with no on-chain recovery path.

6. **Secondary impact**: Any user who queued a withdrawal before the address change also has their request frozen, because `unlockQueue()` calls `IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned)` — the withdrawal manager holds old rsETH, not rsETHv2, so this reverts and the entire queue is blocked. [3](#0-2)

### Citations

**File:** contracts/LRTConfig.sol (L27-27)
```text
    address public rsETH;
```

**File:** contracts/LRTConfig.sol (L215-219)
```text
    function setRSETH(address rsETH_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(rsETH_);
        rsETH = rsETH_;
        emit SetRSETH(rsETH_);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L305-305)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
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
