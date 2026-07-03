### Title
No `minRsEthAmountToWithdraw` enforcement allows dust withdrawal requests to permanently clog the FIFO queue, temporarily freezing legitimate user withdrawals — (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`minRsEthAmountToWithdraw[asset]` is a per-asset mapping that defaults to `0` in Solidity and is never set in `initialize()`. The only guard in `initiateWithdrawal()` is `rsETHUnstaked == 0`, which means any amount ≥ 1 wei of rsETH is accepted. An unprivileged rsETH holder can flood the withdrawal queue with thousands of dust requests. Because `_unlockWithdrawalRequests()` processes requests strictly in FIFO order and cannot skip individual entries, legitimate users' requests queued after the dust entries are temporarily frozen until the operator drains all preceding dust requests at significant gas cost.

---

### Finding Description

`LRTWithdrawalManager` declares `minRsEthAmountToWithdraw` as a mapping but never initialises it:

```solidity
// LRTWithdrawalManager.sol:35
mapping(address asset => uint256) public minRsEthAmountToWithdraw;
```

The `initialize()` function sets only `withdrawalDelayBlocks` and `lrtConfig`; `minRsEthAmountToWithdraw` is left at the Solidity default of `0` for every asset. [1](#0-0) 

The guard in `initiateWithdrawal()` is:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
```

When `minRsEthAmountToWithdraw[asset] == 0`, the condition `rsETHUnstaked < 0` is always false, so the check collapses to `rsETHUnstaked == 0`. Any value ≥ 1 wei passes. [2](#0-1) 

`_unlockWithdrawalRequests()` iterates the queue strictly in ascending nonce order and cannot skip individual entries:

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    ...
    nextLockedNonce_++;
}
nextLockedNonce[asset] = nextLockedNonce_;
``` [3](#0-2) 

Any request at nonce `N` cannot be unlocked until all requests at nonces `0 … N-1` have been processed. Dust requests inserted before a legitimate request block it indefinitely until the operator drains them.

`setMinRsEthAmountToWithdraw` also accepts `0` as a valid value (no zero-check guard), so even if the admin sets a minimum it can be silently reset to `0`:

```solidity
function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
    minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
    ...
}
``` [4](#0-3) 

Additionally, `LRTDepositPool.minAmountToDeposit` is also never set in `initialize()`, defaulting to `0`, so the attacker can cheaply acquire rsETH in 1-wei increments to fund the attack: [5](#0-4) 

---

### Impact Explanation

**Temporary freezing of funds (Medium).**  
Legitimate users whose `initiateWithdrawal()` calls are queued after the attacker's dust requests cannot have their requests unlocked — and therefore cannot call `completeWithdrawal()` — until the operator processes every preceding dust entry. With EigenLayer's mandatory `withdrawalDelayBlocks` (~8 days) already in play, adding thousands of dust entries ahead of a legitimate request extends the effective freeze by however long it takes the operator to drain the queue.

**Unbounded gas consumption (Medium).**  
The operator (`ASSET_TRANSFER_ROLE` / `OPERATOR_ROLE`) must call `unlockQueue()` repeatedly to drain dust entries. Each call processes a bounded batch but the total number of operator transactions — and total ETH spent on gas — scales linearly with the number of dust requests the attacker creates. [6](#0-5) 

---

### Likelihood Explanation

Any address holding rsETH can execute this attack. rsETH is freely obtainable by depositing ETH or a supported LST into `LRTDepositPool.depositETH()` / `depositAsset()`. Because `minAmountToDeposit` also defaults to `0`, the attacker can acquire rsETH in arbitrarily small increments. The only cost to the attacker is gas for creating the dust requests and the temporary lock-up of their own rsETH (which they eventually recover). The cost asymmetry strongly favours the attacker: creating N dust requests costs the attacker N deposit + N `initiateWithdrawal` transactions, while forcing the operator to spend gas on N `unlockQueue` iterations and blocking all legitimate users queued behind those entries.

---

### Recommendation

1. **Enforce a non-zero minimum in `initialize()`**: Set a sensible default for `minRsEthAmountToWithdraw` for each supported asset during initialisation (e.g., equivalent to ~0.001 ETH worth of rsETH).
2. **Guard `setMinRsEthAmountToWithdraw` against zero**: Add `if (minRsEthAmountToWithdraw_ == 0) revert InvalidMinAmount();` analogous to the guard already present in `KernelVaultETH.setMinDeposit()`.
3. **Guard `setMinAmountToDeposit` against zero** in `LRTDepositPool` for the same reason.

---

### Proof of Concept

```
// Setup: minRsEthAmountToWithdraw[ETH] == 0 (default, never set)

1. Attacker calls LRTDepositPool.depositETH{value: 1 ether}(0, "")
   → receives ~1e18 rsETH (exact amount depends on exchange rate)

2. Attacker approves LRTWithdrawalManager to spend rsETH.

3. Attacker calls initiateWithdrawal(ETH_TOKEN, 1, "") 1,000,000 times
   (splitting 1e18 rsETH into 1e6 requests of 1 wei each).
   Each call passes the guard: 1 == 0 → false, 1 < 0 → false → no revert.
   Each call pushes a new entry into the FIFO queue at nonces 0 … 999,999.

4. Victim calls initiateWithdrawal(ETH_TOKEN, 1e18, "") → queued at nonce 1,000,000.

5. Operator calls unlockQueue(ETH_TOKEN, firstExcludedIndex=1_000_001, ...).
   _unlockWithdrawalRequests iterates from nextLockedNonce=0 to 1_000_001.
   Must process all 1,000,000 dust entries before reaching the victim's entry.
   → Victim's withdrawal is locked until the operator drains all dust entries
     across many expensive transactions.
   → Victim cannot call completeWithdrawal() until their request is unlocked.
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L90-98)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        withdrawalDelayBlocks = 8 days / 12 seconds;

        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L162-164)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L268-320)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
    {
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();

        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);

        // If Aave integration is enabled and asset is ETH, deposit to Aave
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }

        emit AssetUnlocked(asset, rsETHBurned, assetAmountUnlocked, params.rsETHPrice, params.assetPrice);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L330-333)
```text
    function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
        minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
        emit MinAmountToWithdrawUpdated(asset, minRsEthAmountToWithdraw_);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L790-815)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

            unlockedWithdrawalsCount[asset]++;

            unchecked {
                nextLockedNonce_++;
            }
        }
        nextLockedNonce[asset] = nextLockedNonce_;
```

**File:** contracts/LRTDepositPool.sol (L45-52)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        maxNodeDelegatorLimit = 10;
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```
