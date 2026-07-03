### Title
Missing Minimum Output Protection in L2 Pool Deposit Functions Exposes Depositors to Unfavorable Rate Execution - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

---

### Summary
The `deposit` functions in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` lack a minimum acceptable output parameter. Depositors have no on-chain protection against receiving fewer `wrsETH` tokens than they previewed, because the oracle rate used to compute the mint amount can change between transaction submission and execution — and the user is never shown or asked to confirm the exact output before it is committed.

---

### Finding Description

The vulnerability class in the external report is **missing user disclosure of what will be accessed/consumed**: a function silently uses more (or different) state than the user expects, with no mechanism for the user to bound or confirm the scope. The DeFi analog is a deposit function that silently mints an oracle-determined output with no user-specified floor, giving the user no on-chain recourse if the rate moves against them.

In `RSETHPoolV3.deposit` (ETH variant), the minted `wrsETH` amount is computed entirely from the live oracle rate at execution time, with no `minWrsETHAmount` guard: [1](#0-0) 

The token-deposit variant has the same structure: [2](#0-1) 

`RSETHPoolV3ExternalBridge.deposit` is identical in this respect: [3](#0-2) 

The rate used is fetched from `rsETHOracle` at call time: [4](#0-3) 

By contrast, the L1 `LRTDepositPool.depositETH` explicitly accepts and enforces a `minRSETHAmountExpected` parameter, reverting if the computed mint falls below it: [5](#0-4) 

The check is enforced inside `_beforeDeposit`: [6](#0-5) 

The L2 pools have no equivalent protection. A user who calls `viewSwapRsETHAmountAndFee` to preview the swap and then submits a deposit transaction has no guarantee that the oracle rate will not be updated before their transaction is mined, silently reducing their output.

---

### Impact Explanation

The depositor's ETH or LST is fully consumed by the pool. The `wrsETH` minted in return is determined by the oracle rate at execution time, which the user cannot bound. If the oracle rate increases (rsETH appreciates, which is the normal direction), the user receives fewer `wrsETH` than previewed. The user's principal is not lost — it is converted — but the protocol fails to deliver the return the user observed and intended to accept. This matches the **Low** impact tier: *contract fails to deliver promised returns, but doesn't lose value*.

---

### Likelihood Explanation

The rsETH oracle rate increases monotonically as staking rewards accumulate. Any oracle update between a user's preview call and their deposit transaction execution produces a shortfall. This is not a rare edge case; it is a persistent structural condition affecting every deposit on both `RSETHPoolV3` and `RSETHPoolV3ExternalBridge`. The attacker-controlled entry path is simply calling `deposit` as an ordinary depositor — no special role or privilege is required.

---

### Recommendation

Add a `minWrsETHAmount` parameter to all `deposit` overloads in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge`. After computing `rsETHAmount` via `viewSwapRsETHAmountAndFee`, revert if `rsETHAmount < minWrsETHAmount`, consistent with the pattern already used in `LRTDepositPool.depositETH`.

---

### Proof of Concept

1. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain and observes they will receive `X` wrsETH.
2. User submits `deposit{value: 1 ether}("ref")` to `RSETHPoolV3`.
3. Before the transaction is mined, the rsETH oracle rate is updated (rsETH has appreciated).
4. `viewSwapRsETHAmountAndFee` is re-evaluated inside the transaction at the new, higher rate: `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` yields `X' < X`.
5. `wrsETH.mint(msg.sender, X')` executes with no revert — the user receives fewer tokens than they agreed to, with no on-chain recourse. [4](#0-3) [7](#0-6)

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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
