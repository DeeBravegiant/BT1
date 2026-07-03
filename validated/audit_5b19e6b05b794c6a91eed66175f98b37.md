### Title
Pause Bypass in RSETHPoolV3 — Fund-Moving Functions Unguarded by `whenNotPaused` - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

`RSETHPoolV3` implements a custom `paused` boolean and `whenNotPaused` modifier to halt deposits during emergencies. However, multiple fund-moving functions that transfer ETH and tokens out of the pool are not guarded by `whenNotPaused`, allowing `BRIDGER_ROLE` and `OPERATOR_ROLE` holders to drain pool assets even while the contract is paused.

---

### Finding Description

The `deposit()` functions are correctly guarded: [1](#0-0) [2](#0-1) 

The `whenNotPaused` modifier is defined as: [3](#0-2) 

However, the following fund-moving functions are **not** guarded by `whenNotPaused`:

**`moveAssetsForBridging(uint256 amount)`** — transfers ETH out of the pool to the bridger: [4](#0-3) 

**`moveAssetsForBridging(address token, uint256 amount)`** — transfers ERC20 tokens out of the pool: [5](#0-4) 

**`withdrawFees(address receiver)`** — transfers accumulated ETH fees out: [6](#0-5) 

**`withdrawFees(address receiver, address token)`** — transfers accumulated token fees out: [7](#0-6) 

**`swapAssetToPremintedRsETH(...)`** — transfers ETH or tokens out of the pool to the operator in exchange for rsETH: [8](#0-7) 

None of these functions include `whenNotPaused`. The pause flag only blocks new deposits (minting of wrsETH), while all outbound fund flows remain fully operational.

---

### Impact Explanation

When the contract is paused due to an emergency (e.g., oracle anomaly, exploit detection, or price manipulation), the `BRIDGER_ROLE` can still call `moveAssetsForBridging()` to drain all ETH and token balances from the pool, and `withdrawFees()` to drain accumulated fee balances. The `OPERATOR_ROLE` can still call `swapAssetToPremintedRsETH()` to extract ETH or tokens in exchange for rsETH.

This means the pause does not protect the pool's liquidity. Users who deposited ETH/LSTs into the pool and received wrsETH may find the pool empty when the pause is lifted, preventing reverse swaps and making protocol recovery difficult or impossible. This constitutes **temporary freezing of user funds** (Medium impact).

---

### Likelihood Explanation

`BRIDGER_ROLE` and `OPERATOR_ROLE` are active operational roles expected to call these functions regularly during normal protocol operation. During an emergency pause, these roles may continue operating without awareness that the pause is active, or may be instructed to continue bridging operations. No malicious intent is required — the design flaw is that the pause does not propagate to outbound fund flows.

---

### Recommendation

Add the `whenNotPaused` modifier to all fund-moving functions in `RSETHPoolV3`:

```solidity
function moveAssetsForBridging(uint256 amount)
    external nonReentrant whenNotPaused onlyRole(BRIDGER_ROLE) { ... }

function moveAssetsForBridging(address token, uint256 amount)
    external nonReentrant onlySupportedToken(token) whenNotPaused onlyRole(BRIDGER_ROLE) { ... }

function withdrawFees(address receiver)
    external nonReentrant whenNotPaused onlyRole(BRIDGER_ROLE) { ... }

function withdrawFees(address receiver, address token)
    external nonReentrant onlySupportedToken(token) whenNotPaused onlyRole(BRIDGER_ROLE) { ... }

function swapAssetToPremintedRsETH(address rsETH, address token, uint256 rsETHAmount)
    external nonReentrant onlySupportedTokenOrEth(token) whenNotPaused onlyRole(OPERATOR_ROLE) { ... }
```

---

### Proof of Concept

1. An emergency is detected; the `PAUSER_ROLE` calls `pause()` on `RSETHPoolV3`.
2. `paused` is set to `true`; `deposit()` now reverts for all callers.
3. `BRIDGER_ROLE` calls `moveAssetsForBridging(address(this).balance - feeEarnedInETH)` — succeeds, draining all non-fee ETH from the pool.
4. `BRIDGER_ROLE` calls `withdrawFees(receiver)` — succeeds, draining all accumulated ETH fees.
5. `BRIDGER_ROLE` calls `moveAssetsForBridging(token, tokenBalanceMinusFees)` — succeeds, draining all token balances.
6. Pool is now empty. When the pause is lifted, users holding wrsETH cannot perform reverse swaps (`swapAssetToPremintedRsETH`) because the pool has no liquidity, effectively freezing their ability to redeem underlying assets.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L71-74)
```text
    modifier whenNotPaused() {
        if (paused) revert ContractPaused();
        _;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L246-252)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-279)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
```

**File:** contracts/pools/RSETHPoolV3.sol (L414-450)
```text
    function swapAssetToPremintedRsETH(
        address rsETH,
        address token,
        uint256 rsETHAmount
    )
        external
        nonReentrant
        onlySupportedTokenOrEth(token)
        onlyRole(OPERATOR_ROLE)
    {
        UtilLib.checkNonZeroAddress(rsETH);

        IRsETHTokenWrapper wrapper = IRsETHTokenWrapper(address(wrsETH));
        IERC20 tokenContract = IERC20(token);

        if (!wrapper.allowedTokens(rsETH)) revert TokenNotAllowedInWrapper();
        if (rsETHAmount == 0) revert InvalidAmount();
        if (rsETHAmount > wrapper.maxAmountToDepositBridgerAsset(rsETH)) revert ExceedsMaxAmountToDepositInWrapper();

        // Get the amount of token to transfer to the user for the given amount of rsETH provided
        uint256 tokenAmount = viewSwapAssetToPremintedRsETH(token, rsETHAmount);

        // Transfer rsETH from sender to the wrapper
        IERC20(rsETH).safeTransferFrom(msg.sender, address(wrapper), rsETHAmount);

        // Transfer the token from the pool to the sender
        if (token == ETH_IDENTIFIER) {
            if (getETHBalanceMinusFees() < tokenAmount) revert InsufficientETHBalanceForReverseSwap();
            (bool success,) = payable(msg.sender).call{ value: tokenAmount }("");
            if (!success) revert TransferFailed();
        } else {
            if (getTokenBalanceMinusFees(token) < tokenAmount) revert InsufficientAssetBalanceForReverseSwap();
            tokenContract.safeTransfer(msg.sender, tokenAmount);
        }

        emit ReverseSwapOccurred(msg.sender, rsETH, token, rsETHAmount, tokenAmount);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L453-461)
```text
    function withdrawFees(address receiver) external nonReentrant onlyRole(BRIDGER_ROLE) {
        // withdraw fees in ETH
        uint256 amountToSendInETH = feeEarnedInETH;
        feeEarnedInETH = 0;
        (bool success,) = payable(receiver).call{ value: amountToSendInETH }("");
        if (!success) revert TransferFailed();

        emit FeesWithdrawn(amountToSendInETH);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L464-479)
```text
    function withdrawFees(
        address receiver,
        address token
    )
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        // withdraw fees in token
        uint256 amountToSendInToken = feeEarnedInToken[token];
        feeEarnedInToken[token] = 0;
        IERC20(token).safeTransfer(receiver, amountToSendInToken);

        emit FeesWithdrawn(amountToSendInToken, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L482-493)
```text
    function moveAssetsForBridging(uint256 amount) external nonReentrant onlyRole(BRIDGER_ROLE) {
        if (amount == 0) revert InvalidAmount();

        // withdraw up to ETH - fees
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
        if (amount > ethBalanceMinusFees) revert InsufficientBalanceInPool();

        (bool success,) = msg.sender.call{ value: amount }("");
        if (!success) revert TransferFailed();

        emit AssetsMovedForBridging(amount);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L496-514)
```text
    function moveAssetsForBridging(
        address token,
        uint256 amount
    )
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        if (amount == 0) revert InvalidAmount();

        // withdraw up to token - fees
        uint256 tokenBalanceMinusFees = getTokenBalanceMinusFees(token);
        if (amount > tokenBalanceMinusFees) revert InsufficientBalanceInPool();

        IERC20(token).safeTransfer(msg.sender, amount);

        emit AssetsMovedForBridging(amount, token);
    }
```
