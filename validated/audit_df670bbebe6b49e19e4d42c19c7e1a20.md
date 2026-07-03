### Title
Dust Withdrawal Request Spam Causes Unbounded Gas in `_unlockWithdrawalRequests` and Temporary Freezing of Legitimate User Funds - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`minRsEthAmountToWithdraw` defaults to `0` for every asset, meaning any non-zero rsETH amount (including 1 wei) passes the only guard in `initiateWithdrawal`. There is no per-user cap on the number of queued requests. An attacker can flood the FIFO withdrawal queue with thousands of dust requests, forcing the operator's `unlockQueue` → `_unlockWithdrawalRequests` while-loop to iterate over all attacker entries before reaching legitimate users' requests, causing unbounded gas consumption and temporary freezing of legitimate withdrawals.

### Finding Description

`initiateWithdrawal` enforces a single amount check:

```solidity
if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
    revert InvalidAmountToWithdraw();
}
``` [1](#0-0) 

`minRsEthAmountToWithdraw` is a plain mapping that is never initialized in `initialize()`:

```solidity
mapping(address asset => uint256) public minRsEthAmountToWithdraw;
``` [2](#0-1) 

Its Solidity default is `0`, so the condition collapses to `rsETHUnstaked == 0`, accepting any amount ≥ 1 wei. There is no per-address limit on how many requests can be queued.

Each accepted request is appended to a global FIFO queue via `_addUserWithdrawalRequest`:

```solidity
userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
``` [3](#0-2) 

When the operator calls `unlockQueue`, it internally calls `_unlockWithdrawalRequests`, which contains an unbounded while-loop that must traverse every entry from `nextLockedNonce` up to the caller-supplied `firstExcludedIndex`:

```solidity
while (nextLockedNonce_ < firstExcludedIndex) {
    bytes32 requestId = getRequestId(asset, nextLockedNonce_);
    WithdrawalRequest storage request = withdrawalRequests[requestId];
    if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
    uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);
    if (availableAssetAmount < payoutAmount) break;
    ...
    unchecked { nextLockedNonce_++; }
}
``` [4](#0-3) 

Dust requests (1 wei rsETH → ~0 asset payout) will never trigger the `availableAssetAmount < payoutAmount` break, so the loop processes every attacker entry before reaching legitimate users' requests. With enough spam entries, a single `unlockQueue` call exceeds the block gas limit, and even batched calls must exhaust all attacker nonces before any legitimate nonce is unlocked.

### Impact Explanation

**Medium — Temporary freezing of funds and unbounded gas consumption.**

Legitimate users who called `initiateWithdrawal` after the attacker cannot have their requests unlocked until all preceding attacker nonces are processed. Because `nextLockedNonce` advances strictly in order, there is no way to skip attacker entries. Users' funds (rsETH already transferred to the contract at line 166) are locked for an indefinite period proportional to the number of spam entries. [5](#0-4) 

### Likelihood Explanation

**Medium.** The attacker only needs to hold rsETH (obtainable by depositing into the protocol). With 1 wei per request, the economic cost is negligible gas fees. No privileged role is required; `initiateWithdrawal` is a public, permissionless function. The default `minRsEthAmountToWithdraw = 0` is the live on-chain state unless the admin has explicitly called `setMinRsEthAmountToWithdraw` for every supported asset. [6](#0-5) 

### Recommendation

1. **Set a meaningful non-zero `minRsEthAmountToWithdraw` for every supported asset** in `initialize()` (or enforce it cannot remain zero via a require in `initiateWithdrawal`).
2. **Add a per-user cap** on the number of simultaneously pending withdrawal requests per asset (e.g., `require(userAssociatedNonces[asset][msg.sender].length() < MAX_REQUESTS_PER_USER)`).
3. **Add a `maxIterations` parameter** to `_unlockWithdrawalRequests` so the operator can bound gas per call without being forced to process attacker entries first.

### Proof of Concept

1. Attacker acquires N rsETH (e.g., N = 10 000 wei, one per call).
2. Attacker calls `initiateWithdrawal(asset, 1, "")` 10 000 times. Each call passes the `rsETHUnstaked == 0` check because `minRsEthAmountToWithdraw[asset] == 0`.
3. Legitimate user calls `initiateWithdrawal(asset, largeAmount, "")`. Their request is assigned nonce 10 000.
4. Operator calls `unlockQueue(asset, 10001, ...)`. `_unlockWithdrawalRequests` loops from nonce 0 to 10 000, processing all attacker dust entries. Each iteration reads storage (`withdrawalRequests[requestId]`), performs arithmetic, and writes back — consuming ~5 000–10 000 gas per iteration → 50–100 M gas total, exceeding the block gas limit.
5. Even if the operator batches calls (e.g., 500 entries at a time), the legitimate user's request at nonce 10 000 cannot be unlocked until all 10 000 attacker nonces are processed, delaying their withdrawal by many operator transactions. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L35-35)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
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

**File:** contracts/LRTWithdrawalManager.sol (L330-332)
```text
    function setMinRsEthAmountToWithdraw(address asset, uint256 minRsEthAmountToWithdraw_) external onlyLRTAdmin {
        minRsEthAmountToWithdraw[asset] = minRsEthAmountToWithdraw_;
        emit MinAmountToWithdrawUpdated(asset, minRsEthAmountToWithdraw_);
```

**File:** contracts/LRTWithdrawalManager.sol (L756-757)
```text
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;
```

**File:** contracts/LRTWithdrawalManager.sol (L790-814)
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
```
