### Title
L2 Pool `deposit()` Functions Lack Minimum rsETH Output Protection — (File: `contracts/pools/RSETHPool.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

Every L2 pool `deposit()` function accepts ETH or a supported token and mints rsETH at the oracle rate at execution time, but none of them accept a `minRsETHAmount` parameter. The L1 `LRTDepositPool` explicitly provides this protection via `minRSETHAmountExpected`. Without it, any oracle rate movement between transaction submission and execution silently delivers fewer rsETH tokens than the user anticipated, with no revert path.

---

### Finding Description

All five L2 pool contracts expose two public deposit entry points:

- `deposit(string memory referralId)` — ETH path
- `deposit(address token, uint256 amount, string memory referralId)` — LST path

In every case the rsETH amount is computed at execution time:

```solidity
// RSETHPool.sol L265-278 (ETH path)
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    ...
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
}
```

`viewSwapRsETHAmountAndFee` divides by the live oracle rate:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // RSETHPool.sol L319
```

There is no floor check on `rsETHAmount`. The function succeeds regardless of how far the rate has moved.

By contrast, the L1 deposit pool enforces a caller-supplied floor:

```solidity
// LRTDepositPool.sol L667-669
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

The same pattern is absent from all five L2 pool contracts:
- `RSETHPool.deposit` (L265, L284) [1](#0-0) 
- `RSETHPoolNoWrapper.deposit` (L231, L250) [2](#0-1) 
- `RSETHPoolV3.deposit` (L246, L271) [3](#0-2) 
- `RSETHPoolV3ExternalBridge.deposit` (L366, L390) [4](#0-3) 
- `RSETHPoolV3WithNativeChainBridge.deposit` (L282, L307) [5](#0-4) 

The L1 counterpart that correctly enforces the minimum: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but does not lose value.**

When the rsETH/ETH oracle rate rises between a user's transaction submission and its on-chain execution, the user receives fewer rsETH tokens than they calculated off-chain. The ETH value of the received rsETH equals the deposited ETH at the new rate, so no ETH is stolen, but the user obtains fewer rsETH units than intended. This matters concretely when the user needs a specific rsETH quantity (e.g., to meet a collateral threshold in a downstream protocol) and silently receives less with no revert.

---

### Likelihood Explanation

**Moderate.** The rsETH oracle rate increases continuously as EigenLayer restaking rewards accrue. On L2 networks where block times differ from L1, or during periods of elevated network congestion, the gap between transaction submission and execution is wide enough for a meaningful rate movement. No privileged action is required; the rate drifts organically. Users who preview the swap off-chain and submit without a minimum output guard are exposed on every deposit.

---

### Recommendation

Add a `minRsETHAmount` parameter to both `deposit` overloads in all five L2 pool contracts, mirroring the existing L1 pattern:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert MinimumAmountToReceiveNotMet();
    ...
}
```

---

### Proof of Concept

1. Oracle rate is `1.10 ETH/rsETH`. User calls `deposit{value: 1 ether}("ref")` on `RSETHPoolV3`, expecting `≈ 0.909 rsETH`.
2. Before the transaction is mined, the oracle rate updates to `1.20 ETH/rsETH` (organic yield accrual).
3. `viewSwapRsETHAmountAndFee(1 ether)` returns `rsETHAmount = 1e18 * 1e18 / 1.2e18 = 0.833e18`.
4. The contract mints `0.833 rsETH` to the user — `0.076 rsETH` less than expected — and emits `SwapOccurred` without reverting.
5. The user has no on-chain mechanism to reject this outcome because no minimum output parameter exists. [8](#0-7) [9](#0-8)

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-412)
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

    /// @dev Swaps supported token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-329)
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

    /// @dev Swaps supported token for rsETH
    /// @param token The token address
    /// @param amount The amount of token
    /// @param referralId The referral id
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

**File:** contracts/LRTDepositPool.sol (L648-669)
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
```
