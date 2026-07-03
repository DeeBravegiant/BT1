### Title
No Slippage Protection on L2 Pool `deposit` Functions - (File: contracts/pools/RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolNoWrapper.sol, RSETHPoolV3WithNativeChainBridge.sol)

### Summary
All L2 pool `deposit` functions accept a fixed input amount (ETH or LST) and compute the rsETH/wrsETH output dynamically from a live oracle rate, but provide no `minRsETHAmount` parameter. A user who previews a rate off-chain and submits a transaction can receive materially fewer rsETH tokens than expected if the oracle rate is updated in the same block before their transaction executes.

### Finding Description
Every L2 pool contract exposes two public `deposit` entry points — one for native ETH and one for supported ERC-20 tokens. In each case the output amount is computed at execution time by calling `viewSwapRsETHAmountAndFee`, which reads the live oracle rate via `getRate()`:

```solidity
// RSETHPoolV3.sol – ETH deposit path
function deposit(string memory referralId) external payable ... {
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    wrsETH.mint(msg.sender, rsETHAmount);
}

// viewSwapRsETHAmountAndFee
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (...) {
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

Neither the ETH path nor the token path accepts a `minRsETHAmountExpected` argument. There is no on-chain guard that reverts if the minted amount falls below what the user anticipated.

This contrasts directly with the L1 `LRTDepositPool`, which does enforce slippage protection:

```solidity
// LRTDepositPool.sol
function depositETH(uint256 minRSETHAmountExpected, ...) external payable ... {
    uint256 rsethAmountToMint = _beforeDeposit(..., minRSETHAmountExpected);
    ...
}

function _beforeDeposit(..., uint256 minRSETHAmountExpected) private view returns (...) {
    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    if (rsethAmountToMint < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
}
```

The same gap exists identically in `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolNoWrapper.sol`, and `RSETHPoolV3WithNativeChainBridge.sol`.

### Impact Explanation
A user who queries `viewSwapRsETHAmountAndFee` off-chain and then submits a `deposit` transaction can have the oracle rate updated (by any caller of `updateRSETHPrice`) in the same block before their transaction is included. Because rsETH is a yield-bearing token whose price only increases over time, a rate update between preview and execution means the user receives fewer rsETH tokens than expected for the same ETH/token input. The user cannot enforce a minimum acceptable output, so the contract fails to deliver the promised return.

**Impact**: Low — contract fails to deliver promised returns, but the user does not lose the ETH value of their deposit (the fewer rsETH tokens they receive are each worth proportionally more ETH).

### Likelihood Explanation
The rsETH oracle rate (`updateRSETHPrice` in `LRTOracle`) is callable by any address when the protocol is not paused. On L2 chains with low block times (e.g., Arbitrum, Optimism, Unichain), the probability of an oracle update landing in the same block as a user deposit is non-trivial, especially during periods of active yield accrual. No special attacker capability is required — the oracle update is a normal protocol operation.

### Recommendation
Add a `minRsETHAmountExpected` parameter to both `deposit` overloads in all L2 pool contracts, mirroring the pattern already used in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused ...
{
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(msg.value);
    if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

### Proof of Concept

1. Alice calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain and sees she will receive `X` wrsETH at the current oracle rate `R`.
2. Alice submits `deposit{value: 1 ether}("ref")` to `RSETHPoolV3`.
3. Before Alice's transaction is included, a keeper calls `LRTOracle.updateRSETHPrice()`, increasing the rate from `R` to `R'` (R' > R).
4. Alice's transaction executes with the new rate: `rsETHAmount = 1e18 * 1e18 / R'`, which is less than `X`.
5. Alice receives fewer wrsETH tokens than she previewed, with no on-chain recourse.

The same sequence applies to the token deposit path and to `RSETHPoolV3ExternalBridge`, `RSETHPoolNoWrapper`, and `RSETHPoolV3WithNativeChainBridge`.

---

**Affected code — L2 pool ETH deposit (no min-output guard):** [1](#0-0) 

**Affected code — L2 pool token deposit (no min-output guard):** [2](#0-1) 

**Rate calculation reads live oracle with no floor check:** [3](#0-2) 

**Same gap in RSETHPoolV3ExternalBridge:** [4](#0-3) 

**Same gap in RSETHPoolNoWrapper:** [5](#0-4) 

**Same gap in RSETHPoolV3WithNativeChainBridge:** [6](#0-5) 

**L1 LRTDepositPool correctly enforces slippage (reference implementation):** [7](#0-6)

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
