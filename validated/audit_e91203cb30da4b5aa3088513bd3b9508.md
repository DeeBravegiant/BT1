### Title
Missing Slippage Protection in L2 Pool `deposit()` Functions - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol)

### Summary
All L2 pool `deposit()` functions accept user ETH or LST tokens and mint rsETH/wrsETH based on a live oracle rate, but provide no `minRSETHAmountExpected` parameter. Users have no way to enforce a minimum output, so any oracle rate movement between transaction submission and execution silently reduces the rsETH they receive with no revert path.

### Finding Description
The L1 `LRTDepositPool` correctly exposes a `minRSETHAmountExpected` parameter and enforces it inside `_beforeDeposit`:

```solidity
// LRTDepositPool.sol L648-669
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

The L2 pool equivalents — `RSETHPoolV3.deposit(string)`, `RSETHPoolV3.deposit(address,uint256,string)`, `RSETHPool.deposit(string)`, `RSETHPool.deposit(address,uint256,string)`, `RSETHPoolNoWrapper.deposit(string)`, `RSETHPoolNoWrapper.deposit(address,uint256,string)`, and `RSETHPoolV2ExternalBridge.deposit(string)` — accept no such parameter. They compute the output amount from the oracle at execution time and mint it unconditionally:

```solidity
// RSETHPoolV3.sol L258-264
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

```solidity
// RSETHPoolV3.sol L286-292
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

The same pattern is present in `RSETHPool`, `RSETHPoolNoWrapper`, and `RSETHPoolV2ExternalBridge`. The oracle rate (`getRate()`) is a live external call whose value can change between block submission and inclusion. There is no mechanism for the depositor to bound the minimum rsETH they accept.

### Impact Explanation
A depositor sends ETH or an LST token expecting a certain rsETH amount based on the rate they observed off-chain. If the oracle rate rises (rsETH becomes more expensive in ETH terms) before the transaction is mined, the user receives fewer rsETH tokens than expected, with no revert. The protocol retains the full input value; the user simply receives less output than they intended. This matches the "contract fails to deliver promised returns, but doesn't lose value" impact category — **Low**.

### Likelihood Explanation
Oracle rates for rsETH update continuously as EigenLayer rewards accrue and as the underlying LST prices move. Any depositor on a congested network or during a period of rate movement is exposed. No privileged access is required; any unprivileged depositor calling `deposit()` on any L2 pool is affected. Likelihood is **Medium** (routine oracle updates make this a realistic occurrence for any deposit that is not mined in the same block it is submitted).

### Recommendation
Add a `minRSETHAmountExpected` parameter to every L2 pool `deposit()` overload and revert if the computed output falls below it, mirroring the pattern already used in `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused ...
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

### Proof of Concept
1. Alice calls `RSETHPoolV3.deposit{value: 1 ether}("ref")` when the oracle reports `rsETHToETHrate = 1.05e18`, expecting ≈ 0.952 wrsETH (after fee).
2. Before Alice's transaction is mined, the oracle updates to `rsETHToETHrate = 1.10e18`.
3. Alice's transaction executes; she receives ≈ 0.909 wrsETH — roughly 4.5% less than she expected — with no revert.
4. Alice has no recourse; the contract accepted her ETH and minted the lower amount silently.

The root cause is the unconditional mint at [1](#0-0)  and [2](#0-1) , contrasted with the correctly guarded path in [3](#0-2) . The same unguarded pattern appears in [4](#0-3) , [5](#0-4) , [6](#0-5) , [7](#0-6) , and [8](#0-7) .

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L258-264)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3.sol (L286-292)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
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
