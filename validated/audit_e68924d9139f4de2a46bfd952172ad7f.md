### Title
Zero rsETH Minted on Dust Deposits Due to Missing Output Amount Validation - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolNoWrapper.sol)

### Summary
All L2 pool `deposit()` functions validate that the input `amount > 0` but never validate that the computed `rsETHAmount > 0` before minting or transferring rsETH/wrsETH to the depositor. Due to integer division truncation in `viewSwapRsETHAmountAndFee`, a non-zero deposit can produce `rsETHAmount = 0`, causing the user to permanently lose their deposited ETH or tokens while receiving nothing.

### Finding Description
In every L2 pool contract, the ETH and token deposit paths follow this pattern:

```solidity
// RSETHPoolV3ExternalBridge.sol (identical in V3, V3WithNativeChainBridge, NoWrapper)
function deposit(string memory referralId) external payable ... {
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();                    // only checks input

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);                       // rsETHAmount can be 0
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

The rate computation in `viewSwapRsETHAmountAndFee` uses integer division:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // truncates to 0 for dust
```

When `amountAfterFee * 1e18 < rsETHToETHrate`, the division truncates to zero. Since rsETH trades at approximately 1.05–1.1 ETH, any deposit where `amountAfterFee < rsETHToETHrate / 1e18` (i.e., 1–2 wei at normal fee levels) yields `rsETHAmount = 0`. The deposited ETH is retained by the pool and eventually bridged to L1 for the protocol's benefit; the user receives nothing.

The same truncation applies to the token deposit path:

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
**Low — Contract fails to deliver promised returns.**

A depositor sending a dust amount of ETH (e.g., 1 wei) receives 0 wrsETH/rsETH while their ETH is absorbed into the pool. The user's funds are not recoverable: the ETH is pooled with all other deposits and bridged to L1 collectively. The user has no claim on any rsETH. The loss per transaction is bounded by the dust threshold (≤ 1–2 wei at normal fee levels), so aggregate financial impact is negligible under normal conditions. However, the contract silently fails to deliver its core promise (ETH in → rsETH out) without reverting.

### Likelihood Explanation
**Low.** Under normal fee configurations (e.g., 10–50 bps), the zero-output threshold is 1–2 wei of ETH, which is economically irrational to deposit intentionally. The scenario is most likely to occur accidentally (e.g., a contract sending a residual dust balance). No attacker-controlled amplification path exists to scale the loss.

### Recommendation
Add a post-computation zero-check in all `deposit()` functions, analogous to the recommendation in the reference report:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount(); // add this check
```

This should be applied in:
- `RSETHPoolV3ExternalBridge.deposit(string)` and `deposit(address,uint256,string)`
- `RSETHPoolV3.deposit(string)` and `deposit(address,uint256,string)`
- `RSETHPoolV3WithNativeChainBridge.deposit(string)` and `deposit(address,uint256,string)`
- `RSETHPoolNoWrapper.deposit(string)` and `deposit(address,uint256,string)` [7](#0-6) [8](#0-7) [9](#0-8) 

### Proof of Concept
Assume `feeBps = 10` (0.1%), `rsETHToETHrate = 1.05e18` (rsETH worth 1.05 ETH):

1. User calls `RSETHPoolV3ExternalBridge.deposit{value: 1}("")`.
2. `amount = 1 wei`. Check `amount == 0` passes.
3. `fee = 1 * 10 / 10_000 = 0`. `amountAfterFee = 1`.
4. `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer truncation).
5. `feeEarnedInETH += 0`. `wrsETH.mint(msg.sender, 0)` — user receives 0 wrsETH.
6. The 1 wei ETH remains in the pool, to be bridged to L1 with the next `bridgeAssets()` call.
7. User has lost 1 wei with no recourse.

The same applies to token deposits where `amountAfterFee * tokenToETHRate < rsETHToETHrate`. [10](#0-9) [11](#0-10)

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L364-384)
```text
    /// @dev Swaps ETH for rsETH
    /// @param referralId The referral id
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
