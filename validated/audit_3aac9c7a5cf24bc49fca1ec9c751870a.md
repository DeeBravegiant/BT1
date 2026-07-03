### Title
No Minimum rsETH Output Guard on L2 Pool Deposits Leaves Users Without Slippage Protection - (File: `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

---

### Summary

All L2 deposit pool contracts (`RSETHPoolV3ExternalBridge`, `RSETHPoolV3`, `RSETHPool`, `RSETHPoolNoWrapper`) expose `deposit()` functions that compute the rsETH/wrsETH output amount solely from a live oracle rate at execution time, with no caller-supplied minimum output guard. The L1 counterpart `LRTDepositPool` already implements this protection via `minRSETHAmountExpected`. The L2 pools are missing the equivalent check, leaving depositors unable to bound the exchange rate they accept.

---

### Finding Description

Every L2 pool computes the rsETH amount to mint/transfer using `viewSwapRsETHAmountAndFee`, which reads `getRate()` from the on-chain oracle at the moment of execution:

```solidity
// RSETHPoolV3ExternalBridge.sol – deposit(string)
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount); // rate read here
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);                                   // no floor check
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
``` [1](#0-0) 

The same pattern appears in the token-deposit overload: [2](#0-1) 

And identically in `RSETHPoolV3`: [3](#0-2) 

And in `RSETHPool`: [4](#0-3) 

And in `RSETHPoolNoWrapper`: [5](#0-4) 

The rate formula is:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate
``` [6](#0-5) 

As `rsETHToETHrate` increases (rsETH appreciates over time as staking rewards accrue), the same ETH input yields fewer rsETH/wrsETH tokens. Because no floor is enforced, a user who observed a favourable rate off-chain when constructing the transaction silently receives fewer tokens if the rate moves before the transaction is included.

**Contrast with L1:** `LRTDepositPool.depositETH` and `depositAsset` both accept `minRSETHAmountExpected` and enforce it inside `_beforeDeposit`:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
``` [7](#0-6) 

The L2 pools have no equivalent check.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A depositor who previewed `viewSwapRsETHAmountAndFee` off-chain and submitted a transaction expecting `X` wrsETH may receive `X - Δ` wrsETH if the oracle rate ticks upward between preview and execution. The depositor's ETH is fully consumed; they cannot recover the shortfall. The protocol itself does not lose funds, but the user receives fewer liquid restaking tokens than the rate they agreed to.

---

### Likelihood Explanation

**Low.** The rsETH oracle rate (`rsETHToETHrate`) increases monotonically and slowly as EigenLayer staking rewards accrue. Rapid manipulation of this rate requires compromising the oracle operator, which is excluded. However, the absence of any floor means the risk is always present for every deposit, and the impact scales with deposit size and the time a transaction spends pending in the mempool (e.g., during periods of network congestion or low gas pricing by the user).

---

### Recommendation

Add a `minRsETHAmountExpected` parameter to all `deposit()` overloads in the L2 pool contracts and revert if the computed output falls below it, mirroring the existing guard in `LRTDepositPool._beforeDeposit`. For example in `RSETHPoolV3ExternalBridge`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

Apply the same change to `RSETHPoolV3`, `RSETHPool`, and `RSETHPoolNoWrapper`.

---

### Proof of Concept

1. Alice calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain and sees she will receive `980e15` wrsETH at the current oracle rate.
2. Alice submits `deposit{value: 1 ether}("ref")` to `RSETHPoolV3ExternalBridge`.
3. Before Alice's transaction is mined, the oracle rate ticks upward (e.g., a reward distribution updates the rsETH price).
4. `viewSwapRsETHAmountAndFee` is re-evaluated at execution time with the new, higher rate, yielding `975e15` wrsETH.
5. `wrsETH.mint(msg.sender, 975e15)` executes with no revert — Alice receives `5e15` fewer wrsETH than she expected, with no recourse.

The affected entry points are:
- `RSETHPoolV3ExternalBridge.deposit(string)` [1](#0-0) 
- `RSETHPoolV3ExternalBridge.deposit(address,uint256,string)` [2](#0-1) 
- `RSETHPoolV3.deposit(string)` [3](#0-2) 
- `RSETHPoolV3.deposit(address,uint256,string)` [8](#0-7) 
- `RSETHPool.deposit(string)` [4](#0-3) 
- `RSETHPool.deposit(address,uint256,string)` [9](#0-8) 
- `RSETHPoolNoWrapper.deposit(string)` [5](#0-4) 
- `RSETHPoolNoWrapper.deposit(address,uint256,string)` [10](#0-9)

### Citations

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L390-412)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
