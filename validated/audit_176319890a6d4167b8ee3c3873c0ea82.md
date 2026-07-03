### Title
Missing `minOutputAmount` Slippage Protection in L2 Pool Deposit Functions — (File: contracts/pools/RSETHPoolV3.sol)

### Summary

The L2 pool `deposit` functions lack a `minRsETHAmount` / `minOutputAmount` parameter, meaning users have no on-chain mechanism to enforce a minimum rsETH output. The mainnet `LRTDepositPool` correctly implements this guard, but the L2 variants omit it entirely, leaving depositors exposed to adverse oracle rate movements between transaction submission and execution.

---

### Finding Description

`LRTDepositPool.depositETH` and `LRTDepositPool.depositAsset` both accept a `minRSETHAmountExpected` argument and revert with `MinimumAmountToReceiveNotMet` if the computed mint amount falls below it: [1](#0-0) [2](#0-1) 

By contrast, every L2 pool `deposit` entry point omits this parameter entirely. For example, `RSETHPoolV3.deposit(string memory referralId)` and `RSETHPoolV3.deposit(address token, uint256 amount, string memory referralId)` compute `rsETHAmount` from the live oracle rate and immediately mint, with no floor check: [3](#0-2) [4](#0-3) 

The same omission is present in `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, and `RSETHPoolNoWrapper`: [5](#0-4) [6](#0-5) 

The oracle rate (`getRate()`) used in `viewSwapRsETHAmountAndFee` is a live, updateable value. When it increases (e.g., after a reward accrual update), the same ETH/token input yields fewer rsETH. A user who previewed the rate off-chain and submitted a transaction has no way to revert if the rate moves against them before their transaction is included.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A depositor on any L2 pool can receive materially fewer rsETH tokens than they observed when constructing the transaction. The deposited ETH/LST is not stolen — it is correctly accounted for — but the user's rsETH share is diluted relative to their expectation, with no recourse. This is the same slippage-bypass class as M-28: a user-specified output floor cannot be enforced because the parameter does not exist.

---

### Likelihood Explanation

The rsETH oracle rate is updated regularly as rewards accrue on mainnet. Any deposit transaction that is pending in the mempool during an oracle update is silently subject to a worse rate. No adversarial action is required; normal protocol operation is sufficient to trigger the condition. L2 mempools with public visibility make this straightforward to observe.

---

### Recommendation

Add a `uint256 minRsETHAmount` parameter to every L2 pool `deposit` function, mirroring the pattern already used in `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount) external payable ... {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert SlippageExceeded();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

Apply the same change to the token-deposit overload and to all pool variants (`RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, `RSETHPoolNoWrapper`).

---

### Proof of Concept

1. Alice calls `RSETHPoolV3.viewSwapRsETHAmountAndFee(1 ether)` off-chain and observes she will receive `X` wrsETH.
2. Alice submits `RSETHPoolV3.deposit{value: 1 ether}("ref")`.
3. Before Alice's transaction is mined, the oracle rate is updated (reward accrual), increasing `rsETHToETHrate`.
4. Alice's transaction executes: `rsETHAmount = 1e18 * amountAfterFee / rsETHToETHrate` is now smaller than `X`.
5. Alice receives fewer wrsETH than expected with no on-chain protection, because there is no `minRsETHAmount` check — unlike `LRTDepositPool` which would have reverted at line 667. [7](#0-6) [2](#0-1)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/pools/RSETHPoolV3.sol (L246-265)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L364-384)
```text
    /// @dev Swaps ETH for rsETH
    /// @param referralId The referral id
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
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L241-271)
```text
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
