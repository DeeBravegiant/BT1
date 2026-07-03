### Title
Zero rsETH Minted for Dust Deposits Causes Permanent Loss of Depositor Funds - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
All L2 pool deposit functions accept any non-zero ETH or token amount but never verify that the computed `rsETHAmount` is greater than zero before proceeding. When a user deposits a dust amount (e.g., 1 wei), integer division in `viewSwapRsETHAmountAndFee` truncates `rsETHAmount` to zero. The user's ETH is permanently absorbed into the pool while they receive nothing in return.

### Finding Description
In `RSETHPoolV3.deposit()`, `RSETHPoolV3ExternalBridge.deposit()`, `RSETHPool.deposit()`, and `RSETHPoolNoWrapper.deposit()`, the only guard against a zero-value deposit is:

```solidity
if (amount == 0) revert InvalidAmount();
```

The swap calculation in `viewSwapRsETHAmountAndFee` uses integer division:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Because rsETH trades at approximately 1.05 ETH (`rsETHToETHrate ≈ 1.05e18`), any deposit where `amountAfterFee < rsETHToETHrate / 1e18 ≈ 1` (i.e., 1 wei with zero fee) produces `rsETHAmount = 0`. The function then calls `wrsETH.mint(msg.sender, 0)` — a no-op — while the deposited ETH is retained in the pool's balance and eventually bridged to L1. No rsETH is issued to the depositor.

The `limitDailyMint` modifier does not block this path: when `rsETHAmount = 0`, the check `dailyMintAmount + 0 > dailyMintLimit` is false, so execution continues normally.

The same truncation applies to token deposits via the overloaded `deposit(address token, uint256 amount, string referralId)` path, where `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` can also round to zero for sufficiently small token amounts.

### Impact Explanation
A depositor who sends 1 wei of ETH (or a dust token amount) receives 0 rsETH. Their funds are permanently transferred to the pool and redistributed to existing rsETH holders through the bridging and L1 deposit flow. The depositor suffers a complete loss of their deposit with no recourse. This matches the **Low** impact category: *"Contract fails to deliver promised returns, but doesn't lose value"* — the protocol retains the value while the individual depositor is silently shortchanged.

### Likelihood Explanation
Any unprivileged user can trigger this by calling `deposit()` with `msg.value = 1`. While accidental dust deposits are unlikely in normal usage, the path is fully reachable with no preconditions. A malicious actor could also exploit this to donate ETH to the pool (inflating rsETH price for existing holders) at negligible cost, or to grief users who copy-paste small test transactions.

### Recommendation
Add an explicit post-computation guard in each `deposit()` function:

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

Alternatively, enforce a protocol-level minimum deposit amount (analogous to `LRTDepositPool.minAmountToDeposit`) in all L2 pool contracts.

### Proof of Concept
1. `rsETHToETHrate = RSETHPoolV3.getRate()` returns `1.05e18` (rsETH ≈ 1.05 ETH).
2. Attacker calls `RSETHPoolV3.deposit{value: 1}("")`.
3. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * feeBps / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`
4. `limitDailyMint` modifier: `0 + 0 > dailyMintLimit` → false, execution continues.
5. `wrsETH.mint(msg.sender, 0)` — no tokens issued.
6. Pool retains 1 wei ETH; depositor receives nothing.

The same path is present in `RSETHPoolV3ExternalBridge.deposit()`, `RSETHPool.deposit()`, and `RSETHPoolNoWrapper.deposit()`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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
