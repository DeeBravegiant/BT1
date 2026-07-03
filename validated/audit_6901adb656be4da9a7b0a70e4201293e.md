### Title
`depositAsset` Mints rsETH Based on Nominal `depositAmount` Without Checking Actual Received Balance - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset` calculates the rsETH amount to mint using the caller-supplied `depositAmount` before the token transfer occurs, and never reconciles against the actual balance received. If a supported LST asset charges a transfer fee, the contract receives fewer tokens than `depositAmount` but mints rsETH as if the full `depositAmount` arrived, over-minting rsETH and diluting all existing holders.

---

### Finding Description

In `depositAsset`, the flow is:

1. `_beforeDeposit(asset, depositAmount, ...)` computes `rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount)` — using the caller-supplied nominal amount.
2. `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` — the actual transfer.
3. `_mintRsETH(rsethAmountToMint)` — mints rsETH based on step 1, not on what was actually received. [1](#0-0) 

`_beforeDeposit` computes the mint amount purely from the nominal `depositAmount`: [2](#0-1) 

No before/after balance snapshot is taken around the `safeTransferFrom` call. The contract never verifies that `balanceOf(address(this))` increased by exactly `depositAmount`. If the token deducts a fee on transfer, the contract is credited with fewer assets than the rsETH it minted represents.

The same pattern exists in `RSETHPoolV3.deposit(address token, ...)`, where `viewSwapRsETHAmountAndFee(amount, token)` is called with the nominal `amount` after `safeTransferFrom`, and `wrsETH.mint` is issued for the full computed amount: [3](#0-2) 

---

### Impact Explanation

rsETH is a share token whose price is derived from `getTotalAssetDeposits / rsETH.totalSupply`. When a depositor uses a fee-on-transfer LST:

- The contract's actual asset balance increases by `depositAmount - fee`.
- rsETH minted corresponds to the full `depositAmount`.
- The rsETH price (`rsETHPrice = totalAssets / totalSupply`) decreases.
- Every existing rsETH holder's redemption value is permanently diluted.

Repeated deposits with a fee-on-transfer token drain value from all existing holders. This constitutes **theft of unclaimed yield** (High) and, at scale, **protocol insolvency** (Critical).

---

### Likelihood Explanation

The `onlySupportedERC20Token` modifier restricts deposits to assets whitelisted by `LRTConfig`. The protocol does not enforce that whitelisted assets are non-fee-on-transfer tokens. If any current or future supported LST introduces a transfer fee (e.g., via an upgrade or a newly added asset), the vulnerability is immediately exploitable by any depositor. Likelihood is **Low** given current assets, but the attack path requires no privilege — any depositor triggers it.

---

### Recommendation

Capture the contract's balance before and after the `safeTransferFrom` and use the delta as the actual deposited amount for both rsETH minting and accounting:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

Apply the same fix to `RSETHPoolV3.deposit(address token, ...)`.

---

### Proof of Concept

1. Assume a supported LST `FeeToken` charges a 1% fee on every transfer.
2. Alice (existing holder) holds rsETH backed by 100 FeeToken already in the protocol. rsETH price = 1.0.
3. Bob calls `depositAsset(FeeToken, 100e18, 0, "")`.
4. `_beforeDeposit` computes `rsethAmountToMint` for 100 FeeToken at price 1.0 → 100 rsETH.
5. `safeTransferFrom` executes; contract receives only 99 FeeToken (1% fee deducted).
6. `_mintRsETH(100 rsETH)` is called — Bob receives 100 rsETH.
7. Protocol now holds 199 FeeToken backing 200 rsETH → rsETH price = 0.995.
8. Alice's 100 rsETH is now worth only 99.5 FeeToken — she lost 0.5 FeeToken of value to Bob.
9. Bob redeems 100 rsETH for 99.5 FeeToken, having deposited only 99 FeeToken net — profit of 0.5 FeeToken at Alice's expense. [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L665-665)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L284-290)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```
