### Title
Missing Minimum Output Protection in L2 Pool `deposit()` Functions Exposes Users to Oracle Rate Manipulation / Slippage - (File: contracts/pools/RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPoolV3.sol, RSETHPoolV2ExternalBridge.sol)

---

### Summary

Every L2 pool `deposit()` function that swaps ETH or LST tokens for rsETH/wrsETH accepts no `minRSETHAmountExpected` parameter. A user's transaction is therefore executed at whatever oracle rate is current at inclusion time, with no floor on the rsETH amount received. This is the direct structural analog of the reported "sandwich attack" vulnerability: a swap inside a user-handled transaction with no slippage guard.

---

### Finding Description

The L1 `LRTDepositPool` correctly exposes slippage protection to callers:

```solidity
// contracts/LRTDepositPool.sol
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
function depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, ...) external ...
```

Every L2 pool `deposit()` function omits this parameter entirely:

```solidity
// contracts/pools/RSETHPool.sol
function deposit(string memory referralId) external payable nonReentrant whenNotPaused { ... }
function deposit(address token, uint256 amount, string memory referralId) external nonReentrant whenNotPaused ... { ... }

// contracts/pools/RSETHPoolNoWrapper.sol
function deposit(string memory referralId) external payable nonReentrant whenNotPaused { ... }
function deposit(address token, uint256 amount, string memory referralId) external nonReentrant whenNotPaused ... { ... }

// contracts/pools/RSETHPoolV3ExternalBridge.sol
function deposit(string memory referralId) external payable nonReentrant whenNotPaused ... { ... }
function deposit(address token, uint256 amount, string memory referralId) external nonReentrant whenNotPaused ... { ... }

// contracts/pools/RSETHPoolV3WithNativeChainBridge.sol  (same pattern)
// contracts/pools/RSETHPoolV3.sol                       (same pattern)
// contracts/pools/RSETHPoolV2ExternalBridge.sol         (same pattern)
```

In every case the rsETH amount is computed solely from the live oracle rate at execution time:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
// or viewSwapRsETHAmountAndFee(amount, token)
wrsETH.mint(msg.sender, rsETHAmount);   // or safeTransfer
```

`viewSwapRsETHAmountAndFee` reads `getRate()` → `IOracle(rsETHOracle).getRate()` at the moment of execution. The caller has no way to specify a minimum acceptable output.

---

### Impact Explanation

**Impact: Low — Contract fails to deliver promised returns.**

A user who previews the exchange rate off-chain (or via `viewSwapRsETHAmountAndFee`) and submits a `deposit()` transaction has no on-chain guarantee that the rate will still be favorable at inclusion time. If the oracle rate moves adversely between submission and execution — whether through legitimate market movement, a block-reorg, or deliberate oracle front-running — the user receives fewer rsETH tokens than they anticipated with no recourse. Unlike the L1 `LRTDepositPool`, the L2 pools provide no mechanism for the user to enforce a minimum output, so the contract silently delivers less value than the user expected.

If the oracle used is a spot-price or short-window TWAP source that can be transiently influenced, the impact escalates to **Medium (theft of unclaimed yield / temporary value loss)** because an attacker can sandwich the deposit: push the oracle rate up (rsETH appears more expensive) before the user's transaction, let the user receive fewer rsETH, then restore the rate.

---

### Likelihood Explanation

**Likelihood: Medium.**

- These are the primary user-facing entry points on every deployed L2 chain (Arbitrum, Base, Optimism, etc.).
- Oracle rate changes between mempool submission and block inclusion are routine on L2s with sequencer reordering.
- The absence of a slippage guard is a structural property of the contract, not a transient condition; every deposit is affected.
- The L1 counterpart already has the fix, confirming the design intent was to protect users — the omission on L2 is an oversight.

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to every public `deposit()` overload in all L2 pool contracts, mirroring the L1 `LRTDepositPool` pattern:

```solidity
function deposit(
    string memory referralId,
    uint256 minRSETHAmountExpected   // <-- add this
) external payable nonReentrant whenNotPaused {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert SlippageExceeded();
    ...
}
```

Apply the same pattern to the token-deposit overloads and to `RSETHPoolNoWrapper`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPoolV2ExternalBridge`.

---

### Proof of Concept

1. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain; oracle rate = 1.05 ETH/rsETH → expected output ≈ 0.952 rsETH.
2. Oracle rate moves to 1.10 ETH/rsETH before the transaction is included (legitimate market move or sequencer reordering).
3. User's `deposit{value: 1 ether}("ref")` executes; `viewSwapRsETHAmountAndFee` now returns ≈ 0.909 rsETH.
4. User receives ≈ 0.909 rsETH — ~4.5% less than expected — with no revert and no recourse.
5. On L1, the same user calling `LRTDepositPool.depositETH(0.95e18, "ref")` would have the transaction revert at step 3, protecting them.

Relevant code locations:

- `RSETHPool.deposit(string)` — no minimum output [1](#0-0) 
- `RSETHPool.deposit(address,uint256,string)` — no minimum output [2](#0-1) 
- `RSETHPoolNoWrapper.deposit(string)` — no minimum output [3](#0-2) 
- `RSETHPoolNoWrapper.deposit(address,uint256,string)` — no minimum output [4](#0-3) 
- `RSETHPoolV3ExternalBridge.deposit(string)` — no minimum output [5](#0-4) 
- `RSETHPoolV3ExternalBridge.deposit(address,uint256,string)` — no minimum output [6](#0-5) 
- `RSETHPoolV3WithNativeChainBridge.deposit(string)` — no minimum output [7](#0-6) 
- `RSETHPoolV3WithNativeChainBridge.deposit(address,uint256,string)` — no minimum output [8](#0-7) 
- `RSETHPoolV2ExternalBridge.deposit(string)` — no minimum output [9](#0-8) 
- L1 reference with correct slippage guard: `LRTDepositPool.depositETH` / `depositAsset` [10](#0-9) 
- Rate computation (oracle read at execution time): [11](#0-10)

### Citations

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

**File:** contracts/pools/RSETHPool.sol (L311-320)
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

**File:** contracts/LRTDepositPool.sol (L76-118)
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

    /// @notice helps user stake LST to the protocol
    /// @param asset LST asset address to stake
    /// @param depositAmount LST asset amount to stake
    /// @param minRSETHAmountExpected Minimum amount of rseth to receive
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```
