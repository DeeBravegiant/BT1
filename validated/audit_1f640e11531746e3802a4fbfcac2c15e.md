### Title
Missing Minimum rsETH Output Slippage Protection in L2 Pool Deposit Functions - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPool.sol)

---

### Summary

The `deposit()` functions across all L2 pool contracts lack a `minRSETHAmountExpected` slippage guard. A depositor has no way to enforce a minimum rsETH output, so any oracle rate update that occurs between transaction submission and execution silently reduces the rsETH they receive below their expectation. This is the direct analog of the reported `DutchAuction.bid()` issue: a user-facing swap function accepts an asset input but provides no bound on the output, leaving the user exposed to rate movement they cannot control.

---

### Finding Description

`LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` both accept a `minRSETHAmountExpected` parameter and revert if the computed mint amount falls below it: [1](#0-0) [2](#0-1) 

The L2 pool equivalents — `RSETHPoolV3.deposit()`, `RSETHPoolNoWrapper.deposit()`, `RSETHPoolV3ExternalBridge.deposit()`, and `RSETHPool.deposit()` — accept only `referralId` (and optionally `token`/`amount`) with no minimum-output parameter: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

The rsETH amount is computed at execution time from the live oracle rate: [9](#0-8) 

If `rsETHToETHrate` increases between the user's transaction submission and its on-chain execution (e.g., due to a scheduled oracle update), `rsETHAmount` decreases and the user receives fewer rsETH tokens than they observed in the pre-flight `viewSwapRsETHAmountAndFee()` call. The user has no mechanism to reject this outcome.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The user deposits ETH or an LST and receives fewer rsETH tokens than the rate they observed at submission time. Because the same oracle rate governs both minting and redemption, the ETH-denominated value of the received rsETH is preserved. However, the user cannot enforce the token quantity they expected, which breaks the implicit promise of the pre-flight quote and can cause downstream failures (e.g., the user needed a specific rsETH amount to meet a collateral threshold or to execute a subsequent on-chain action).

---

### Likelihood Explanation

**Medium.** The rsETH oracle rate is updated periodically by the protocol as staking rewards accrue. Any oracle update that lands in the same block or in the mempool ahead of a pending deposit transaction will silently reduce the depositor's output. This is a routine operational event, not an edge case. L2 chains with public mempools (Arbitrum, Optimism, Unichain) make the ordering observable and exploitable by any MEV searcher who can sandwich the oracle update with the user's deposit.

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to every public `deposit()` overload in `RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, and `RSETHPool`, mirroring the pattern already used in `LRTDepositPool._beforeDeposit()`:

```solidity
// Example fix for RSETHPoolV3.deposit(string)
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

Apply the same pattern to the token-deposit overloads and to the equivalent functions in the other three pool contracts.

---

### Proof of Concept

1. User calls `viewSwapRsETHAmountAndFee(1 ether)` on `RSETHPoolV3` and observes `rsETHAmount = 0.952e18` at oracle rate `1.05e18`.
2. User submits `deposit{value: 1 ether}("")`.
3. Before the transaction is included, the protocol updates the rsETH oracle rate to `1.06e18`.
4. At execution: `rsETHAmount = (1e18 - fee) * 1e18 / 1.06e18 ≈ 0.943e18`.
5. User receives `0.943e18` wrsETH — `~0.009e18` fewer than the quoted amount — with no revert and no recourse.
6. The same scenario applies to `RSETHPoolNoWrapper.deposit()`, `RSETHPoolV3ExternalBridge.deposit()`, and `RSETHPool.deposit()` across all deployed L2 chains. [10](#0-9) [11](#0-10) [12](#0-11) [13](#0-12)

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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
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
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L250-271)
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

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-384)
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
```

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
