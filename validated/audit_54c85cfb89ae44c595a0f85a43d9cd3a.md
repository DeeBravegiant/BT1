### Title
Missing Minimum Amount Out in L2 Pool `deposit()` Functions - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

### Summary
The `deposit()` functions in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` lack a `minAmountOut` parameter. The amount of wrsETH minted is determined entirely by the oracle rate at execution time, with no user-supplied floor. If the oracle rate changes between TX submission and on-chain execution (including via a block re-org), the depositor silently receives fewer wrsETH than they observed when constructing the transaction. The L1 counterpart (`LRTDepositPool`) already enforces this protection via `minRSETHAmountExpected`.

### Finding Description
`RSETHPoolV3.deposit(string referralId)` and `deposit(address token, uint256 amount, string referralId)` compute the wrsETH output exclusively from the live oracle rate at execution time:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
wrsETH.mint(msg.sender, rsETHAmount);
```

`viewSwapRsETHAmountAndFee` calls `getRate()` → `IOracle(rsETHOracle).getRate()`, which is a live external read. There is no parameter allowing the caller to specify the minimum wrsETH they are willing to accept.

The same pattern exists in `RSETHPoolV3ExternalBridge.deposit()`.

By contrast, `LRTDepositPool.depositETH(uint256 minRSETHAmountExpected, ...)` and `depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, ...)` both enforce a caller-supplied floor inside `_beforeDeposit`:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

The L2 pool contracts provide no equivalent guard.

### Impact Explanation
A depositor who observes rate R and submits a transaction may have that transaction executed at rate R′ > R (rate rose, meaning fewer wrsETH per ETH). The depositor receives fewer wrsETH than they intended to accept, with no on-chain recourse. Because wrsETH represents a claim on the underlying restaked ETH, receiving fewer tokens is a direct reduction in the depositor's economic position. This maps to **Low: Contract fails to deliver promised returns, but doesn't lose value** — the depositor's ETH is not stolen, but the protocol does not deliver the quantity of wrsETH the user expected.

### Likelihood Explanation
The oracle rate (`rsETHOracle`) is updated externally and can change between the block in which a user reads the rate and the block in which their transaction is included. On Ethereum mainnet, block re-orgs (even shallow 1-block re-orgs) are documented and occur regularly. A re-org that replaces the block containing an oracle update causes the depositor's transaction to execute against a different rate than observed. No attacker action is required beyond the natural occurrence of re-orgs; the depositor has no on-chain mechanism to protect themselves.

### Recommendation
Add a `minRsETHAmountOut` parameter to all `deposit()` overloads in `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, and any other L2 pool variants, and revert if the computed `rsETHAmount` falls below it:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountOut)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountOut) revert SlippageExceeded();
    wrsETH.mint(msg.sender, rsETHAmount);
    ...
}
```

### Proof of Concept
1. Oracle rate is 1.10 ETH/rsETH. User calls `deposit{value: 1 ether}("ref")` expecting ≈ 0.909 wrsETH.
2. Before the transaction is mined, a 1-block re-org occurs; the oracle update that set 1.10 is replaced, and the rate reverts to 1.20 ETH/rsETH.
3. The user's transaction executes at 1.20 ETH/rsETH, minting ≈ 0.833 wrsETH — an ~8.3 % shortfall with no revert.
4. The user has no on-chain protection and no way to detect this outcome before submission.

**Root cause:** [1](#0-0) 

**Comparison — L1 pool has the guard:** [2](#0-1) 

**Same missing guard in ExternalBridge variant:** [3](#0-2) 

**Oracle rate read that determines output:** [4](#0-3)

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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

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
