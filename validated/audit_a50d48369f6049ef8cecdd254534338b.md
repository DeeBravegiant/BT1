### Title
Missing Slippage Protection in `deposit()` Functions Across Pool Contracts - (`File: contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPool.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`, `contracts/pools/RSETHPoolV2.sol`, `contracts/pools/RSETHPoolV2ExternalBridge.sol`)

---

### Summary

All `deposit()` functions across the RSETH pool family accept ETH or supported tokens from users and return wrsETH/rsETH computed from a live oracle rate, but expose no `minRsETHAmount` parameter. A user has no on-chain mechanism to enforce a minimum output, so an oracle rate change between transaction submission and execution can silently deliver fewer rsETH tokens than the user anticipated.

---

### Finding Description

Every pool variant's `deposit()` function follows the same pattern:

1. Accept ETH or an ERC-20 token from the caller.
2. Call `viewSwapRsETHAmountAndFee()`, which reads the live oracle rate via `IOracle(rsETHOracle).getRate()`.
3. Immediately transfer or mint the computed `rsETHAmount` to the caller — with no floor check.

Representative instances:

**`RSETHPoolV3ExternalBridge.sol` — ETH deposit (line 366):**
```solidity
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minRsETHAmount guard
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

**`RSETHPoolV3ExternalBridge.sol` — token deposit (line 390):**
```solidity
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token) limitDailyMint(amount, token)
{
    if (amount == 0) revert InvalidAmount();
    IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
    feeEarnedInToken[token] += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minRsETHAmount guard
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
}
```

The same pattern is present in every pool variant:

| Contract | Function | Lines |
|---|---|---|
| `RSETHPool.sol` | `deposit(string)` | 265–278 |
| `RSETHPool.sol` | `deposit(address,uint256,string)` | 284–305 |
| `RSETHPoolV2.sol` | `deposit(string)` | 207–219 |
| `RSETHPoolV2ExternalBridge.sol` | `deposit(string)` | 289–301 |
| `RSETHPoolV3.sol` | `deposit(string)` | 246–265 |
| `RSETHPoolV3.sol` | `deposit(address,uint256,string)` | 271–293 |
| `RSETHPoolV3ExternalBridge.sol` | `deposit(string)` | 366–384 |
| `RSETHPoolV3ExternalBridge.sol` | `deposit(address,uint256,string)` | 390–412 |
| `RSETHPoolNoWrapper.sol` | `deposit(string)` | 231–244 |
| `RSETHPoolNoWrapper.sol` | `deposit(address,uint256,string)` | 250–271 |
| `RSETHPoolV3WithNativeChainBridge.sol` | `deposit(string)` | 282–301 |
| `RSETHPoolV3WithNativeChainBridge.sol` | `deposit(address,uint256,string)` | 307–329 |

Contrast this with `LRTDepositPool.depositAsset()` / `depositETH()`, which correctly accept and enforce a `minRSETHAmountExpected` parameter:

```solidity
// LRTDepositPool.sol line 667
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

The pool contracts have no equivalent guard.

---

### Impact Explanation

A user who previews the exchange rate off-chain and submits a `deposit()` transaction can receive materially fewer rsETH/wrsETH tokens than expected if the oracle rate changes before the transaction is mined. The user's ETH or LST tokens are consumed in full; the shortfall in rsETH is unrecoverable. This constitutes the contract failing to deliver the promised return without any loss of value to the protocol itself.

**Impact: Low** — Contract fails to deliver promised returns, but doesn't lose value.

---

### Likelihood Explanation

The rsETH oracle rate (`IOracle(rsETHOracle).getRate()`) reflects the total ETH value of protocol assets divided by total rsETH supply. This rate increases monotonically under normal staking conditions, but can jump discretely when the oracle is updated (e.g., after a large batch of staking rewards is reported). Any user whose transaction is pending across such an update receives fewer rsETH than they previewed. This is a routine, non-adversarial scenario that affects every depositor who relies on an off-chain quote.

---

### Recommendation

Add a `minRsETHAmount` parameter to all `deposit()` overloads in every pool contract, and revert if the computed output falls below it — mirroring the pattern already used in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert MinimumAmountToReceiveNotMet();

    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

Apply the same change to every token `deposit()` overload across all pool contracts.

---

### Proof of Concept

1. Alice calls `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee(1 ether)` off-chain and sees she will receive `X` wrsETH at the current oracle rate.
2. Alice submits `deposit{value: 1 ether}("ref")`.
3. Before Alice's transaction is mined, the rsETH oracle is updated (e.g., staking rewards are reported), increasing `getRate()`.
4. Alice's transaction executes: `viewSwapRsETHAmountAndFee` now returns `X - delta` wrsETH because the denominator (rsETH/ETH rate) is higher.
5. Alice receives `X - delta` wrsETH — less than she expected — with no revert and no recourse. Her 1 ETH is fully consumed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9) [11](#0-10) [12](#0-11)

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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L307-329)
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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
