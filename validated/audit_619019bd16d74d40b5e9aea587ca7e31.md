### Title
Missing User-Controlled Slippage Protection in L2 Pool `deposit()` Functions - (File: `contracts/pools/RSETHPoolV3.sol`)

---

### Summary

The `deposit()` functions in the L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV2ExternalBridge`) accept no `minRSETHAmountExpected` parameter, giving depositors zero protection against receiving fewer rsETH tokens than anticipated. The L1 `LRTDepositPool` correctly implements this guard; the L2 pools do not, creating an asymmetric and exploitable gap.

---

### Finding Description

**L1 `LRTDepositPool` — correct implementation:**

`depositETH` and `depositAsset` both accept a `minRSETHAmountExpected` argument and revert if the minted amount falls below it: [1](#0-0) [2](#0-1) 

**L2 `RSETHPoolV3` — missing guard:**

Both `deposit` overloads (ETH and token) accept only `referralId`. There is no `minRSETHAmountExpected` parameter and no minimum-output check before minting: [3](#0-2) [4](#0-3) 

The minted amount is computed entirely from the oracle rate at execution time: [5](#0-4) 

The same pattern is present in `RSETHPoolV2ExternalBridge.deposit()`: [6](#0-5) 

---

### Impact Explanation

If the oracle rate (`rsETHToETHrate`) rises between the moment a user signs a transaction and the moment it is mined — due to a scheduled oracle update, network congestion, or MEV reordering — the user receives fewer rsETH tokens than the rate they observed off-chain. Because there is no on-chain minimum-output check, the transaction succeeds silently and the user has no recourse. The deposited ETH/LST is consumed in full while the rsETH minted is materially less than expected.

**Impact level:** Low — the contract fails to deliver the promised return (rsETH amount the user expected), but the deposited principal is not stolen outright.

---

### Likelihood Explanation

Oracle rates for rsETH are updated periodically by the oracle operator. On any L2 where block times are short and oracle updates are frequent, the window between a user's `eth_call` quote and their transaction landing on-chain is sufficient for the rate to shift. This is a routine, non-adversarial scenario that affects every depositor who relies on the quoted rate. No special attacker capability is required.

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to both `deposit()` overloads in `RSETHPoolV3` (and the equivalent functions in `RSETHPoolV2ExternalBridge`), mirroring the L1 `LRTDepositPool` pattern:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

---

### Proof of Concept

1. Oracle reports `rsETHToETHrate = 1.05e18` (1 ETH = ~0.952 rsETH).
2. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain, sees they will receive `≈0.952 rsETH`, and submits `deposit{value: 1 ether}("ref")`.
3. Before the transaction is mined, the oracle is updated to `rsETHToETHrate = 1.10e18`.
4. The transaction executes: `rsETHAmount = 1e18 * 1e18 / 1.10e18 ≈ 0.909 rsETH` — roughly 4.5% less than expected.
5. No revert occurs; the user silently receives 0.909 rsETH instead of 0.952 rsETH with no on-chain protection. [7](#0-6)

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

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
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
