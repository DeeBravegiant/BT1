### Title
L2 Pool `deposit()` Functions Lack Minimum Output Slippage Protection, Unlike L1 Counterpart - (File: contracts/pools/RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPool.sol, RSETHPoolNoWrapper.sol)

---

### Summary

Every L2 pool `deposit()` function mints wrsETH (or rsETH) to the caller using a rate fetched live from an oracle at execution time, but accepts **no `minRSETHAmountExpected` parameter**. There is therefore no on-chain slippage guard protecting the depositor against an oracle rate update that occurs between tx submission and tx execution. The L1 `LRTDepositPool` explicitly provides this protection; the L2 pools do not.

---

### Finding Description

On L1, `LRTDepositPool.depositETH()` and `depositAsset()` both accept a `minRSETHAmountExpected` argument and enforce it inside `_beforeDeposit()`:

```solidity
// contracts/LRTDepositPool.sol
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
function depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, ...) external ...
``` [1](#0-0) [2](#0-1) 

The check `if (rsethAmountToMint < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet()` ensures the user cannot receive fewer shares than they declared acceptable.

On every L2 pool variant, the analogous `deposit()` functions accept only a `referralId` string (for ETH deposits) or `(token, amount, referralId)` (for LST deposits). No minimum output is accepted or checked:

```solidity
// contracts/pools/RSETHPoolV3.sol
function deposit(string memory referralId) external payable ... {
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    wrsETH.mint(msg.sender, rsETHAmount);   // no minimum check
}

function deposit(address token, uint256 amount, string memory referralId) external ... {
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
    wrsETH.mint(msg.sender, rsETHAmount);   // no minimum check
}
``` [3](#0-2) [4](#0-3) 

The same pattern is present in every other L2 pool variant: [5](#0-4) [6](#0-5) 

The wrsETH amount is computed as:

```
rsETHAmount = (amount - fee) * 1e18 / rsETHToETHrate
```

where `rsETHToETHrate = IOracle(rsETHOracle).getRate()` is read at execution time. [7](#0-6) 

The oracle rate is pushed cross-chain via LayerZero (`MultiChainRateProvider` → `CrossChainRateReceiver`) and can be updated at any time by the operator. If the rate is updated in the same block as, or just before, a user's deposit transaction, the user receives fewer wrsETH than they observed when constructing the transaction, with no on-chain recourse.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user who previews `viewSwapRsETHAmountAndFee()` off-chain and submits a deposit transaction may receive materially fewer wrsETH than expected if the oracle rate is updated before their transaction executes. The user's ETH/LST is accepted and is not returned; they simply receive a smaller wrsETH balance than the rate they observed implied. The discrepancy is bounded by the magnitude of the rate update, which is typically small per update but can compound if multiple updates occur in rapid succession (e.g., after a large staking-reward accrual event on L1).

---

### Likelihood Explanation

The oracle rate is updated by an operator-controlled cross-chain message. On L2 networks with fast block times (Arbitrum, Base, Optimism, Linea), a rate update and a user deposit can land in the same or adjacent blocks without any coordination. This is a normal operational event, not an attack. Any depositor who previews the rate and then submits a transaction is exposed to this race condition on every deposit.

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to all L2 pool `deposit()` functions, mirroring the L1 `LRTDepositPool` design:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

Apply the same change to the token-deposit overload and to all pool variants: `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, and `RSETHPoolNoWrapper`.

---

### Proof of Concept

1. Oracle rate at tx-submission time: `rsETHToETHrate = 1.05e18` (1 rsETH = 1.05 ETH).
2. User calls `deposit{value: 1 ether}("ref")` expecting `≈ 0.952 wrsETH`.
3. Before the tx executes, the operator pushes a rate update: `rsETHToETHrate = 1.10e18`.
4. `viewSwapRsETHAmountAndFee(1 ether)` now returns `≈ 0.909 wrsETH`.
5. `wrsETH.mint(msg.sender, 0.909e18)` executes — the user receives ~4.5% fewer tokens than expected with no revert and no recourse.

The L1 equivalent (`LRTDepositPool.depositETH`) would have reverted at step 5 if the user had passed `minRSETHAmountExpected = 0.952e18`. [8](#0-7) [9](#0-8)

### Citations

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
