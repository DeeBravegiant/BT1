### Title
Direct rsETH Transfer to LRTWithdrawalManager Permanently Freezes User Funds - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary
Any rsETH holder can call `rsETH.transfer(address(lrtWithdrawalManager), amount)` directly, bypassing `initiateWithdrawal`. Because no `WithdrawalRequest` entry is created, the deposited rsETH is never included in `unlockQueue`'s burn accounting and has no on-chain recovery path — permanently freezing the sender's funds.

---

### Finding Description

The intended withdrawal entry point is `LRTWithdrawalManager.initiateWithdrawal`, which:
1. Pulls rsETH from the user via `safeTransferFrom`
2. Creates a `WithdrawalRequest` struct
3. Records the nonce in `userAssociatedNonces` [1](#0-0) 

The `unlockQueue` operator function later burns only the rsETH that corresponds to queued requests, accumulating `rsETHAmountToBurn += request.rsETHUnstaked` per entry, then calling `IRSETH.burnFrom(address(this), rsETHBurned)`. [2](#0-1) [3](#0-2) 

`RSETH._transfer` only enforces `_enforceNotBlocked(from)` and `_enforceNotBlocked(to)` — there is no restriction on the recipient being `LRTWithdrawalManager`. [4](#0-3) 

A user can therefore call `rsETH.transfer(withdrawalManagerAddress, amount)` directly. The rsETH lands in `LRTWithdrawalManager` with no `WithdrawalRequest` and no nonce in `userAssociatedNonces`. `unlockQueue` will never include it in `rsETHAmountToBurn`, so it is never burned.

No existing function recovers it:
- `sweepRemainingAssets` handles only LST/ETH assets, not rsETH. [5](#0-4) 

- `RSETH.recoverFrozenFunds` requires the target address to be actively blocked, which would simultaneously block all normal withdrawal operations. [6](#0-5) 

- There is no `recoverERC20` or equivalent in `LRTWithdrawalManager`.

---

### Impact Explanation

The directly transferred rsETH is permanently frozen inside `LRTWithdrawalManager` with no on-chain recovery path short of a protocol upgrade — exactly mirroring the original report's conclusion: *"the locked shares can only be retrieved by a protocol update."*

Because the attacker merely relocates their own tokens (total supply is unchanged), the rsETH price formula `newRsETHPrice = totalETHInProtocol / rsethSupply` is unaffected, and other users' positions are not harmed. [7](#0-6) 

Impact: **Permanent freezing of the sender's own funds** (no recovery without upgrade).

---

### Likelihood Explanation

Low. The user must call `rsETH.transfer(withdrawalManagerAddress, amount)` instead of `initiateWithdrawal`. This is plausible as an accidental mistake (copy-paste error, wallet UI confusion, or direct contract interaction). No special permissions are required — any rsETH holder can trigger this.

---

### Recommendation

1. In `RSETH._transfer`, revert if `to == lrtConfig.withdrawManager()` and `msg.sender != lrtConfig.withdrawManager()`, preventing direct user transfers to the withdrawal manager.
2. Alternatively (or additionally), add a manager-restricted `recoverERC20(address token, address to, uint256 amount)` function to `LRTWithdrawalManager` so accidentally sent rsETH can be recovered without a full protocol upgrade.

---

### Proof of Concept

1. User holds 10 rsETH.
2. User calls `rsETH.transfer(address(lrtWithdrawalManager), 10e18)` directly. `RSETH._transfer` passes — neither address is blocked.
3. `LRTWithdrawalManager` now holds 10 rsETH; `userAssociatedNonces[asset][user]` is empty.
4. Operator calls `unlockQueue` — `rsETHAmountToBurn` is 0 for this user (no queued requests), so the 10 rsETH is not burned.
5. User calls `completeWithdrawal` — reverts with `NoWithdrawalRequests` because `userAssociatedNonces` is empty. [8](#0-7) 

6. The 10 rsETH is permanently stuck in `LRTWithdrawalManager` with no on-chain recovery path.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-176)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L305-305)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

**File:** contracts/LRTWithdrawalManager.sol (L395-413)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
```

**File:** contracts/LRTWithdrawalManager.sol (L700-702)
```text
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L805-806)
```text
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
```

**File:** contracts/RSETH.sol (L206-219)
```text
    function recoverFrozenFunds(address from) external onlyLRTAdmin {
        UtilLib.checkNonZeroAddress(from);
        UtilLib.checkNonZeroAddress(custodyAddress);

        if (isPermanentlyExempt[from]) revert AddressPermanentlyExempt(from);

        uint256 blockedUntil = transfersBlockedUntil[from];
        if (blockedUntil == 0 || block.timestamp >= blockedUntil) revert NoActiveTransferBlock(from);

        uint256 accountBalance = balanceOf(from);

        // Bypass transfer block enforcement when transferring to custody address
        super._transfer(from, custodyAddress, accountBalance);
        emit FrozenFundsRecovered(from, custodyAddress, accountBalance);
```

**File:** contracts/RSETH.sol (L287-291)
```text
    function _transfer(address from, address to, uint256 amount) internal override {
        _enforceNotBlocked(from);
        _enforceNotBlocked(to);
        super._transfer(from, to, amount);
    }
```

**File:** contracts/LRTOracle.sol (L216-250)
```text
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```
