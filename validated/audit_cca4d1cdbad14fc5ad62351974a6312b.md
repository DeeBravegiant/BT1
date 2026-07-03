### Title
Fee-on-Transfer Token Mis-accounting in `depositAsset` Causes Protocol Insolvency - (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.depositAsset` computes the rsETH amount to mint from the caller-supplied `depositAmount` **before** the token transfer executes. If a supported LST asset introduces a fee on transfer, the contract receives `depositAmount - fee` but mints rsETH as though it received the full `depositAmount`. Every such deposit creates an unbacked rsETH surplus, progressively making the protocol insolvent. The `removeSupportedAsset` guard in `LRTConfig` further prevents the admin from removing the asset once it has accumulated significant deposits, locking in the damage.

---

### Finding Description

In `LRTDepositPool.depositAsset` the execution order is:

1. **Mint calculation** — `_beforeDeposit` calls `getRsETHAmountToMint(asset, depositAmount)`, which computes rsETH to mint using the full caller-supplied `depositAmount`.
2. **Token transfer** — `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` executes. For a fee-on-transfer token the contract receives only `depositAmount - transferFee`.
3. **Mint** — `_mintRsETH(rsethAmountToMint)` mints rsETH calculated in step 1, i.e., based on the **full** `depositAmount`, not the actual received amount. [1](#0-0) 

`getRsETHAmountToMint` is a pure view function that takes `amount` at face value: [2](#0-1) 

`_beforeDeposit` delegates directly to it with the unverified `depositAmount`: [3](#0-2) 

The compounding factor is `LRTConfig.removeSupportedAsset`, which blocks removal of any asset whose total deposits exceed `maxNegligibleAmount`. Once a fee-on-transfer token has accumulated real deposits, the admin cannot de-list it to stop further damage: [4](#0-3) 

The same pattern is replicated across every L2 pool variant — `RSETHPoolV3.deposit`, `RSETHPoolNoWrapper.deposit`, `RSETHPoolV3ExternalBridge.deposit`, and `RSETHPoolV3WithNativeChainBridge.deposit` — all of which pass the raw `amount` to `viewSwapRsETHAmountAndFee` and mint wrsETH accordingly, while only receiving `amount - fee` from the transfer: [5](#0-4) 

---

### Impact Explanation

**Critical — Protocol insolvency.**

`rsETHPrice` is computed as `totalETHValue / rsETHSupply`. Each deposit with a fee-on-transfer token inflates `rsETHSupply` without a matching increase in `totalETHValue`. The deficit compounds with every deposit. Eventually the protocol cannot honour all rsETH redemptions: later redeemers receive fewer underlying assets than their rsETH entitles them to, constituting direct theft of funds from existing rsETH holders.

---

### Likelihood Explanation

**Low-Medium.**

The currently supported assets (stETH, ETHx, rETH, sfrxETH) do not presently charge transfer fees. However:
- The protocol's `addNewSupportedAsset` path allows any future LST to be whitelisted, including tokens that carry a transfer tax from inception.
- Existing tokens can introduce fees via upgrades (USDT is the canonical precedent).
- The `removeSupportedAsset` guard makes recovery impossible once deposits accumulate, so even a brief window of fee-on-transfer behaviour causes permanent insolvency.

The attack requires no privileged access — any ordinary depositor calling `depositAsset` with a fee-on-transfer token triggers the mis-accounting.

---

### Recommendation

Replace the pre-transfer mint calculation with a balance-delta pattern so that rsETH is always minted against the **actual** amount received:

```solidity
function depositAsset(
    address asset,
    uint256 depositAmount,
    uint256 minRSETHAmountExpected,
    string calldata referralId
) external nonReentrant whenNotPaused onlySupportedERC20Token(asset) {
    if (depositAmount == 0 || depositAmount < minAmountToDeposit) revert InvalidAmountToDeposit();

    uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
    IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
    uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

    uint256 rsethAmountToMint = getRsETHAmountToMint(asset, actualReceived);
    if (rsethAmountToMint < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();

    _mintRsETH(rsethAmountToMint);
    emit AssetDeposit(msg.sender, asset, actualReceived, rsethAmountToMint, referralId);
}
```

Apply the same fix to all L2 pool `deposit(address token, ...)` functions. Additionally, remove the `getTotalAssetDeposits > maxNegligibleAmount` guard from `removeSupportedAsset`, or provide a separate emergency de-listing path that does not require the balance to be zero, so the protocol can halt deposits of a problematic token immediately.

---

### Proof of Concept

1. Admin whitelists asset `X` (e.g., a new LST) via `LRTConfig.addNewSupportedAsset`. At listing time, `X` has no transfer fee.
2. `X`'s governance later activates a 1 % transfer fee.
3. Alice calls `LRTDepositPool.depositAsset(X, 1000e18, minRSETH, "")`.
4. `_beforeDeposit` computes `rsethAmountToMint = getRsETHAmountToMint(X, 1000e18)` — based on the full 1 000 tokens.
5. `safeTransferFrom` executes; the contract receives only **990e18** (1 % fee retained by the token).
6. `_mintRsETH` mints rsETH worth 1 000 tokens for Alice, backed by only 990 tokens.
7. The 10-token deficit is socialised across all rsETH holders via a lower `rsETHPrice`.
8. Admin attempts `LRTConfig.removeSupportedAsset(X, idx)` — reverts with `CannotRemoveAssetWithDeposits` because `getTotalAssetDeposits(X) > maxNegligibleAmount`.
9. Every subsequent deposit of `X` widens the insolvency gap until the protocol cannot cover all redemptions. [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTDepositPool.sol (L506-520)
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
```

**File:** contracts/LRTDepositPool.sol (L665-665)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```

**File:** contracts/LRTConfig.sol (L66-94)
```text
    function removeSupportedAsset(
        address asset,
        uint256 tokenIndex
    )
        external
        onlySupportedAsset(asset)
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(asset);

        if (supportedAssetList[tokenIndex] != asset) {
            revert TokenNotFoundError();
        }

        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }

        delete isSupportedAsset[asset];
        delete assetStrategy[asset];
        depositLimitByAsset[asset] = 0;

        supportedAssetList[tokenIndex] = supportedAssetList[supportedAssetList.length - 1];
        supportedAssetList.pop();

        emit RemovedSupportedAsset(asset);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L284-290)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```
