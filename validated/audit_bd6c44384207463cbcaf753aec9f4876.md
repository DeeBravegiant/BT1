### Title
`RSETHPoolNoWrapper` Assumes Unlimited Pre-Funded rsETH Liquidity With No Rate-Limiting Mechanism — (File: contracts/pools/RSETHPoolNoWrapper.sol)

---

### Summary

`RSETHPoolNoWrapper` is the only pool variant in the LRT-rsETH suite that lacks a daily mint limit. Every other pool (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) enforces a `limitDailyMint` modifier on `deposit`. `RSETHPoolNoWrapper` has no such guard. The pool transfers rsETH from its own pre-funded balance on every deposit and implicitly assumes that balance is always sufficient. Any depositor can exhaust the pool's rsETH inventory in a single block, leaving the pool unable to serve subsequent depositors until the BRIDGER replenishes it from L1.

---

### Finding Description

`RSETHPoolNoWrapper` is deployed on chains where no wrsETH wrapper exists (Arbitrum, Unichain). It holds canonical rsETH OFT tokens pre-funded by the BRIDGER and transfers them directly to depositors:

```solidity
// contracts/pools/RSETHPoolNoWrapper.sol  line 231-243
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    if (!isEthDepositEnabled) revert EthDepositDisabled();
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    rsETH.safeTransfer(msg.sender, rsETHAmount);   // ← transfers from pool balance
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

Compare with `RSETHPoolV3`, which gates every deposit through `limitDailyMint`:

```solidity
// contracts/pools/RSETHPoolV3.sol  line 246-265
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)   // ← rate-limiting guard
{ ... }
```

The `limitDailyMint` modifier tracks `dailyMintAmount` against `dailyMintLimit` and reverts when the cap is reached. `RSETHPoolNoWrapper` has no equivalent. Its only natural ceiling is the pool's rsETH balance, which is finite and replenished asynchronously by the BRIDGER.

The same absence applies to the token deposit path:

```solidity
// contracts/pools/RSETHPoolNoWrapper.sol  line 250-271
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token)
{
    ...
    rsETH.safeTransfer(msg.sender, rsETHAmount);   // ← no daily cap
    ...
}
```

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A depositor who arrives after the pool's rsETH balance has been exhausted receives a revert (the `safeTransfer` fails, rolling back the entire transaction including the ETH/LST transfer). No user funds are permanently lost. However, the pool is unable to fulfil its core promise — accepting deposits and issuing rsETH — until the BRIDGER bridges additional rsETH from L1. During that window the pool is non-functional for all depositors.

---

### Likelihood Explanation

Any holder of sufficient ETH or a supported LST can drain the pool in a single transaction or a small sequence of transactions. The attacker receives rsETH at the fair oracle rate (no financial gain, no financial loss), so the cost of the attack is only gas. The BRIDGER replenishment path requires a cross-chain LayerZero message from L1, introducing a latency window during which the pool remains empty. On chains with low gas costs (Arbitrum, Unichain) the attack is cheap to repeat.

---

### Recommendation

Add the same `dailyMintLimit` / `limitDailyMint` mechanism that exists in `RSETHPoolV3` to `RSETHPoolNoWrapper`. This gives the BRIDGER a guaranteed replenishment window and prevents a single actor from exhausting the pool's rsETH inventory in one block. Alternatively, expose a configurable per-transaction deposit cap that the TIMELOCK_ROLE can adjust.

---

### Proof of Concept

1. BRIDGER pre-funds `RSETHPoolNoWrapper` with `R` rsETH.
2. Attacker calls `deposit{value: R * rsETHToETHrate / 1e18}("")` — one transaction drains the entire rsETH balance.
3. All subsequent `deposit` calls revert at `rsETH.safeTransfer(msg.sender, rsETHAmount)` because `rsETH.balanceOf(pool) == 0`.
4. Legitimate depositors are blocked until the BRIDGER executes a cross-chain replenishment (LayerZero round-trip latency).
5. Attacker can repeat the drain immediately after each replenishment at negligible cost on L2.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-243)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
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
