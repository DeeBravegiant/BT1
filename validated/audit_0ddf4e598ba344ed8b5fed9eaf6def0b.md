### Title
Missing Minimum Output Slippage Guard in L2 Pool `deposit()` Functions Allows Users to Receive Fewer rsETH Than Previewed - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

All L2 pool `deposit()` functions accept ETH or ERC-20 tokens and mint rsETH based on a live oracle rate, but provide no `minRSETHAmountExpected` parameter. The mainnet `LRTDepositPool` enforces such a guard; the L2 pools do not. A user who previews their expected output via `viewSwapRsETHAmountAndFee()` and then submits a deposit has no on-chain protection if the oracle rate is updated before their transaction is mined, causing them to receive fewer rsETH tokens than the amount they agreed to.

---

### Finding Description

`LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` both accept a `minRSETHAmountExpected` argument and enforce it inside `_beforeDeposit`:

```solidity
// LRTDepositPool.sol L667
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

None of the L2 pool deposit entry-points carry an equivalent guard:

| Contract | Function | minOut guard? |
|---|---|---|
| `RSETHPoolV3` | `deposit(string)` | ✗ |
| `RSETHPoolV3` | `deposit(address,uint256,string)` | ✗ |
| `RSETHPool` | `deposit(string)` | ✗ |
| `RSETHPool` | `deposit(address,uint256,string)` | ✗ |
| `RSETHPoolNoWrapper` | `deposit(string)` | ✗ |
| `RSETHPoolNoWrapper` | `deposit(address,uint256,string)` | ✗ |
| `RSETHPoolV3ExternalBridge` | `deposit(string)` | ✗ |
| `RSETHPoolV3ExternalBridge` | `deposit(address,uint256,string)` | ✗ |

The rsETH amount is computed at execution time from the live oracle rate:

```solidity
// RSETHPoolV3.sol L258-262
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

```solidity
// RSETHPoolV3.sol L299-307
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The oracle rate (`rsETHOracle.getRate()`) is a mutable value that can be updated by the oracle operator between the moment a user calls `viewSwapRsETHAmountAndFee()` and the moment their `deposit()` transaction is included in a block. Because the deposit function never checks the resulting `rsETHAmount` against any caller-supplied floor, the user silently receives fewer tokens than the amount they previewed and accepted off-chain.

---

### Impact Explanation

**Low.** The depositor does not lose their principal (ETH or LST is held by the pool and bridged to L1), but the contract fails to deliver the rsETH amount the user was shown and agreed to. This is the on-chain analog of the external report's finding: the user cannot enforce what they will receive when interacting with the protocol. The discrepancy grows proportionally with the magnitude of the oracle rate update and the deposit size.

---

### Likelihood Explanation

**Medium.** The rsETH/ETH oracle rate is updated periodically by the oracle operator (and can be updated by anyone via `LRTOracle.updateRSETHPrice()` on mainnet). On L2s with high mempool latency or during periods of network congestion, a deposit transaction can remain pending long enough for one or more oracle updates to occur. This is a routine operational condition, not an exotic edge case. The mainnet contract's explicit `minRSETHAmountExpected` guard demonstrates that the protocol designers recognized this risk for the L1 path but omitted the protection on all L2 paths.

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to every L2 pool `deposit()` overload and revert if the computed `rsETHAmount` falls below it, mirroring the pattern already used in `LRTDepositPool._beforeDeposit()`:

```solidity
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

Apply the same change to the token-deposit overload and to `RSETHPool`, `RSETHPoolNoWrapper`, and `RSETHPoolV3ExternalBridge`.

---

### Proof of Concept

1. User calls `RSETHPoolV3.viewSwapRsETHAmountAndFee(1 ether)` at oracle rate `R₀ = 1.05 ETH/rsETH` → preview shows `≈ 0.952 rsETH`.
2. User submits `deposit{value: 1 ether}("ref")`.
3. Before the transaction is mined, the oracle operator calls `setRSETHOracle` or the underlying oracle updates to `R₁ = 1.10 ETH/rsETH`.
4. `deposit()` executes: `rsETHAmount = 1e18 * 1e18 / 1.10e18 ≈ 0.909 rsETH` — roughly 4.5% less than previewed.
5. No revert occurs; the user receives `0.909 rsETH` with no on-chain recourse.

Contrast: on mainnet, `LRTDepositPool.depositETH(minRSETHAmountExpected = 0.95e18, ...)` would revert at step 4 with `MinimumAmountToReceiveNotMet`, protecting the depositor. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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
