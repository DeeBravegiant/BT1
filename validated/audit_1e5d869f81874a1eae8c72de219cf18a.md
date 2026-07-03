### Title
stETH Transfer Rounding Causes rsETH Over-Minting on Every Deposit - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset` calculates and mints rsETH based on the user-supplied `depositAmount` before verifying the actual stETH received. Because stETH's share-based accounting delivers 1–2 wei less than requested on every transfer, the protocol systematically over-mints rsETH relative to the stETH it actually holds.

---

### Finding Description

In `LRTDepositPool.depositAsset`, the rsETH mint amount is computed from `depositAmount` (the caller-supplied value) and the transfer is executed afterward:

```solidity
// Line 111 – rsETH calculated from user-supplied depositAmount
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

// Line 114 – actual stETH received may be depositAmount − 1 or − 2 wei
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);

// Line 115 – rsETH minted based on depositAmount, not actual received
_mintRsETH(rsethAmountToMint);
``` [1](#0-0) 

stETH internally tracks balances in shares and rounds down on every `transfer`/`transferFrom`, delivering 1–2 wei less than the requested amount (documented in [Lido's integration guide](https://docs.lido.fi/guides/lido-tokens-integration-guide/#1-2-wei-corner-case)). The contract never checks the balance before and after the transfer to determine the real received amount. As a result, `rsethAmountToMint` is computed from a value that is 1–2 wei larger than what the contract actually receives.

The `_beforeDeposit` path feeds `depositAmount` directly into the oracle-based rsETH calculation:

```solidity
// LRTDepositPool.sol ~line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

Every stETH deposit therefore mints a fractionally larger rsETH amount than the stETH actually deposited justifies.

---

### Impact Explanation

**Low – Contract fails to deliver promised returns.**

Each stETH deposit introduces a 1–2 wei discrepancy between the stETH held and the rsETH minted. Cumulatively across many deposits, the protocol holds slightly less stETH than the rsETH supply implies. When the last stETH withdrawers attempt to redeem, the protocol may be unable to deliver the full stETH amount their rsETH entitles them to, because the actual stETH balance is marginally short. No single user loses a meaningful amount, but the protocol's stETH backing is permanently and irreversibly eroded with every deposit.

---

### Likelihood Explanation

**Medium.** stETH is a first-class supported asset in `LRTDepositPool` (referenced throughout the codebase including `LRTConstants.ST_ETH_TOKEN`, `stakeEthForStETH`, and `LRTConverter`). The rounding occurs on every stETH `transferFrom` call unconditionally. Any unprivileged user depositing stETH triggers this path. [3](#0-2) 

---

### Recommendation

Measure the actual stETH received by comparing balances before and after the transfer, and use the real received amount for rsETH minting:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

This pattern is already used correctly elsewhere in the codebase (e.g., `KernelDepositPool.notifyRewardAmount` checks balance before/after). [4](#0-3) 

---

### Proof of Concept

1. Alice calls `depositAsset(stETH, 1_000_000_000, 0, "")`.
2. `_beforeDeposit` computes `rsethAmountToMint` using `1_000_000_000` as the stETH amount.
3. `safeTransferFrom` executes; due to stETH share rounding, the contract receives `999_999_999` wei of stETH.
4. `_mintRsETH` mints rsETH corresponding to `1_000_000_000` wei of stETH.
5. The protocol now holds 1 wei less stETH than the rsETH it issued implies.
6. Repeated across thousands of deposits, the cumulative shortfall grows, and the final stETH redeemers receive less than their rsETH entitles them to.

### Citations

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L573-577)
```text
        uint256 balanceBefore = rewardsToken.balanceOf(address(this));
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;
```
