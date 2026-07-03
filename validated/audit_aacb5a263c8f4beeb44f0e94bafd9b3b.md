### Title
Missing Minimum Output Slippage Guard on L2 Pool `deposit()` Functions - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary
All L2 pool `deposit()` functions accept ETH or LST tokens and mint/transfer rsETH to the caller, but none expose a `minRsETHAmount` parameter. The rsETH output is computed at execution time from a live oracle rate. If the oracle rate advances between the moment a user previews the swap and the moment the transaction is mined, the user silently receives fewer rsETH than expected with no on-chain protection.

### Finding Description
Every L2 pool contract exposes two public `deposit()` overloads — one for native ETH and one for ERC-20 tokens. In each case the output amount is derived entirely from the oracle rate at execution time:

`RSETHPoolV3.sol` ETH path:
```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
// ...
wrsETH.mint(msg.sender, rsETHAmount);
``` [1](#0-0) 

`viewSwapRsETHAmountAndFee` divides by the live oracle rate:
```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [2](#0-1) 

The same pattern is present in `RSETHPoolNoWrapper.sol`: [3](#0-2) 

`RSETHPool.sol`: [4](#0-3) 

and `RSETHPoolV3ExternalBridge.sol`: [5](#0-4) 

None of these functions accept a caller-supplied minimum output amount. There is no check of the form `if (rsETHAmount < minRsETHAmount) revert SlippageExceeded()`.

By contrast, the L1 `LRTDepositPool` already enforces this protection:
```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
``` [6](#0-5) 

The L1 deposit functions explicitly accept `minRSETHAmountExpected`: [7](#0-6) 

The L2 pool contracts also expose a `getMinAmount()` helper and `InvalidSlippageTolerance` / `InvalidMinAmount` errors, indicating the infrastructure for slippage control exists but was never wired into the deposit path: [8](#0-7) 

### Impact Explanation
**Low — Contract fails to deliver promised returns, but does not lose value.**

A user who calls `viewSwapRsETHAmountAndFee()` off-chain to preview their output and then submits a `deposit()` transaction may receive materially fewer rsETH tokens if the oracle rate is updated before their transaction is mined. Because rsETH accrues restaking yield, its rate against ETH increases over time and is updated periodically. A rate update that occurs in the same block or a few blocks before the user's transaction executes will silently reduce the user's rsETH output with no revert and no recourse. The deposited ETH/LST is not returned; the user simply receives less rsETH than the amount they agreed to when constructing the transaction.

### Likelihood Explanation
**Medium.** The rsETH oracle rate is updated regularly as restaking rewards accrue. On active L2 networks (Arbitrum, Optimism, etc.) with variable block times and mempool delays, the window between a user previewing a swap and the transaction being included is non-trivial. Any oracle update in that window silently degrades the user's output. No special attacker action is required — this is a normal operational condition.

### Recommendation
Add a `uint256 minRsETHAmount` parameter to all four `deposit()` overloads across `RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPool`, and `RSETHPoolV3ExternalBridge`. After computing `rsETHAmount`, revert if it falls below the caller-supplied minimum:

```solidity
if (rsETHAmount < minRsETHAmount) revert SlippageLimitExceeded();
```

This mirrors the protection already present in `LRTDepositPool._beforeDeposit()` on L1. [9](#0-8) 

### Proof of Concept
1. User calls `viewSwapRsETHAmountAndFee(1 ether)` on `RSETHPoolV3` and observes they will receive `X` rsETH at the current oracle rate `R`.
2. User submits `deposit{value: 1 ether}("ref")`.
3. Before the transaction is mined, the rsETH oracle updates its rate from `R` to `R'` where `R' > R` (rsETH appreciated).
4. The transaction executes: `rsETHAmount = 1e18 * 1e18 / R'`, which is strictly less than `X`.
5. `wrsETH.mint(msg.sender, rsETHAmount)` mints the reduced amount with no revert.
6. The user receives fewer rsETH than expected and has no on-chain mechanism to prevent this outcome.

The same sequence applies to the ERC-20 token `deposit()` overload, where both the rsETH oracle and the token oracle can shift between preview and execution. [10](#0-9)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L258-262)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
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

**File:** contracts/pools/RSETHPoolV3.sol (L304-307)
```text
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L237-243)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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
