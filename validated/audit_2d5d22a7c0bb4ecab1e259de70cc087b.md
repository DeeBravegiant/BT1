### Title
Lack of Minimum Output Slippage Protection in L2 Pool `deposit` Functions — (`contracts/pools/RSETHPoolV3.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPool.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`)

### Summary

All L2 pool `deposit` functions accept ETH or LST tokens and return wrsETH/rsETH based on a live oracle rate, but provide no `minRsETHAmount` parameter. If the oracle rate increases while a user's transaction is pending in the mempool, the user receives fewer rsETH/wrsETH than they previewed, with no ability to revert. The L1 `LRTDepositPool` already implements this protection via `minRSETHAmountExpected`, making the omission in L2 pools an inconsistency with a concrete user-loss path.

### Finding Description

Every L2 pool variant exposes a `deposit` function that computes the rsETH output at execution time using the live oracle rate:

```solidity
// RSETHPoolV3.sol lines 299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle call
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The `deposit` function then mints exactly that amount with no floor check:

```solidity
// RSETHPoolV3.sol lines 246-265
function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER) {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minOut check
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

The same pattern is present in the token-deposit overload and in every other pool variant (`RSETHPool.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV2ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`).

By contrast, the L1 `LRTDepositPool` explicitly enforces a minimum:

```solidity
// LRTDepositPool.sol lines 648-670
function _beforeDeposit(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected) private view returns (uint256 rsethAmountToMint) {
    ...
    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    if (rsethAmountToMint < minRSETHAmountExpected) {
        revert MinimumAmountToReceiveNotMet();
    }
}
```

The oracle rate (`rsETHToETHrate`) returned by `getRate()` reflects the current rsETH/ETH price. This price increases over time as staking rewards accrue and can also be updated by the `LRTOracle` at any block. If the oracle is updated between the block where the user previews the swap and the block where their transaction executes, the user receives fewer rsETH than expected with no recourse.

### Impact Explanation

A user who previews `viewSwapRsETHAmountAndFee` off-chain and then submits a `deposit` transaction may receive materially fewer wrsETH/rsETH than they expected if the oracle rate increases before their transaction is mined. The user has already transferred ETH (or tokens) to the pool and cannot revert. They receive fewer rsETH tokens than they intended to purchase, representing a direct financial loss relative to their expectation. This matches the "contract fails to deliver promised returns" impact class.

**Impact: Low** — Contract fails to deliver promised returns, but the user does not lose their principal outright; they receive fewer rsETH tokens than expected.

### Likelihood Explanation

The rsETH oracle price increases regularly as EigenLayer staking rewards accrue. On L2 chains, oracle updates may be batched or delayed, creating windows where the on-chain rate diverges from what a user observed. Any deposit transaction that sits in the mempool across an oracle update will silently receive fewer tokens. This is a routine, non-adversarial condition that affects every depositor on every L2 pool deployment.

**Likelihood: Medium** — Oracle rate changes are a normal, frequent occurrence; no adversarial action is required.

### Recommendation

Add a `minRsETHAmount` parameter to all `deposit` function overloads in every L2 pool contract, mirroring the pattern already used in `LRTDepositPool._beforeDeposit`. Revert if the computed `rsETHAmount` is below the caller-supplied minimum:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount) external payable ... {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert SlippageExceeded();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

### Proof of Concept

1. Alice calls `viewSwapRsETHAmountAndFee(1 ether)` on `RSETHPoolV3` and observes she will receive `X` wrsETH at the current oracle rate of `R`.
2. Alice submits `deposit{value: 1 ether}("ref")`.
3. Before Alice's transaction is mined, the `LRTOracle` is updated on L1 and the L2 oracle rate is refreshed to `R' > R`.
4. Alice's transaction executes: `rsETHAmount = 1e18 * 1e18 / R'` which is less than `X`.
5. Alice receives fewer wrsETH than she previewed, with no ability to revert.
6. The L1 equivalent (`LRTDepositPool.depositETH`) would have reverted with `MinimumAmountToReceiveNotMet` if Alice had passed her expected minimum. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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
