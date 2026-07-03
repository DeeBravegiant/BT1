Audit Report

## Title
Missing Slippage Protection in RSETHPool User-Facing Deposit Functions - (File: contracts/pools/RSETHPool.sol)

## Summary
Both public `deposit` entry points in `RSETHPool.sol` compute the rsETH output solely from the live oracle rate at execution time and accept no caller-supplied minimum output floor. A user who submits a deposit transaction may have it mined after an oracle rate update, silently receiving fewer `wrsETH` tokens than observed off-chain, with no revert and no recourse. The same protocol already enforces this protection in `LRTDepositPool`.

## Finding Description
`deposit(string memory referralId)` (L265–278) and `deposit(address token, uint256 amount, string memory referralId)` (L284–305) both delegate output computation to `viewSwapRsETHAmountAndFee`, which reads `getRate()` from `rsETHOracle` at execution time (L316–319). Neither function accepts a `minRSETHAmount` parameter, and neither performs any post-computation floor check before transferring `wrsETH` to the caller (L275, L302).

The exploit path requires no attacker:
1. User calls `viewSwapRsETHAmountAndFee` off-chain, observes rate `R`, and submits `deposit{value: 1 ETH}("ref")`.
2. Before the transaction is included, the oracle is updated to a lower rate `R' > R`.
3. `viewSwapRsETHAmountAndFee` computes `rsETHAmount = amountAfterFee * 1e18 / R'`, yielding fewer tokens.
4. `IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount)` executes unconditionally; the user's ETH is consumed and they receive the degraded amount with no revert.

`LRTDepositPool.depositETH` (L76–93) and `_beforeDeposit` (L665–669) demonstrate the protocol's own standard: a `minRSETHAmountExpected` parameter is accepted and enforced with `revert MinimumAmountToReceiveNotMet()`. `RSETHPool` provides no equivalent guard.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but does not lose value.** The user's deposited ETH or token is fully consumed; the contract does not lose funds. However, the user receives fewer `wrsETH` tokens than the rate they observed at submission time, with no mechanism to prevent or revert this outcome. This matches the explicitly allowed Low impact: "Contract fails to deliver promised returns, but doesn't lose value."

## Likelihood Explanation
The `rsETHOracle` rate is updated periodically on-chain. Any oracle update that occurs between a user's transaction submission and its inclusion changes the effective exchange rate. No attacker is required; normal oracle operation is sufficient to trigger the impact. Additionally, MEV searchers can observe a pending oracle update and sandwich a user's deposit, amplifying the rate degradation. This is a standard, well-known pattern on L2 pools. The precondition — an oracle update between submission and inclusion — is a routine occurrence, making this repeatable and realistic.

## Recommendation
Add a `minRSETHAmount` parameter to both `deposit` overloads and revert if the computed output falls below it, mirroring the protection already present in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmount)
    external payable nonReentrant whenNotPaused
{
    if (!isEthDepositEnabled) revert EthDepositDisabled();
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmount) revert SlippageExceeded();
    feeEarnedInETH += fee;
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

Apply the same pattern to the token overload.

## Proof of Concept
**Foundry fork test outline:**
1. Fork Arbitrum mainnet at block `N` where `rsETHOracle.getRate()` returns `1.05e18`.
2. Deploy or reference the live `RSETHPool`.
3. Record `expected = viewSwapRsETHAmountAndFee(1 ether).rsETHAmount` (≈ `0.952e18`).
4. Warp/roll to block `N+1`; simulate an oracle update setting `getRate()` to `1.10e18`.
5. Call `pool.deposit{value: 1 ether}("ref")` from a user EOA.
6. Assert `wrsETH.balanceOf(user) ≈ 0.909e18` — approximately 4.5% less than `expected` — and that no revert occurred.
7. Confirm the same test with `LRTDepositPool.depositETH(0.952e18, "ref")` reverts with `MinimumAmountToReceiveNotMet`, demonstrating the asymmetry. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
