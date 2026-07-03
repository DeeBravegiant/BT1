### Title
Missing Daily Mint Limit on `RSETHPoolNoWrapper` Deposit Functions Allows Full Pool Drain - (File: contracts/pools/RSETHPoolNoWrapper.sol)

### Summary
`RSETHPoolNoWrapper` omits the `limitDailyMint` safety modifier on both of its `deposit` functions. Every other pool variant in the protocol enforces a per-day cap on rsETH distributed. The absence of this control in `RSETHPoolNoWrapper` allows any depositor to drain the pool's entire pre-minted rsETH balance in a single transaction, leaving the pool unable to serve subsequent users.

### Finding Description
All other L2 pool contracts enforce a daily distribution cap via the `limitDailyMint` modifier:

- `RSETHPoolV3.deposit` (ETH) — `limitDailyMint(msg.value, ETH_IDENTIFIER)` [1](#0-0) 
- `RSETHPoolV3.deposit` (token) — `limitDailyMint(amount, token)` [2](#0-1) 
- `RSETHPoolV3ExternalBridge.deposit` (ETH) — `limitDailyMint(msg.value, ETH_IDENTIFIER)` [3](#0-2) 
- `RSETHPoolV3WithNativeChainBridge.deposit` (ETH/token) — same pattern [4](#0-3) 
- `RSETHPoolV2ExternalBridge.deposit` — `limitDailyMint(msg.value)` [5](#0-4) 

`RSETHPoolNoWrapper`, however, exposes both deposit paths with no such guard:

```solidity
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    ...
    rsETH.safeTransfer(msg.sender, rsETHAmount);
}

function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token) {
    ...
    rsETH.safeTransfer(msg.sender, rsETHAmount);
}
``` [6](#0-5) 

Unlike the other pools, `RSETHPoolNoWrapper` does not mint new wrapper tokens; it holds a pre-minted `rsETH` balance and transfers it directly to depositors. [7](#0-6) 

Without a daily cap, a single depositor can exhaust the entire rsETH reserve in one call.

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A depositor with sufficient ETH (or supported LST) can drain the pool's full rsETH balance atomically. After the drain, the pool holds zero rsETH and every subsequent `deposit` call reverts with an ERC-20 insufficient-balance error until the pool is manually refilled. No funds are stolen (the attacker pays oracle-fair value), but the pool ceases to function for all other users until an operator intervenes.

### Likelihood Explanation
Any unprivileged depositor can trigger this. No special role, oracle manipulation, or governance capture is required. The only prerequisite is holding enough ETH/LST to match the pool's rsETH reserve at the current oracle rate — a realistic condition given that the pool is designed to hold meaningful liquidity.

### Recommendation
Add the `limitDailyMint` modifier (or an equivalent daily-distribution cap) to both `deposit` overloads in `RSETHPoolNoWrapper`, consistent with every other pool variant in the protocol. The modifier should track distributed rsETH against a configurable daily ceiling and reset each day relative to `startTimestamp`.

### Proof of Concept
1. `RSETHPoolNoWrapper` is deployed on Arbitrum/Unichain with a balance of `N` rsETH tokens.
2. Attacker observes `N` and the current oracle rate `rsETHToETHrate` via `getRate()`.
3. Attacker calls `deposit("")` with `msg.value = N * rsETHToETHrate / 1e18` ETH (after fee).
4. `viewSwapRsETHAmountAndFee` returns `rsETHAmount ≈ N`; `rsETH.safeTransfer(attacker, N)` succeeds.
5. Pool balance is now 0. All subsequent `deposit` calls revert on the `safeTransfer` until the pool is refilled by an operator. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L246-252)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-281)
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
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-372)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-288)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-289)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L37-39)
```text
    /// @notice The canonical rsETH token address (rsETH OFT)
    IERC20 public rsETH;

```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-271)
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

    /// @dev Swaps token for rsETH
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
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
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
