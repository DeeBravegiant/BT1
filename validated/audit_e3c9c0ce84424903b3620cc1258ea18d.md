Looking at the external report, the vulnerability class is: **a slippage parameter is accepted by a function but then overwritten/ignored, so the actual slippage protection comes only from a different, less granular parameter — potentially freezing settlement.**

Let me verify the `_beforeDeposit` enforcement and check all deposit paths.

### Title
L2 Pool Deposit Functions Lack Minimum Output (Slippage) Protection - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol)

---

### Summary

All L2 pool `deposit()` functions mint wrsETH based solely on the oracle rate at execution time, with no `minRSETHAmountExpected` parameter or minimum output check. The L1 `LRTDepositPool` correctly enforces this protection, but every L2 pool variant completely omits it, leaving depositors with zero slippage protection.

---

### Finding Description

The L1 `LRTDepositPool.depositETH()` and `depositAsset()` accept a `minRSETHAmountExpected` parameter and enforce it inside `_beforeDeposit()`:

```solidity
// contracts/LRTDepositPool.sol L648-L669
function _beforeDeposit(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected)
    private view returns (uint256 rsethAmountToMint)
{
    ...
    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    if (rsethAmountToMint < minRSETHAmountExpected) {
        revert MinimumAmountToReceiveNotMet();
    }
}
```

In contrast, every L2 pool `deposit()` function mints wrsETH with no minimum output check whatsoever. For example, `RSETHPoolV3ExternalBridge.deposit()`:

```solidity
// contracts/pools/RSETHPoolV3ExternalBridge.sol L366-L384
function deposit(string memory referralId) external payable ... {
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // ← no minimum check
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

The same pattern is present in all L2 pool variants:
- `RSETHPoolV3.deposit()` (ETH and token overloads)
- `RSETHPoolV3WithNativeChainBridge.deposit()` (ETH and token overloads)
- `RSETHPool.deposit()`
- `RSETHPoolNoWrapper.deposit()`
- `RSETHPoolV2ExternalBridge.deposit()`

The minted amount is computed as:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

where `rsETHToETHrate` is fetched live from the oracle at execution time. [1](#0-0) 

---

### Impact Explanation

A user submits a deposit transaction expecting to receive `X` wrsETH based on the oracle rate visible at submission time. If the oracle rate is updated (rsETH appreciates, increasing `rsETHToETHrate`) before the transaction is included, the user receives fewer wrsETH than anticipated with no on-chain protection. Because the L1 contract explicitly provides this guarantee via `minRSETHAmountExpected` [2](#0-1)  but the L2 contracts do not, the protocol fails to deliver the promised return to L2 depositors. Impact: **Low — contract fails to deliver promised returns, but does not lose value.**

---

### Likelihood Explanation

Low. The oracle rate is updated periodically as rsETH accrues restaking yield. An update occurring between a user's transaction submission and its on-chain execution is possible but not frequent. No attacker-controlled mechanism is required; the risk arises from normal protocol operation. The entry path is any unprivileged user calling `deposit()` on any deployed L2 pool. [3](#0-2) 

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to every L2 pool `deposit()` function and revert if the computed `rsETHAmount` falls below it, mirroring the L1 `_beforeDeposit()` guard:

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

Apply the same change to the token-deposit overload and to all other L2 pool variants. [4](#0-3) 

---

### Proof of Concept

1. Current oracle rate: `rsETHToETHrate = 1.02e18` (rsETH worth 1.02 ETH).
2. User submits `RSETHPoolV3ExternalBridge.deposit{value: 1 ether}("ref")`, expecting to receive `≈ 0.980 wrsETH`.
3. Before the transaction is mined, the oracle is updated to `rsETHToETHrate = 1.05e18`.
4. Transaction executes: `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.952 wrsETH` — roughly 2.8% less than expected.
5. No revert occurs; user silently receives fewer tokens than anticipated, with no recourse.

The L1 path (`LRTDepositPool.depositETH`) would have reverted at step 4 with `MinimumAmountToReceiveNotMet` if the user had set `minRSETHAmountExpected = 0.980e18`. [5](#0-4) [6](#0-5)

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-426)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
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
