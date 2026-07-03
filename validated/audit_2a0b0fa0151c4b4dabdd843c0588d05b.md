Audit Report

## Title
L2 Pool Deposit Functions Lack Minimum Output (Slippage) Protection - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol)

## Summary
Every L2 pool `deposit()` function mints wrsETH (or transfers rsETH) based solely on the live oracle rate at execution time, with no caller-supplied minimum output parameter. The L1 `LRTDepositPool._beforeDeposit()` explicitly enforces a `minRSETHAmountExpected` guard, but this protection is entirely absent from all six L2 pool variants. A user who submits a deposit transaction when the oracle rate is `R` will silently receive fewer tokens than expected if the oracle is updated to a higher rate before the transaction is mined, with no on-chain recourse.

## Finding Description
In `LRTDepositPool`, `depositETH()` and `depositAsset()` both pass a caller-supplied `minRSETHAmountExpected` to `_beforeDeposit()`, which reverts with `MinimumAmountToReceiveNotMet` if the computed mint amount falls below it.

In every L2 pool, the `deposit(string memory referralId)` and `deposit(address token, uint256 amount, string memory referralId)` overloads accept no such parameter. The mint amount is computed as:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate   // ETH path
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate  // token path
```

where `rsETHToETHrate = IOracle(rsETHOracle).getRate()` is read live at execution time. No floor check on `rsETHAmount` exists anywhere in the deposit path. The `limitDailyMint` modifier also reads the oracle to compute `rsETHAmount`, but only compares it against a protocol-wide daily cap — it provides no per-user minimum guarantee.

Affected functions confirmed in code:
- `RSETHPoolV3ExternalBridge.deposit(string)` and `deposit(address,uint256,string)` — mints via `wrsETH.mint()`
- `RSETHPoolV3.deposit(string)` and `deposit(address,uint256,string)` — mints via `wrsETH.mint()`
- `RSETHPoolV3WithNativeChainBridge.deposit(string)` and `deposit(address,uint256,string)` — mints via `wrsETH.mint()`
- `RSETHPool.deposit(string)` and `deposit(address,uint256,string)` — transfers via `safeTransfer`
- `RSETHPoolNoWrapper.deposit(string)` and `deposit(address,uint256,string)` — transfers via `safeTransfer`
- `RSETHPoolV2ExternalBridge.deposit(string)` — mints via `wrsETH.mint()`

## Impact Explanation
**Low — Contract fails to deliver promised returns, but does not lose value.**

A depositor observes oracle rate `R` off-chain, computes expected output `X`, submits the transaction, and receives `X' < X` because the oracle was updated to a higher rate before inclusion. No funds are stolen; the depositor receives fewer wrsETH/rsETH than the rate visible at submission time. The L1 contract explicitly promises this protection via `minRSETHAmountExpected`; the L2 contracts do not, creating an asymmetry in the protocol's stated guarantees.

## Likelihood Explanation
Low. The rsETH oracle rate increases monotonically as restaking yield accrues; updates are periodic (not attacker-controlled). The window between transaction submission and inclusion is narrow under normal conditions. No privileged role or external attacker is required — any unprivileged user calling `deposit()` on any deployed L2 pool is exposed. The risk is proportional to deposit size and the magnitude of the oracle update.

## Recommendation
Add a `minRSETHAmountExpected` parameter to every L2 pool `deposit()` overload and revert if the computed amount falls below it, mirroring `LRTDepositPool._beforeDeposit()`:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

Apply identically to the token-deposit overload and to all other L2 pool variants.

## Proof of Concept
1. Oracle state: `rsETHToETHrate = 1.02e18`.
2. User calls `RSETHPoolV3ExternalBridge.deposit{value: 1 ether}("ref")`, expecting `≈ 0.980 wrsETH` (after fee).
3. Before the transaction is mined, the oracle is updated to `rsETHToETHrate = 1.05e18`.
4. Transaction executes: `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.952 wrsETH` — ~2.8% less than expected.
5. `wrsETH.mint(msg.sender, rsETHAmount)` succeeds; no revert occurs; user silently receives fewer tokens.
6. The equivalent L1 call (`LRTDepositPool.depositETH` with `minRSETHAmountExpected = 0.980e18`) would have reverted at step 4 with `MinimumAmountToReceiveNotMet`.

This sequence is reproducible as a Foundry fork test: deploy a mock oracle, submit the deposit, update the oracle rate in the same block before the deposit executes (via `vm.prank` + oracle setter), and assert the minted amount is below the pre-submission expectation with no revert. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-301)
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
