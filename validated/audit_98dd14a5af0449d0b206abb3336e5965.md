### Title
No Minimum Output (Slippage) Protection in RSETHPool `deposit()` Functions - (File: contracts/pools/RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3ExternalBridge.sol)

---

### Summary

All L2 RSETHPool `deposit()` variants accept ETH or ERC-20 tokens from unprivileged users and mint/transfer rsETH (wrsETH) based on a live oracle rate, but provide no `minRsETHAmountExpected` parameter. Users cannot bound the minimum rsETH they will receive, leaving them exposed to unfavorable rate changes between transaction submission and execution.

---

### Finding Description

Every public-facing `deposit()` function across the RSETHPool family computes the rsETH output at execution time using `getRate()` from the configured oracle, then immediately transfers or mints that amount to the caller — with no floor check:

**`RSETHPool.sol` (ETH path):**
```solidity
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);  // no min check
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

**`RSETHPool.sol` (token path):**
```solidity
function deposit(address token, uint256 amount, string memory referralId) external ... {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
    feeEarnedInToken[token] += fee;
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);  // no min check
}
```

The same pattern is present in `RSETHPoolNoWrapper.sol`, `RSETHPoolV2ExternalBridge.sol`, and `RSETHPoolV3ExternalBridge.sol`.

The rate computation is:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // RSETHPool / RSETHPoolNoWrapper
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;  // token path
```

If `rsETHToETHrate` increases (rsETH appreciates) between the moment the user signs the transaction and the moment it is mined, `rsETHAmount` decreases — and the user has no on-chain mechanism to revert.

**Contrast with `LRTDepositPool.sol`**, the L1 deposit pool in the same codebase, which explicitly accepts and enforces a `minRSETHAmountExpected` parameter:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
function depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, ...) external ...
```

and enforces it in `_beforeDeposit`:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

The L2 pool contracts have no equivalent guard.

---

### Impact Explanation

A user deposits ETH or a supported token and receives fewer rsETH/wrsETH than they anticipated at submission time. The deposited assets are not returned; the user is locked into the worse rate. This constitutes the contract failing to deliver the promised return without an outright loss of principal value.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The rsETH oracle rate is updated periodically by the protocol. On any L2 where block times are short and oracle updates are frequent, a user's pending deposit transaction can be executed after an oracle update in the same block or a subsequent block, silently reducing the rsETH output. No attacker action is required; the condition arises from normal protocol operation. Any depositor on any supported L2 chain is affected.

---

### Recommendation

Add a `minRsETHAmountExpected` parameter to all `deposit()` overloads in every RSETHPool variant, mirroring the pattern already used in `LRTDepositPool.sol`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

---

### Proof of Concept

1. User calls `RSETHPool.deposit{value: 1 ether}("ref")` when `rsETHToETHrate = 1.05e18`, expecting ≈ 0.952 wrsETH.
2. Before the transaction is mined, the oracle is updated to `rsETHToETHrate = 1.10e18`.
3. The transaction executes; `rsETHAmount = 1e18 * 1e18 / 1.10e18 ≈ 0.909 wrsETH`.
4. The user receives ~4.5% fewer wrsETH than expected with no ability to revert.
5. The 1 ETH remains in the pool; the user cannot recover the difference. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/pools/RSETHPool.sol (L265-278)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPool.sol (L284-305)
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
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-271)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev Swaps token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-301)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-412)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev Swaps supported token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
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

**File:** contracts/LRTDepositPool.sol (L648-669)
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
```
